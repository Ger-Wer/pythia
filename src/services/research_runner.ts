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
  sources: string[];
  research_cost_usd: number;
}

export interface NewsResult {
  title: string;
  url: string;
  snippet: string;
}

const exaResponseSchema = z.object({
  results: z.array(
    z.object({
      title: z.string().optional(),
      url: z.string(),
      text: z.string().optional(),
      highlights: z.array(z.string()).optional(),
    }),
  ),
});

const tavilyResponseSchema = z.object({
  results: z.array(
    z.object({
      title: z.string().optional(),
      url: z.string(),
      content: z.string().optional(),
    }),
  ),
});

const researchSchema = z.object({
  summary: z.string().min(1),
  key_facts: z.array(z.string()).min(1).max(5),
  for_yes: z.array(z.string()).min(1).max(3),
  against_yes: z.array(z.string()).min(1).max(3),
  crowd_might_miss: z.string().min(1),
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

async function searchExa(query: string, limit: number): Promise<NewsResult[]> {
  if (!CONFIG.exa.apiKey) {
    return [];
  }

  const response = await fetchWithRetry(async () => {
    const res = await fetch('https://api.exa.ai/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': CONFIG.exa.apiKey,
      },
      body: JSON.stringify({
        query,
        numResults: limit,
        type: 'auto',
        contents: {
          text: {
            maxCharacters: 700,
          },
          highlights: {
            numSentences: 2,
          },
        },
      }),
      signal: AbortSignal.timeout(20_000),
    });

    if (!res.ok) {
      throw new Error(`Exa HTTP ${res.status}: ${res.statusText}`);
    }

    return exaResponseSchema.parse(await res.json());
  });

  return response.results.map((result) => ({
    title: result.title || result.url,
    url: result.url,
    snippet: (result.highlights?.join(' ') || result.text || '').trim(),
  }));
}

async function searchTavily(query: string, limit: number): Promise<NewsResult[]> {
  if (!CONFIG.exa.tavilyApiKey) {
    return [];
  }

  const response = await fetchWithRetry(async () => {
    const res = await fetch('https://api.tavily.com/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        api_key: CONFIG.exa.tavilyApiKey,
        query,
        max_results: limit,
        search_depth: 'basic',
        include_answer: false,
        include_images: false,
        include_raw_content: false,
      }),
      signal: AbortSignal.timeout(20_000),
    });

    if (!res.ok) {
      throw new Error(`Tavily HTTP ${res.status}: ${res.statusText}`);
    }

    return tavilyResponseSchema.parse(await res.json());
  });

  return response.results.map((result) => ({
    title: result.title || result.url,
    url: result.url,
    snippet: (result.content || '').trim(),
  }));
}

export async function searchNews(query: string, limit: number): Promise<NewsResult[]> {
  try {
    const exaResults = await searchExa(query, limit);
    if (exaResults.length > 0) {
      return exaResults;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/Exa HTTP 401/i.test(message)) {
      warnSearchOnce('exa-401', 'Предупреждение research-слоя: Exa API вернул 401. Переключаюсь на Tavily, если он настроен.');
    } else {
      warnSearchOnce('exa-failed', 'Предупреждение research-слоя: поиск через Exa завершился ошибкой. Переключаюсь на Tavily, если он настроен.');
    }
  }

  try {
    const tavilyResults = await searchTavily(query, limit);
    if (tavilyResults.length === 0 && !CONFIG.exa.tavilyApiKey) {
      warnSearchOnce('research-off', 'Предупреждение research-слоя: не настроен ни один рабочий search provider. Продолжаю в degraded mode.');
    }
    return tavilyResults;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/401|403/i.test(message)) {
      warnSearchOnce('tavily-auth', 'Предупреждение research-слоя: Tavily отклонил ключ. Продолжаю в degraded mode.');
    } else {
      warnSearchOnce('tavily-failed', 'Предупреждение research-слоя: поиск через Tavily завершился ошибкой. Продолжаю в degraded mode.');
    }
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

// Phase 1 research path: multi-search через Exa/Tavily плюс один структурированный LLM summary-call.
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

  const prompt = `Ты — аналитик prediction-рынков. Подготовь краткую исследовательскую справку.

ВОПРОС РЫНКА: ${market.question}
ДЕДЛАЙН: ${market.endDate} (через ${daysRemaining} дней)
ТЕКУЩАЯ ЦЕНА РЫНКА: ${(market.outcomePrices[0] * 100).toFixed(1)}%
(это коллективная оценка вероятности тысячами людей, которые ставят реальные деньги)

НАЙДЕННЫЕ ИСТОЧНИКИ:
${resolvedSearchResults
  .map(
    (result, index) =>
      `${index + 1}. ${result.title}\nURL: ${result.url}\nSNIPPET: ${result.snippet || 'No snippet available.'}`,
  )
  .join('\n\n')}

ЗАДАЧА:
1. Выдели 3-5 ключевых фактов, влияющих на исход
2. Какие факты ЗА исход "Да", какие ПРОТИВ
3. Что толпа может недооценивать или переоценивать?
4. Ближайший исторический прецедент

Пиши кратко, на русском. Максимум 200 слов.

Ответь JSON:
{
  "summary": "краткая справка",
  "key_facts": ["факт 1", "факт 2", "факт 3"],
  "for_yes": ["аргумент за 1", "аргумент за 2"],
  "against_yes": ["аргумент против 1", "аргумент против 2"],
  "crowd_might_miss": "что толпа может упускать",
  "sources": ["url1", "url2"]
}`;

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
    const parsed = researchSchema.parse(JSON.parse(jsonText));
    const researchCost = costTracker.trackCall(
      CONFIG.deepseek.modelResearch,
      completion.usage?.prompt_tokens ?? 0,
      completion.usage?.completion_tokens ?? 0,
    );

    return {
      summary: parsed.summary.trim(),
      key_facts: parsed.key_facts.map((fact) => fact.trim()).filter(Boolean).slice(0, 5),
      for_yes: parsed.for_yes.map((fact) => fact.trim()).filter(Boolean).slice(0, 3),
      against_yes: parsed.against_yes.map((fact) => fact.trim()).filter(Boolean).slice(0, 3),
      crowd_might_miss: parsed.crowd_might_miss.trim(),
      sources: parsed.sources.map((source) => source.trim()).filter(Boolean),
      research_cost_usd: researchCost,
    };
  } catch (error) {
    console.error(`Failed to parse research response for market ${market.id}:`, error);
    return null;
  }
}
