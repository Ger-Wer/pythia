import OpenAI from 'openai';
import { z } from 'zod';
import { CONFIG } from '../config';
import type { PolymarketMarket } from '../tools/polymarket';
import { CostTracker } from './cost_tracker';

export interface ResearchBrief {
  summary: string;
  key_facts: string[];
  for_yes: string[];
  against_yes: string[];
  crowd_might_miss: string;
  catalyst_type:
    | 'polling'
    | 'regulatory'
    | 'macro'
    | 'geopolitics'
    | 'sports_form'
    | 'injury_or_roster'
    | 'award_cycle'
    | 'crypto'
    | 'corporate'
    | 'other';
  primary_driver: string;
  second_order_effects: string[];
  disconfirming_signal: string;
  sources: string[];
  research_cost_usd: number;
}

export interface NewsResult {
  title: string;
  url: string;
  snippet: string;
}

const braveResponseSchema = z.object({
  web: z.object({
    results: z.array(
      z.object({
        title: z.string().optional(),
        url: z.string(),
        description: z.string().optional(),
      }),
    ),
  }).optional(),
});

const searxngResponseSchema = z.object({
  results: z.array(
    z.object({
      title: z.string().optional(),
      url: z.string(),
      content: z.string().optional(),
    }),
  ),
});

const ddgResponseSchema = z.object({
  Heading: z.string().optional(),
  AbstractText: z.string().optional(),
  AbstractURL: z.string().optional(),
  RelatedTopics: z.array(
    z.object({
      Text: z.string().optional(),
      FirstURL: z.string().optional(),
    }),
  ).optional(),
});

const researchSchema = z.object({
  summary: z.string().min(1),
  key_facts: z.array(z.string()).min(1).max(5),
  for_yes: z.array(z.string()).min(1).max(3),
  against_yes: z.array(z.string()).min(1).max(3),
  crowd_might_miss: z.string().min(1),
  catalyst_type: z.enum([
    'polling',
    'regulatory',
    'macro',
    'geopolitics',
    'sports_form',
    'injury_or_roster',
    'award_cycle',
    'crypto',
    'corporate',
    'other',
  ]).optional().default('other'),
  primary_driver: z.string().optional().default('Нет данных.'),
  second_order_effects: z.array(z.string()).optional().default([]),
  disconfirming_signal: z.string().optional().default('Нет данных.'),
  sources: z.array(z.string()).min(1),
});
const emittedSearchWarnings = new Set<string>();

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function warnSearchOnce(key: string, message: string): void {
  if (emittedSearchWarnings.has(key)) {
    return;
  }

  emittedSearchWarnings.add(key);
  console.warn(message);
}

export function resetSearchWarningCache(): void {
  emittedSearchWarnings.clear();
}

async function fetchWithRetry<T>(request: () => Promise<T>): Promise<T> {
  const backoff = [1_000, 3_000, 9_000] as const;
  let lastError: unknown;

  for (let attempt = 0; attempt < backoff.length; attempt += 1) {
    try {
      return await request();
    } catch (error) {
      lastError = error;
      if (attempt < backoff.length - 1) {
        await sleep(backoff[attempt]);
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Request failed');
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

function normalizeSearchFragment(value: string): string {
  return value
    .replace(/^Will\s+/i, '')
    .replace(/\?+$/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractContext(question: string): string {
  const normalized = normalizeSearchFragment(question);
  const candidate = normalized
    .split(/\b(win|lose|finish|hit|reach|pass|sign|qualify|be elected|be the next|make)\b/i)
    .slice(-1)[0]
    ?.trim();

  if (candidate && candidate.length >= 8) {
    return candidate;
  }

  return normalized;
}

export function buildSearchQueries(question: string, category?: string): string[] {
  const normalizedQuestion = normalizeSearchFragment(question);
  const context = extractContext(question);
  const normalizedCategory = (category || '').trim();

  return [
    `${normalizedQuestion} latest news facts 2026`,
    `${context}${normalizedCategory ? ` ${normalizedCategory}` : ''} prediction odds forecast 2026`,
  ];
}

export function buildResearchPrompt(market: PolymarketMarket, searchResults: NewsResult[]): string {
  const daysRemaining = Math.max(
    1,
    Math.ceil((new Date(market.endDate).getTime() - Date.now()) / 86_400_000),
  );

  return `Ты — аналитик prediction markets. Подготовь краткую исследовательскую справку.

ВОПРОС РЫНКА: ${market.question}
ДЕДЛАЙН: ${market.endDate} (через ${daysRemaining} дней)
РЫНОЧНАЯ ЦЕНА: ${(market.outcomePrices[0] * 100).toFixed(1)}%
КАТЕГОРИЯ: ${market.category || 'не указана'}

ИСТОЧНИКИ:
${searchResults
  .map(
    (result, index) =>
      `${index + 1}. ${result.title}\nURL: ${result.url}\nSNIPPET: ${result.snippet || 'No snippet available.'}`,
  )
  .join('\n\n')}

ЗАДАЧА:
1. Выдели 3-5 ключевых фактов
2. Дай 2 аргумента за YES
3. Дай 2 аргумента против YES
4. Что толпа может недооценивать?
5. Определи тип катализатора
6. Назови главный драйвер вероятности
7. Назови 1-3 вторичных сигнала, которые должны подтверждать тезис
8. Назови один сигнал, который быстро опровергнет прогноз

Ответ строго JSON:
{
  "summary": "...",
  "key_facts": ["...", "..."],
  "for_yes": ["...", "..."],
  "against_yes": ["...", "..."],
  "crowd_might_miss": "...",
  "catalyst_type": "polling|regulatory|macro|geopolitics|sports_form|injury_or_roster|award_cycle|crypto|corporate|other",
  "primary_driver": "...",
  "second_order_effects": ["...", "..."],
  "disconfirming_signal": "...",
  "sources": ["..."]
}`;
}

export function parseResearchResponse(raw: string): Omit<ResearchBrief, 'research_cost_usd'> | null {
  const jsonText = extractJson(raw);

  if (!jsonText) {
    return null;
  }

  try {
    const parsed = researchSchema.parse(JSON.parse(jsonText));
    return {
      summary: parsed.summary.trim(),
      key_facts: parsed.key_facts.map((fact) => fact.trim()).filter(Boolean).slice(0, 5),
      for_yes: parsed.for_yes.map((fact) => fact.trim()).filter(Boolean).slice(0, 3),
      against_yes: parsed.against_yes.map((fact) => fact.trim()).filter(Boolean).slice(0, 3),
      crowd_might_miss: parsed.crowd_might_miss.trim(),
      catalyst_type: parsed.catalyst_type,
      primary_driver: parsed.primary_driver.trim() || 'Нет данных.',
      second_order_effects: parsed.second_order_effects
        .map((item) => item.trim())
        .filter(Boolean)
        .slice(0, 3),
      disconfirming_signal: parsed.disconfirming_signal.trim() || 'Нет данных.',
      sources: parsed.sources.map((source) => source.trim()).filter(Boolean),
    };
  } catch (error) {
    console.error('Failed to parse research response:', error);
    return null;
  }
}

export function deduplicateNewsResults(results: NewsResult[]): NewsResult[] {
  const seenUrls = new Set<string>();
  const deduplicated: NewsResult[] = [];

  for (const result of results) {
    const normalizedUrl = result.url.trim();
    if (!normalizedUrl || seenUrls.has(normalizedUrl)) {
      continue;
    }

    seenUrls.add(normalizedUrl);
    deduplicated.push(result);
  }

  return deduplicated;
}

async function searchBrave(query: string, limit: number): Promise<NewsResult[]> {
  if (!CONFIG.search.braveApiKey) {
    return [];
  }

  const response = await fetchWithRetry(async () => {
    const res = await fetch(
      `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${limit}`,
      {
        headers: {
          Accept: 'application/json',
          'X-Subscription-Token': CONFIG.search.braveApiKey,
        },
        signal: AbortSignal.timeout(20_000),
      },
    );

    if (!res.ok) {
      throw new Error(`Brave HTTP ${res.status}: ${res.statusText}`);
    }

    return braveResponseSchema.parse(await res.json());
  });

  return (response.web?.results ?? []).map((result) => ({
    title: result.title || result.url,
    url: result.url,
    snippet: (result.description || '').trim(),
  }));
}

async function searchSearXNG(query: string, limit: number): Promise<NewsResult[]> {
  if (!CONFIG.search.searxngBaseUrl) {
    return [];
  }

  const response = await fetchWithRetry(async () => {
    const res = await fetch(`${CONFIG.search.searxngBaseUrl}/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({ q: query, format: 'json' }),
      signal: AbortSignal.timeout(20_000),
    });

    if (!res.ok) {
      throw new Error(`SearXNG HTTP ${res.status}: ${res.statusText}`);
    }

    return searxngResponseSchema.parse(await res.json());
  });

  return response.results.slice(0, limit).map((result) => ({
    title: result.title || result.url,
    url: result.url,
    snippet: (result.content || '').trim(),
  }));
}

async function searchDuckDuckGo(query: string, limit: number): Promise<NewsResult[]> {
  const response = await fetchWithRetry(async () => {
    const res = await fetch(
      `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1`,
      {
        signal: AbortSignal.timeout(15_000),
      },
    );

    if (!res.ok) {
      throw new Error(`DuckDuckGo HTTP ${res.status}: ${res.statusText}`);
    }

    return ddgResponseSchema.parse(await res.json());
  });

  const results: NewsResult[] = [];

  if (response.AbstractText && response.AbstractURL) {
    results.push({
      title: response.Heading || response.AbstractURL,
      url: response.AbstractURL,
      snippet: response.AbstractText.trim(),
    });
  }

  for (const topic of response.RelatedTopics ?? []) {
    if (topic.Text && topic.FirstURL) {
      results.push({
        title: topic.Text.slice(0, 80),
        url: topic.FirstURL,
        snippet: topic.Text.trim(),
      });
    }
  }

  return results.slice(0, limit);
}

export async function searchNews(query: string, limit: number): Promise<NewsResult[]> {
  try {
    const braveResults = await searchBrave(query, limit);
    if (braveResults.length > 0) {
      return braveResults;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/401|403/i.test(message)) {
      warnSearchOnce('brave-auth', 'Предупреждение research-слоя: Brave Search отклонил ключ. Переключаюсь на SearXNG, если он настроен.');
    } else {
      warnSearchOnce('brave-failed', 'Предупреждение research-слоя: поиск через Brave завершился ошибкой. Переключаюсь на SearXNG, если он настроен.');
    }
  }

  try {
    const searxngResults = await searchSearXNG(query, limit);
    if (searxngResults.length > 0) {
      return searxngResults;
    }
    if (!CONFIG.search.braveApiKey && !CONFIG.search.searxngBaseUrl) {
      warnSearchOnce('search-off', 'Предупреждение research-слоя: не настроен ни один search provider. Использую DuckDuckGo emergency fallback.');
    }
  } catch (error) {
    warnSearchOnce('searxng-failed', 'Предупреждение research-слоя: поиск через SearXNG завершился ошибкой. Использую DuckDuckGo emergency fallback.');
  }

  try {
    return await searchDuckDuckGo(query, limit);
  } catch (error) {
    warnSearchOnce('ddg-failed', 'Предупреждение research-слоя: DuckDuckGo fallback завершился ошибкой. Продолжаю в degraded mode.');
    return [];
  }
}

export async function multiSearch(
  question: string,
  category: string,
  limitPerQuery = 3,
): Promise<NewsResult[]> {
  const aggregated: NewsResult[] = [];

  for (const query of buildSearchQueries(question, category)) {
    const results = await searchNews(query, limitPerQuery);
    aggregated.push(...results);
  }

  return deduplicateNewsResults(aggregated).slice(0, 8);
}

// Phase 1 research path: multi-search через Brave/SearXNG/DuckDuckGo плюс один структурированный LLM summary-call.
// Это production research-контур Pythia, он не зависит от Dexter framework.
export async function buildResearchBrief(
  market: PolymarketMarket,
  costTracker: CostTracker,
  searchResults?: NewsResult[],
): Promise<ResearchBrief | null> {
  const resolvedSearchResults = searchResults ?? await multiSearch(market.question, market.category, 3);
  if (resolvedSearchResults.length === 0) {
    return null;
  }

  const client = new OpenAI({
    apiKey: CONFIG.deepseek.apiKey,
    baseURL: CONFIG.deepseek.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

  const daysRemaining = Math.max(
    1,
    Math.ceil((new Date(market.endDate).getTime() - Date.now()) / 86_400_000),
  );
  void daysRemaining;
  const prompt = buildResearchPrompt(market, resolvedSearchResults);

  const completion = await client.chat.completions.create({
    model: CONFIG.deepseek.modelResearch,
    temperature: 0.2,
    messages: [
      {
        role: 'user',
        content: prompt,
      },
    ],
  });

  const raw = completion.choices[0]?.message?.content ?? '';
  const jsonText = extractJson(raw);

  if (!jsonText) {
    console.error(`Failed to extract research JSON for market ${market.id}`);
    return null;
  }

  try {
    const parsed = parseResearchResponse(jsonText);
    if (!parsed) {
      console.error(`Failed to parse research response for market ${market.id}`);
      return null;
    }
    const researchCost = costTracker.trackCall(
      CONFIG.deepseek.modelResearch,
      completion.usage?.prompt_tokens ?? 0,
      completion.usage?.completion_tokens ?? 0,
    );

    return {
      ...parsed,
      research_cost_usd: researchCost,
    };
  } catch (error) {
    console.error(`Failed to parse research response for market ${market.id}:`, error);
    return null;
  }
}
