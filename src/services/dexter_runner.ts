import OpenAI from 'openai';
import { z } from 'zod';
import { CONFIG } from '../config';
import type { PolymarketMarket } from '../tools/polymarket';
import { CostTracker } from './cost_tracker';

export interface DexterBrief {
  summary: string;
  key_facts: string[];
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
      warnSearchOnce('exa-401', 'Research warning: Exa API returned 401. Falling back to Tavily if configured.');
    } else {
      warnSearchOnce('exa-failed', 'Research warning: Exa search failed. Falling back to Tavily if configured.');
    }
  }

  try {
    const tavilyResults = await searchTavily(query, limit);
    if (tavilyResults.length === 0 && !CONFIG.exa.tavilyApiKey) {
      warnSearchOnce('research-off', 'Research warning: no working search provider configured. Continuing in degraded mode.');
    }
    return tavilyResults;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/401|403/i.test(message)) {
      warnSearchOnce('tavily-auth', 'Research warning: Tavily authentication failed. Continuing in degraded mode.');
    } else {
      warnSearchOnce('tavily-failed', 'Research warning: Tavily search failed. Continuing in degraded mode.');
    }
    return [];
  }
}

// TODO: Replace this Phase 1 mini-research fallback with a proper Dexter Agent integration
// once a stable non-interactive programmatic path is validated for this repo/runtime pair.
export async function dexterResearch(
  market: PolymarketMarket,
  costTracker: CostTracker,
): Promise<DexterBrief | null> {
  const searchResults = await searchNews(market.question, 5);
  if (searchResults.length === 0) {
    return null;
  }

  const client = new OpenAI({
    apiKey: CONFIG.deepseek.apiKey,
    baseURL: CONFIG.deepseek.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

  const prompt = `You are preparing a structured research brief for a prediction market.

MARKET: ${market.question}
DESCRIPTION: ${market.description || 'No market description provided.'}
CURRENT IMPLIED PROBABILITY: ${(market.outcomePrices[0] * 100).toFixed(1)}%
DEADLINE: ${market.endDate}

SEARCH RESULTS:
${searchResults
  .map(
    (result, index) =>
      `${index + 1}. ${result.title}\nURL: ${result.url}\nSNIPPET: ${result.snippet || 'No snippet available.'}`,
  )
  .join('\n\n')}

Output JSON only with this schema:
{
  "summary": "<200-500 word summary>",
  "key_facts": ["<3-5 findings>"],
  "sources": ["<URLs used>"]
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
      sources: parsed.sources.map((source) => source.trim()).filter(Boolean),
      research_cost_usd: researchCost,
    };
  } catch (error) {
    console.error(`Failed to parse research response for market ${market.id}:`, error);
    return null;
  }
}
