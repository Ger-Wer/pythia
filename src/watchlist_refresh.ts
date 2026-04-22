import OpenAI from 'openai';
import { z } from 'zod';
import { CONFIG } from './config';
import { CostTracker } from './services/cost_tracker';
import { initDb } from './services/db_shadow';
import type {
  DeepReviewInput,
  DeepReviewResult,
  MonitoringInput,
  MonitoringResult,
  NewsDigest,
  OperatorFacingUpdate,
} from './services/watchlist_engine';
import { runWatchlistRefresh } from './services/watchlist_engine';
import {
  buildResearchBrief,
  multiSearch,
  type NewsResult,
  type ResearchBrief,
} from './services/research_runner';
import {
  buildSynthesisPrompt,
  parseSynthesisResponse,
  type SynthesisResult,
} from './prompts/synthesis';
import {
  fetchMarket,
  marketAgeHours,
  marketDaysRemaining,
  marketPriceFromYesOutcome,
  type PolymarketMarket,
} from './tools/polymarket';

const monitoringSchema = z.object({
  monitoring_status: z.enum(['no_change', 'strengthened', 'weakened', 'review_required', 'broken']),
  thesis_state: z.enum(['intact', 'strengthened', 'weakened', 'review_required', 'broken']),
  status_label: z.string().min(1),
  reason_summary: z.string().min(1),
  action_summary: z.string().min(1),
  should_deep_review: z.boolean(),
  severity: z.enum(['info', 'review', 'warning', 'critical']).optional().default('review'),
});

function log(message: string): void {
  console.log(`[${new Date().toISOString()}] ${message}`);
}

function extractJson(raw: string): string | null {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) {
    return fenced[1].trim();
  }

  const firstBrace = raw.indexOf('{');
  const lastBrace = raw.lastIndexOf('}');
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    return raw.slice(firstBrace, lastBrace + 1).trim();
  }

  return null;
}

function createLlmClient(): OpenAI {
  return new OpenAI({
    apiKey: CONFIG.deepseek.apiKey,
    baseURL: CONFIG.deepseek.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });
}

function compactNewsSummary(results: NewsResult[]): string {
  if (results.length === 0) {
    return 'Новых значимых материалов не найдено.';
  }

  return results
    .slice(0, 3)
    .map((result, index) => `${index + 1}. ${result.title}`)
    .join('\n');
}

function toIsoPlusHours(hours: number, endDate?: string | null): string {
  const candidate = new Date(Date.now() + hours * 60 * 60 * 1000);
  if (endDate) {
    const end = new Date(endDate);
    if (!Number.isNaN(end.getTime()) && end < candidate) {
      return end.toISOString();
    }
  }
  return candidate.toISOString();
}

function classifyThesisState(
  previousProbability: number | null,
  nextProbability: number | null,
  marketPrice: number | null,
): 'intact' | 'strengthened' | 'weakened' | 'review_required' | 'broken' {
  if (nextProbability === null || marketPrice === null) {
    return 'review_required';
  }

  const nextEdge = nextProbability - marketPrice;
  if (Math.abs(nextEdge) < 7) {
    return 'review_required';
  }

  if (previousProbability === null) {
    return 'intact';
  }

  const previousEdge = previousProbability - marketPrice;
  if (Math.abs(previousEdge) >= 7 && Math.sign(previousEdge) !== Math.sign(nextEdge)) {
    return 'broken';
  }

  if (Math.abs(nextEdge) >= Math.abs(previousEdge) + 5) {
    return 'strengthened';
  }

  if (Math.abs(nextEdge) <= Math.max(0, Math.abs(previousEdge) - 5)) {
    return 'weakened';
  }

  return 'intact';
}

function buildMonitoringPrompt(input: MonitoringInput, triggerFlags: Record<string, boolean>): string {
  const previousMarketPrice = input.dossier.current_market_price;
  const currentMarketPrice = input.current.marketPrice;
  const previousProbability = input.dossier.current_research_probability;
  const priceDelta =
    previousMarketPrice !== null && currentMarketPrice !== null
      ? currentMarketPrice - previousMarketPrice
      : null;

  return `Ты — monitoring classifier для Pythia. Нужен короткий операторский вывод по рынку из избранного.

РЫНОК: ${input.dossier.question ?? input.dossier.market_id}
ПРЕДЫДУЩИЙ ТЕЗИС: ${input.dossier.current_thesis_summary ?? 'нет'}
ГЛАВНЫЙ ДРАЙВЕР: ${input.dossier.current_primary_driver ?? 'нет'}
СИГНАЛ ОПРОВЕРЖЕНИЯ: ${input.dossier.current_invalidation_signal ?? 'нет'}
PREVIOUS MARKET PRICE: ${previousMarketPrice ?? 'N/A'}%
CURRENT MARKET PRICE: ${currentMarketPrice ?? 'N/A'}%
PREVIOUS RESEARCH PROBABILITY: ${previousProbability ?? 'N/A'}%
PRICE DELTA: ${priceDelta ?? 'N/A'} п.п.
OPEN POSITION: ${input.dossier.has_open_position === 1 ? 'yes' : 'no'}
NEXT REVIEW DUE AT: ${input.dossier.next_review_due_at ?? 'нет'}
NEWS DIGEST:
${input.newsDigest.summary}

TRIGGER FLAGS:
${JSON.stringify(triggerFlags)}

Ответь СТРОГО JSON:
{
  "monitoring_status": "no_change|strengthened|weakened|review_required|broken",
  "thesis_state": "intact|strengthened|weakened|review_required|broken",
  "status_label": "без изменений|тезис усилился|тезис ослаб|нужен review|thesis break",
  "reason_summary": "одно короткое предложение по-русски",
  "action_summary": "одно короткое действие по-русски",
  "should_deep_review": true,
  "severity": "info|review|warning|critical"
}`;
}

async function monitorWithLlm(
  client: OpenAI,
  input: MonitoringInput,
  triggerFlags: Record<string, boolean>,
): Promise<MonitoringResult | null> {
  const completion = await client.chat.completions.create({
    model: CONFIG.deepseek.modelResearch,
    temperature: 0.1,
    messages: [
      {
        role: 'user',
        content: buildMonitoringPrompt(input, triggerFlags),
      },
    ],
  });

  const raw = completion.choices[0]?.message?.content ?? '';
  const jsonText = extractJson(raw);
  if (!jsonText) {
    return null;
  }

  const parsed = monitoringSchema.parse(JSON.parse(jsonText));
  const operatorFacing =
    parsed.status_label === 'без изменений'
      ? null
      : {
          severity: parsed.severity,
          statusLabel: parsed.status_label,
          reasonSummary: parsed.reason_summary,
          actionSummary: parsed.action_summary,
        };

  return {
    monitoringStatus: parsed.monitoring_status,
    thesisState: parsed.thesis_state,
    statusLabel: parsed.status_label,
    reasonSummary: parsed.reason_summary,
    actionSummary: parsed.action_summary,
    shouldDeepReview: parsed.should_deep_review,
    triggerFlags,
    operatorFacing,
  };
}

export function buildHeuristicMonitoringResult(
  input: MonitoringInput,
  triggerFlags: Record<string, boolean>,
): MonitoringResult {
  const previousMarketPrice = input.dossier.current_market_price;
  const currentMarketPrice = input.current.marketPrice;
  const priceDelta =
    previousMarketPrice !== null && currentMarketPrice !== null
      ? currentMarketPrice - previousMarketPrice
      : 0;
  const dueReview = Boolean(triggerFlags.dueReview);
  const largePriceMove = Boolean(triggerFlags.largePriceMove);
  const hasNews = Boolean(triggerFlags.hasNews);
  const nextProbability = input.current.researchProbability ?? input.current.nakedProbability;
  const previousComparableProbability =
    input.dossier.current_research_probability ?? input.dossier.current_naked_probability;

  if (!dueReview && !largePriceMove && !hasNews) {
    return {
      monitoringStatus: 'no_change',
      thesisState: 'intact',
      statusLabel: 'без изменений',
      reasonSummary: 'Цена и контекст почти не изменились.',
      actionSummary: 'Ничего не делать.',
      shouldDeepReview: false,
      triggerFlags,
      operatorFacing: null,
    };
  }

  const thesisState =
    dueReview
      ? 'review_required'
      : classifyThesisState(previousComparableProbability, nextProbability, currentMarketPrice);

  const normalizedThesisState = thesisState === 'intact' ? 'review_required' : thesisState;

  const statusLabel =
    normalizedThesisState === 'broken'
      ? 'thesis break'
      : normalizedThesisState === 'weakened'
        ? 'тезис ослаб'
        : normalizedThesisState === 'strengthened'
          ? 'тезис усилился'
          : 'нужен review';

  return {
    monitoringStatus: normalizedThesisState === 'review_required' ? 'review_required' : normalizedThesisState,
    thesisState: normalizedThesisState,
    statusLabel,
    reasonSummary:
      dueReview
        ? 'Наступил срок пересмотра тезиса.'
        : `Рынок сдвинулся на ${priceDelta.toFixed(1)} п.п. и требует проверки.`,
    actionSummary: 'Открыть review и обновить тезис.',
    shouldDeepReview: dueReview || largePriceMove || hasNews,
    triggerFlags,
    operatorFacing: {
      severity: normalizedThesisState === 'broken' ? 'critical' : normalizedThesisState === 'weakened' ? 'warning' : 'review',
      statusLabel,
      reasonSummary:
        dueReview
          ? 'Наступил срок пересмотра тезиса.'
          : `Рынок сдвинулся на ${priceDelta.toFixed(1)} п.п. и требует проверки.`,
      actionSummary: 'Открыть review и обновить тезис.',
    },
  };
}

function buildResearchBriefText(brief: ResearchBrief): string {
  return [
    `Сводка: ${brief.summary}`,
    `Ключевые факты:\n- ${brief.key_facts.join('\n- ')}`,
    `Аргументы за YES:\n- ${brief.for_yes.join('\n- ')}`,
    `Аргументы против YES:\n- ${brief.against_yes.join('\n- ')}`,
    `Что толпа может упускать: ${brief.crowd_might_miss}`,
    `Тип катализатора: ${brief.catalyst_type}`,
    `Главный драйвер: ${brief.primary_driver}`,
    `Что должно подтверждать прогноз:\n- ${brief.second_order_effects.length > 0 ? brief.second_order_effects.join('\n- ') : 'Нет данных.'}`,
    `Что быстро опровергнет прогноз: ${brief.disconfirming_signal}`,
  ].join('\n\n');
}

async function runSynthesis(
  client: OpenAI,
  market: PolymarketMarket,
  researchBrief: string,
  costTracker: CostTracker,
): Promise<SynthesisResult | null> {
  const prompt = buildSynthesisPrompt({
    question: market.question,
    resolutionSource: market.resolutionSource || 'Use the market description and official resolution notes.',
    endDate: market.endDate,
    daysRemaining: Math.max(1, Math.ceil(marketDaysRemaining(market))),
    hoursSinceCreation: Math.max(1, Math.round(marketAgeHours(market))),
    marketPrice: Math.round(marketPriceFromYesOutcome(market)),
    volume: Math.round(market.volume),
    researchBrief,
  });

  const completion = await client.chat.completions.create({
    model: CONFIG.deepseek.modelSynthesis,
    temperature: 0.2,
    messages: [{ role: 'user', content: prompt }],
  });

  const raw = completion.choices[0]?.message?.content ?? '';
  const parsed = parseSynthesisResponse(raw);

  if (!parsed) {
    return null;
  }

  costTracker.trackCall(
    CONFIG.deepseek.modelSynthesis,
    completion.usage?.prompt_tokens ?? 0,
    completion.usage?.completion_tokens ?? 0,
  );
  return parsed;
}

function buildDeepReviewUpdate(
  previousProbability: number | null,
  nextProbability: number | null,
  thesisState: DeepReviewResult['thesisState'],
): OperatorFacingUpdate | null {
  if (!thesisState || nextProbability === null) {
    return null;
  }

  const delta =
    previousProbability !== null
      ? nextProbability - previousProbability
      : 0;

  if (Math.abs(delta) < 5 && !['broken', 'review_required'].includes(thesisState)) {
    return null;
  }

  let statusLabel = 'нужен review';
  let severity: OperatorFacingUpdate['severity'] = 'review';
  if (thesisState === 'broken') {
    statusLabel = 'thesis break';
    severity = 'critical';
  } else if (thesisState === 'weakened') {
    statusLabel = 'тезис ослаб';
    severity = 'warning';
  } else if (thesisState === 'strengthened') {
    statusLabel = 'тезис усилился';
    severity = 'info';
  }

  return {
    severity,
    statusLabel,
    reasonSummary:
      previousProbability !== null
        ? `Deep review изменил оценку с ${previousProbability.toFixed(1)}% до ${nextProbability.toFixed(1)}%.`
        : `Deep review выставил новую оценку ${nextProbability.toFixed(1)}%.`,
    actionSummary:
      thesisState === 'broken'
        ? 'Срочно открыть разбор и проверить позицию.'
        : thesisState === 'weakened'
          ? 'Открыть разбор и пересмотреть тезис.'
          : 'Сверить рынок с обновлённым тезисом.',
  };
}

async function main(): Promise<void> {
  const db = initDb();
  const llm = createLlmClient();
  const costTracker = new CostTracker(db);
  const marketCache = new Map<string, PolymarketMarket | null>();

  async function loadMarket(marketId: string): Promise<PolymarketMarket | null> {
    if (marketCache.has(marketId)) {
      return marketCache.get(marketId) ?? null;
    }
    const market = await fetchMarket(marketId);
    marketCache.set(marketId, market);
    return market;
  }

  const summary = await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async (marketId) => {
      const market = await loadMarket(marketId);
      if (!market) {
        throw new Error(`Market ${marketId} not found in Gamma API`);
      }

      return {
        marketPrice: marketPriceFromYesOutcome(market),
        researchProbability: null,
        nakedProbability: null,
      };
    },
    fetchNewsDigest: async ({ marketId, question }) => {
      const market = await loadMarket(marketId);
      const hits = await multiSearch(question ?? market?.question ?? marketId, market?.category ?? '', 2);
      return {
        hits,
        summary: compactNewsSummary(hits),
      };
    },
    monitorThesis: async (input) => {
      const now = Date.now();
      const previousMarketPrice = input.dossier.current_market_price;
      const currentMarketPrice = input.current.marketPrice;
      const priceDelta =
        previousMarketPrice !== null && currentMarketPrice !== null
          ? currentMarketPrice - previousMarketPrice
          : 0;
      const nextReviewDueAt = input.dossier.next_review_due_at
        ? Date.parse(input.dossier.next_review_due_at)
        : Number.NaN;

      const triggerFlags = {
        hasNews: input.newsDigest.hits.length > 0,
        dueReview: Number.isFinite(nextReviewDueAt) && nextReviewDueAt <= now,
        meaningfulPriceMove: Math.abs(priceDelta) >= 5,
        largePriceMove: Math.abs(priceDelta) >= 10,
        openPosition: input.dossier.has_open_position === 1,
      };

      if (!triggerFlags.hasNews && !triggerFlags.dueReview && !triggerFlags.meaningfulPriceMove) {
        return {
          monitoringStatus: 'no_change',
          thesisState: 'intact',
          statusLabel: 'без изменений',
          reasonSummary: 'Цена и контекст почти не изменились.',
          actionSummary: 'Ничего не делать.',
          shouldDeepReview: false,
          triggerFlags,
          operatorFacing: null,
        } satisfies MonitoringResult;
      }

      try {
        const llmResult = await monitorWithLlm(llm, input, triggerFlags);
        if (llmResult) {
          if (triggerFlags.dueReview || triggerFlags.largePriceMove) {
            llmResult.shouldDeepReview = true;
          }
          return llmResult;
        }
      } catch (error) {
        log(`Watch monitoring fallback for ${input.dossier.market_id}: ${error instanceof Error ? error.message : String(error)}`);
      }

      return buildHeuristicMonitoringResult(input, triggerFlags);
    },
    deepReview: async (input: DeepReviewInput) => {
      const market = await loadMarket(input.dossier.market_id);
      if (!market) {
        return {
          summary: input.dossier.current_thesis_summary ?? 'Deep review недоступен: рынок не найден.',
          researchProbability: input.dossier.current_research_probability,
          nakedProbability: input.dossier.current_naked_probability,
          confidence: input.dossier.current_confidence,
          researchStatus: 'DEGRADED',
          thesisState: 'review_required',
          nextReviewDueAt: toIsoPlusHours(24),
          operatorFacing: {
            severity: 'review',
            statusLabel: 'нужен review',
            reasonSummary: 'Не удалось загрузить рынок из Gamma API.',
            actionSummary: 'Повторить deep review позже.',
          },
        };
      }

      const newsResults = await multiSearch(market.question, market.category, 3);
      const brief = await buildResearchBrief(market, costTracker, newsResults);

      if (!brief) {
        return {
          summary: input.dossier.current_thesis_summary ?? 'Deep review не собрал достаточный research.',
          primaryDriver: input.dossier.current_primary_driver,
          invalidationSignal: input.dossier.current_invalidation_signal,
          researchProbability: input.dossier.current_research_probability,
          nakedProbability: input.dossier.current_naked_probability,
          confidence: input.dossier.current_confidence,
          researchStatus: 'DEGRADED',
          thesisState: 'review_required',
          nextReviewDueAt: toIsoPlusHours(24, market.endDate),
          operatorFacing: {
            severity: 'review',
            statusLabel: 'нужен review',
            reasonSummary: 'Deep review не собрал достаточный research и оставил тезис в деградированном режиме.',
            actionSummary: 'Подождать следующего refresh или открыть рынок вручную.',
          },
        };
      }

      const synthesis = await runSynthesis(llm, market, buildResearchBriefText(brief), costTracker);
      const researchProbability = synthesis?.probability ?? input.dossier.current_research_probability;
      const thesisState = classifyThesisState(
        input.dossier.current_research_probability,
        researchProbability,
        input.current.marketPrice,
      );

      return {
        summary: synthesis?.reasoning ?? brief.summary,
        primaryDriver: brief.primary_driver,
        invalidationSignal: brief.disconfirming_signal,
        researchProbability,
        nakedProbability: input.dossier.current_naked_probability,
        confidence: synthesis?.confidence ?? input.dossier.current_confidence,
        researchStatus: 'ON',
        thesisState,
        nextReviewDueAt: toIsoPlusHours(input.dossier.has_open_position === 1 ? 24 : 48, market.endDate),
        operatorFacing: buildDeepReviewUpdate(
          input.dossier.current_research_probability,
          researchProbability,
          thesisState,
        ),
      } satisfies DeepReviewResult;
    },
  });

  log(
    `Watchlist refresh completed: checked=${summary.marketsChecked}, changed=${summary.marketsChanged}, deep_reviews=${summary.deepReviewsTriggered}, alerts=${summary.alertsCreated}`,
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
