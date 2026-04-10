import { expect, test } from 'bun:test';
import type { CostTracker } from './cost_tracker';
import type { NakedPrediction } from './naked_r1';
import type { ResearchBrief, NewsResult } from './research_runner';
import type { SynthesisResult } from '../prompts/synthesis';
import type { PolymarketMarket } from '../tools/polymarket';

const forecastEvaluatorModule = import('./forecast_evaluator');

const baseMarket: PolymarketMarket = {
  id: 'market-429',
  question: 'Will the retry logic work?',
  category: 'tests',
  volume: 12_345,
  outcomePrices: [0.075, 0.925],
  outcomes: ['Yes', 'No'],
  endDate: '2026-05-01T00:00:00.000Z',
  createdAt: '2026-04-01T00:00:00.000Z',
  active: true,
  closed: false,
  resolutionSource: 'Official rules',
  description: 'Test market',
};

const brief: ResearchBrief = {
  summary: 'Short summary',
  key_facts: ['Fact 1'],
  for_yes: ['For yes'],
  against_yes: ['Against yes'],
  crowd_might_miss: 'Crowd misses this',
  catalyst_type: 'other',
  primary_driver: 'Primary driver',
  second_order_effects: ['Secondary effect'],
  disconfirming_signal: 'Disconfirming signal',
  sources: ['https://example.com'],
  research_cost_usd: 0,
};

const nakedPrediction: NakedPrediction = {
  probability: 13,
  reasoning: 'Baseline is still available.',
  cost_usd: 0,
};

const synthesisPrediction: SynthesisResult = {
  probability: 11,
  confidence: 'medium',
  vs_market: 4,
  reasoning: 'Recovered after retry.',
  key_risk: 'Unexpected reversal.',
  base_rate: 'Base rate text',
};

function rateLimitError(retryAfterSeconds: string): Error & {
  status: number;
  headers: Record<string, string>;
  code: string;
} {
  const error = new Error('rate limit');
  return Object.assign(error, {
    status: 429,
    headers: { 'retry-after': retryAfterSeconds },
    code: 'rate_limit_exceeded',
  });
}

test('runWithRateLimitRetry waits and retries synthesis once', async () => {
  const { runWithRateLimitRetry } = await forecastEvaluatorModule;

  const sleepCalls: number[] = [];
  let attempts = 0;

  const result = await runWithRateLimitRetry(
    async () => {
      attempts += 1;
      if (attempts === 1) {
        throw rateLimitError('1');
      }

      return synthesisPrediction;
    },
    {
      marketId: baseMarket.id,
      sleep: async (ms) => {
        sleepCalls.push(ms);
      },
      log: () => undefined,
      maxAttempts: 2,
    },
  );

  expect(result).toEqual(synthesisPrediction);
  expect(attempts).toBe(2);
  expect(sleepCalls).toEqual([1_000]);
});

test('evaluateMarketPrediction keeps naked result when synthesis keeps hitting 429', async () => {
  const { evaluateMarketPrediction } = await forecastEvaluatorModule;

  const sleepCalls: number[] = [];
  let synthesisAttempts = 0;

  const result = await evaluateMarketPrediction({
    market: baseMarket,
    costTracker: {
      getSessionCost: () => 0,
    } as CostTracker,
    multiSearch: async (): Promise<NewsResult[]> => [],
    nakedPredict: async () => nakedPrediction,
    buildResearchBrief: async () => brief,
    buildResearchBriefText: () => 'Research brief text',
    runSynthesis: async () => {
      synthesisAttempts += 1;
      throw rateLimitError('1');
    },
    log: () => undefined,
    sleep: async (ms) => {
      sleepCalls.push(ms);
    },
  });

  expect(result.naked).toEqual(nakedPrediction);
  expect(result.synthesis).toBeNull();
  expect(result.brief).toEqual(brief);
  expect(result.hadError).toBeTrue();
  expect(synthesisAttempts).toBe(3);
  expect(sleepCalls).toEqual([1_000, 1_000]);
});
