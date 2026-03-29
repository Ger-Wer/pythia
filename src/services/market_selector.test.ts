import { expect, test } from 'bun:test';
import type { PolymarketMarket } from '../tools/polymarket';

const marketSelectorModule = import('./market_selector');

function makeMarket(
  id: string,
  ageHours: number,
  overrides: Partial<PolymarketMarket> = {},
): PolymarketMarket {
  const now = Date.now();

  return {
    id,
    question: `Market ${id}`,
    category: 'tests',
    volume: 10_000,
    outcomePrices: [0.51, 0.49],
    outcomes: ['Yes', 'No'],
    endDate: new Date(now + 7 * 86_400_000).toISOString(),
    createdAt: new Date(now - ageHours * 3_600_000).toISOString(),
    active: true,
    closed: false,
    resolutionSource: 'test',
    description: 'test market',
    slug: `market-${id}`,
    conditionId: `condition-${id}`,
    groupItemTitle: '',
    ...overrides,
  };
}

test('buildSelectionPlan enforces staged expansion and diversity constraints', async () => {
  const { buildSelectionPlan } = await marketSelectorModule;

  expect(buildSelectionPlan()).toEqual({
    newMarketHours: 72,
    minimumUniqueEvents: 10,
    maxPerEvent: 1,
    maxPerCategory: 3,
    stages: [
      {
        label: 'primary',
        minVolume: 1_000,
        maxVolume: 100_000,
        maxDaysToResolution: 21,
        limit: 500,
      },
      {
        label: 'fallback',
        minVolume: 500,
        maxVolume: 250_000,
        maxDaysToResolution: 30,
        limit: 500,
      },
      {
        label: 'fallback-diverse',
        minVolume: 500,
        maxVolume: 250_000,
        maxDaysToResolution: 60,
        limit: 1_000,
      },
    ],
  });
});

test('extractEventKey uses candidate heuristics only for candidate-style markets', async () => {
  const { extractEventKey } = await marketSelectorModule;

  const mastersCandidate = makeMarket('masters-bubba', 12, {
    question: 'Will Bubba Watson win the 2026 Masters tournament?',
    slug: 'will-bubba-watson-win-the-2026-masters-tournament',
    groupItemTitle: 'Bubba Watson',
  });
  const ceasefire = makeMarket('ceasefire', 12, {
    question: 'Will there be a Russia-Ukraine ceasefire by March 31, 2026?',
    slug: 'russia-ukraine-ceasefire-by-march-31-2026',
  });

  expect(extractEventKey(mastersCandidate)).toBe('win the 2026 masters tournament');
  expect(extractEventKey(ceasefire)).toBe('will there be a russia-ukraine ceasefire by march 31, 2026');
});

test('deduplicateByEvent keeps the most informative sub-market in each event group', async () => {
  const { deduplicateByEvent } = await marketSelectorModule;

  const lowSignal = makeMarket('masters-bubba', 12, {
    question: 'Will Bubba Watson win the 2026 Masters tournament?',
    outcomePrices: [0.02, 0.98],
    slug: 'will-bubba-watson-win-the-2026-masters-tournament',
    groupItemTitle: 'Bubba Watson',
  });
  const highSignal = makeMarket('masters-rory', 12, {
    question: 'Will Rory McIlroy win the 2026 Masters tournament?',
    outcomePrices: [0.24, 0.76],
    slug: 'will-rory-mcilroy-win-the-2026-masters-tournament',
    groupItemTitle: 'Rory McIlroy',
  });
  const independent = makeMarket('ceasefire', 12, {
    question: 'Will there be a Russia-Ukraine ceasefire by March 31, 2026?',
    outcomePrices: [0.55, 0.45],
    slug: 'russia-ukraine-ceasefire-by-march-31-2026',
    category: 'world',
  });

  const selected = deduplicateByEvent([lowSignal, highSignal, independent]);

  expect(selected.map((market: PolymarketMarket) => market.id)).toEqual([
    'masters-rory',
    'ceasefire',
  ]);
});

test('applyDiversityConstraints enforces one market per event and three per category', async () => {
  const { applyDiversityConstraints } = await marketSelectorModule;

  const politicsA = makeMarket('politics-a', 12, {
    question: 'Will the Fed cut rates in June 2026?',
    category: 'politics',
    outcomePrices: [0.61, 0.39],
  });
  const politicsB = makeMarket('politics-b', 12, {
    question: 'Will CPI fall below 3% by June 2026?',
    category: 'politics',
    outcomePrices: [0.41, 0.59],
  });
  const politicsC = makeMarket('politics-c', 12, {
    question: 'Will Congress pass a TikTok ban by June 2026?',
    category: 'politics',
    outcomePrices: [0.31, 0.69],
  });
  const politicsD = makeMarket('politics-d', 12, {
    question: 'Will the US government shut down in 2026?',
    category: 'politics',
    outcomePrices: [0.21, 0.79],
  });
  const sports = makeMarket('sports-a', 12, {
    question: 'Will Team A win the finals?',
    category: 'sports',
  });

  const selected = applyDiversityConstraints([
    politicsA,
    politicsB,
    politicsC,
    politicsD,
    sports,
  ]);

  expect(selected.map((market: PolymarketMarket) => market.id)).toEqual([
    'politics-a',
    'politics-b',
    'politics-c',
    'sports-a',
  ]);
});
