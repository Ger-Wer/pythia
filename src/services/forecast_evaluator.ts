import type { SynthesisResult } from '../prompts/synthesis';
import { marketPriceFromYesOutcome } from '../tools/polymarket';
import type { PolymarketMarket } from '../tools/polymarket';
import type { CostTracker } from './cost_tracker';
import type { NakedPrediction } from './naked_r1';
import type { NewsResult, ResearchBrief } from './research_runner';

export interface EvaluateMarketPredictionArgs {
  market: PolymarketMarket;
  costTracker: CostTracker;
  multiSearch: (question: string, category: string, limitPerQuery?: number) => Promise<NewsResult[]>;
  nakedPredict: (
    market: PolymarketMarket,
    newsSnippets: string[],
    costTracker: CostTracker,
  ) => Promise<NakedPrediction | null>;
  buildResearchBrief: (
    market: PolymarketMarket,
    costTracker: CostTracker,
    newsResults: NewsResult[],
  ) => Promise<ResearchBrief | null>;
  buildResearchBriefText: (brief: ResearchBrief) => string;
  runSynthesis: (
    market: PolymarketMarket,
    researchBrief: string,
    costTracker: CostTracker,
  ) => Promise<SynthesisResult | null>;
  log: (message: string) => void;
  sleep: (ms: number) => Promise<void>;
}

export interface EvaluatedMarketPrediction {
  newsResults: NewsResult[];
  brief: ResearchBrief | null;
  naked: NakedPrediction | null;
  synthesis: SynthesisResult | null;
  hadError: boolean;
}

interface RetryOptions {
  marketId: string;
  sleep: (ms: number) => Promise<void>;
  log: (message: string) => void;
  maxAttempts?: number;
}

function headerValue(headers: unknown, name: string): string | null {
  if (!headers) {
    return null;
  }

  if (
    typeof headers === 'object'
    && headers !== null
    && 'get' in headers
    && typeof (headers as { get?: unknown }).get === 'function'
  ) {
    const value = (headers as { get(name: string): string | null }).get(name);
    return value?.trim() || null;
  }

  if (typeof headers === 'object' && headers !== null) {
    const record = headers as Record<string, unknown>;
    const value = record[name] ?? record[name.toLowerCase()];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }

  return null;
}

function retryDelayMs(error: unknown, attempt: number): number | null {
  if (typeof error !== 'object' || error === null) {
    return null;
  }

  const maybeError = error as {
    status?: unknown;
    code?: unknown;
    headers?: unknown;
  };

  const status = typeof maybeError.status === 'number' ? maybeError.status : null;
  const code = typeof maybeError.code === 'string' ? maybeError.code : null;

  if (status !== 429 && code !== 'rate_limit_exceeded') {
    return null;
  }

  const retryAfter = headerValue(maybeError.headers, 'retry-after');
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds) && seconds > 0) {
      return Math.max(1_000, Math.round(seconds * 1_000));
    }
  }

  return [1_000, 2_000, 4_000][attempt] ?? 4_000;
}

export async function runWithRateLimitRetry<T>(
  operation: () => Promise<T>,
  options: RetryOptions,
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 3;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      const delayMs = retryDelayMs(error, attempt);
      if (delayMs === null || attempt === maxAttempts - 1) {
        throw error;
      }

      options.log(
        `Synthesis rate limit for market ${options.marketId}; retrying in ${delayMs}ms (${attempt + 2}/${maxAttempts})`,
      );
      await options.sleep(delayMs);
    }
  }

  throw new Error(`Unreachable retry state for market ${options.marketId}`);
}

export async function evaluateMarketPrediction(
  args: EvaluateMarketPredictionArgs,
): Promise<EvaluatedMarketPrediction> {
  const marketPrice = marketPriceFromYesOutcome(args.market);
  const newsResults = await args.multiSearch(args.market.question, args.market.category, 3);
  const newsSnippets = newsResults.map(
    (result) => `- ${result.title}: ${result.snippet || result.url}`,
  );

  const naked = await args.nakedPredict(args.market, newsSnippets, args.costTracker);
  if (naked) {
    args.log(`Naked R1: ${naked.probability}% (market: ${marketPrice.toFixed(1)}%)`);
  }

  let brief: ResearchBrief | null = null;
  let synthesis: SynthesisResult | null = null;
  let hadError = false;

  try {
    brief = await args.buildResearchBrief(args.market, args.costTracker, newsResults);
    if (brief) {
      const briefText = args.buildResearchBriefText(brief);
      synthesis = await runWithRateLimitRetry(
        () => args.runSynthesis(args.market, briefText, args.costTracker),
        {
          marketId: args.market.id,
          sleep: args.sleep,
          log: args.log,
        },
      );

      if (synthesis) {
        args.log(`Research+R1: ${synthesis.probability}% (market: ${marketPrice.toFixed(1)}%)`);
      }
    }
  } catch (error) {
    hadError = true;
    console.error(`Research+synthesis degraded for market ${args.market.id}:`, error);
  }

  return {
    newsResults,
    brief,
    naked,
    synthesis,
    hadError,
  };
}
