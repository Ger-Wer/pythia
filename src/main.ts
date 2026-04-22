import { mkdirSync } from 'node:fs';
import path from 'node:path';
import OpenAI from 'openai';
import { CONFIG } from './config';
import {
  CostLimitError,
  CostTracker,
} from './services/cost_tracker';
import { syncDecisionCoreForForecastRun } from './services/decision_core';
// Phase 1 — Track D: writes fan out through db_shadow when
// DUAL_LOOP_SHADOW_PG=true, otherwise behaviour is identical to ./services/db.
import { initDb, insertPrediction, getTodayCost } from './services/db_shadow';
import type { Database } from './services/db_shadow';
import { evaluateMarketPrediction } from './services/forecast_evaluator';
import {
  buildResearchBrief,
  multiSearch,
  resetSearchWarningCache,
} from './services/research_runner';
import type { ResearchBrief } from './services/research_runner';
import { selectMarkets } from './services/market_selector';
import { nakedR1Predict } from './services/naked_r1';
import {
  buildSynthesisPrompt,
  parseSynthesisResponse,
} from './prompts/synthesis';
import type { SynthesisResult } from './prompts/synthesis';
import {
  marketAgeHours,
  marketDaysRemaining,
  marketPriceFromYesOutcome,
} from './tools/polymarket';
import type { PolymarketMarket } from './tools/polymarket';

function log(message: string): void {
  console.log(`[${new Date().toISOString()}] ${message}`);
}

function escapeMarkdown(value: string): string {
  return value.replace(/([_*\[\]()~`>#+\-=|{}.!])/g, '\\$1');
}

async function sendTelegramAlert(message: string): Promise<void> {
  if (!CONFIG.telegram.botToken || !CONFIG.telegram.chatId) {
    return;
  }

  const url = `https://api.telegram.org/bot${CONFIG.telegram.botToken}/sendMessage`;
  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chat_id: CONFIG.telegram.chatId,
      text: message,
      parse_mode: 'Markdown',
    }),
  }).catch((error) => console.error('Telegram alert failed:', error));
}

async function runSynthesis(
  market: PolymarketMarket,
  researchBrief: string,
  costTracker: CostTracker,
): Promise<SynthesisResult | null> {
  const client = new OpenAI({
    apiKey: CONFIG.deepseek.apiKey,
    baseURL: CONFIG.deepseek.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

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
    messages: [
      {
        role: 'user',
        content: prompt,
      },
    ],
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

function formatMarketLog(market: PolymarketMarket): string {
  return `${market.question} | volume=$${market.volume.toFixed(0)} | market=${marketPriceFromYesOutcome(market).toFixed(1)}% | age=${marketAgeHours(market).toFixed(1)}h`;
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

async function processMarket(
  db: Database,
  market: PolymarketMarket,
  costTracker: CostTracker,
  forecastTimestamp: string,
): Promise<{
  saved: boolean;
  hadError: boolean;
}> {
  const perMarketCostStart = costTracker.getSessionCost();
  const marketPrice = marketPriceFromYesOutcome(market);

  try {
    const {
      brief,
      naked,
      synthesis,
      hadError,
    } = await evaluateMarketPrediction({
      market,
      costTracker,
      multiSearch,
      nakedPredict: nakedR1Predict,
      buildResearchBrief,
      buildResearchBriefText,
      runSynthesis,
      log,
      sleep: async (ms) => {
        await new Promise((resolve) => setTimeout(resolve, ms));
      },
    });

    if (!naked && !synthesis) {
      log(`Skipping market ${market.id}: both baseline and synthesis failed.`);
      return { saved: false, hadError: true };
    }

    insertPrediction(db, {
      market_id: market.id,
      question: market.question,
      category: market.category || null,
      volume: market.volume,
      created_at: market.createdAt,
      end_date: market.endDate,
      market_price: marketPrice,
      dexter_p: synthesis?.probability ?? null,
      dexter_confidence: synthesis?.confidence ?? null,
      dexter_reasoning: synthesis?.reasoning ?? null,
      dexter_key_risk: synthesis?.key_risk ?? null,
      dexter_base_rate: synthesis?.base_rate ?? null,
      naked_p: naked?.probability ?? null,
      naked_reasoning: naked?.reasoning ?? null,
      research_brief_json: brief ? JSON.stringify(brief) : null,
      forecast_timestamp: forecastTimestamp,
      api_cost_usd: costTracker.getSessionCost() - perMarketCostStart,
      is_valid: 1,
    });

    const synthesisEdge = synthesis ? Math.abs(synthesis.probability - marketPrice) : 0;
    const nakedEdge = naked ? Math.abs(naked.probability - marketPrice) : 0;
    if (synthesisEdge > 15 || nakedEdge > 15) {
      await sendTelegramAlert(
        [
          '*Pythia: сильное расхождение с рынком*',
          `Маркет: ${escapeMarkdown(market.question)}`,
          `Рыночная цена: ${marketPrice.toFixed(1)}%`,
          synthesis ? `Research\\+R1: ${synthesis.probability}%` : 'Research\\+R1: N/A',
          naked ? `Naked R1: ${naked.probability}%` : 'Naked R1: N/A',
        ].join('\n'),
      );
    }

    return { saved: true, hadError };
  } catch (error) {
    if (error instanceof CostLimitError) {
      throw error;
    }

    console.error(`Error while processing market ${market.id}:`, error);
    return { saved: false, hadError: true };
  }
}

async function main(): Promise<void> {
  mkdirSync(CONFIG.paths.logDir, { recursive: true });
  mkdirSync(path.dirname(CONFIG.paths.db), { recursive: true });

  const db = initDb();
  const costTracker = new CostTracker(db);
  const startedAt = new Date().toISOString();
  resetSearchWarningCache();

  log(`=== Pythia forecast run started at ${startedAt} ===`);

  const markets = await selectMarkets();
  if (markets.length === 0) {
    await sendTelegramAlert('*Pythia: уведомление*\nНа сегодня не выбрано ни одного маркета. Завершаю работу.');
    process.exit(1);
  }

  for (const market of markets) {
    log(`Selected market: ${formatMarketLog(market)}`);
  }

  // Check hourly burn rate projection
  const burnProjection = costTracker.getHourlyBurnProjection();
  if (burnProjection.isWarning && burnProjection.minutesElapsed > 0) {
    log(
      `⚠️  Cost burn rate alert: Hourly=$${burnProjection.hourlyRate.toFixed(4)}, ` +
      `Projected daily=$${burnProjection.projectedDailyTotal.toFixed(4)} (limit=$${CONFIG.limits.dailyCostUsd})`,
    );
    await sendTelegramAlert(
      `*Pythia: ⚠️  Предупреждение о расходах*\n` +
      `Почасовой темп сжигания: $${burnProjection.hourlyRate.toFixed(4)}\n` +
      `Прогноз за день: $${burnProjection.projectedDailyTotal.toFixed(4)} / $${CONFIG.limits.dailyCostUsd}\n` +
      `Риск превышить лимит. Сокращаю рынки.`,
    );
  }

  let processed = 0;
  let errors = 0;

  for (const market of markets) {
    log(`Processing market ${market.id}: ${formatMarketLog(market)}`);

    try {
      const result = await processMarket(db, market, costTracker, startedAt);
      if (result.saved) {
        processed += 1;
      }
      if (result.hadError) {
        errors += 1;
      }
    } catch (error) {
      if (error instanceof CostLimitError) {
        await sendTelegramAlert(
          `*Pythia: уведомление*\nДостигнут лимит расходов после ${processed} маркетов: $${error.spent.toFixed(4)} / $${error.limit.toFixed(2)}`,
        );
        process.exit(1);
      }

      console.error(`Fatal market error for ${market.id}:`, error);
      errors += 1;
    }
  }

  await syncDecisionCoreForForecastRun(db, startedAt);

  const sessionCost = costTracker.getSessionCost();
  const finalBurnProjection = costTracker.getHourlyBurnProjection();
  log(
    `${processed} markets processed, run cost: $${sessionCost.toFixed(4)}, ` +
    `today total: $${(sessionCost + getTodayCost(db)).toFixed(4)}/${CONFIG.limits.dailyCostUsd}`,
  );

  if (finalBurnProjection.isWarning) {
    await sendTelegramAlert(
      `*Pythia: расходы приближаются к лимиту*\n` +
      `Затрачено сегодня: $${(sessionCost + getTodayCost(db)).toFixed(4)} / $${CONFIG.limits.dailyCostUsd}\n` +
      `На сегодня осталось: ${Math.round(CONFIG.limits.dailyCostUsd - (sessionCost + getTodayCost(db))) * 100}% от лимита`,
    );
  }

  if (errors > 0) {
    await sendTelegramAlert(`*Pythia: уведомление*\nПрогон завершён с ${errors} ошибками на уровне маркетов.`);
  }

  if (processed === 0) {
    await sendTelegramAlert('*Pythia: уведомление*\nВо время прогона не удалось обработать ни один маркет.');
    process.exit(1);
  }
}

main().catch(async (error) => {
  console.error('Pythia run failed:', error);
  const message = error instanceof Error ? error.message : String(error);
  await sendTelegramAlert(`*Pythia: критическая ошибка*\n${escapeMarkdown(message)}`);
  process.exit(1);
});
