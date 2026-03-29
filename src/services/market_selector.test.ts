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
    minimumUsefulEvents: 5,
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
  expect(extractEventKey(ceasefire)).toBe('will there be a russia ukraine ceasefire by march 31, 2026');
});

test('deriveCategoryKey infers useful fallback categories when Gamma category is empty', async () => {
  const { deriveCategoryKey } = await marketSelectorModule;

  const bundesliga = makeMarket('bundesliga', 12, {
    question: 'Will Bayern Munich win the 2025-26 Bundesliga?',
    slug: 'will-bayern-munich-win-the-202526-bundesliga',
    category: '',
  });
  const ceasefire = makeMarket('ceasefire', 12, {
    question: 'Will there be a Russia-Ukraine ceasefire by March 31, 2026?',
    slug: 'russia-ukraine-ceasefire-by-march-31-2026',
    category: '',
  });
  const bitcoin = makeMarket('bitcoin', 12, {
    question: 'Will Bitcoin hit $200,000 by the end of 2026?',
    slug: 'will-bitcoin-hit-200000-by-the-end-of-2026',
    category: '',
  });

  expect(deriveCategoryKey(bundesliga)).toBe('soccer');
  expect(deriveCategoryKey(ceasefire)).toBe('geopolitics');
  expect(deriveCategoryKey(bitcoin)).toBe('crypto');
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

test('applyDiversityConstraints enforces one market per event and three per derived category', async () => {
  const { applyDiversityConstraints } = await marketSelectorModule;

  const soccerA = makeMarket('soccer-a', 12, {
    question: 'Will Bayern Munich win the 2025-26 Bundesliga?',
    slug: 'will-bayern-munich-win-the-202526-bundesliga',
    category: '',
    outcomePrices: [0.61, 0.39],
  });
  const soccerB = makeMarket('soccer-b', 12, {
    question: 'Will Inter win the 2025-26 Serie A league?',
    slug: 'will-inter-win-the-202526-serie-a-league',
    category: '',
    outcomePrices: [0.41, 0.59],
  });
  const soccerC = makeMarket('soccer-c', 12, {
    question: 'Will PSG win the 2025-26 French Ligue 1?',
    slug: 'will-psg-win-the-202526-french-ligue-1',
    category: '',
    outcomePrices: [0.31, 0.69],
  });
  const soccerD = makeMarket('soccer-d', 12, {
    question: 'Will Sweden qualify for the 2026 FIFA World Cup?',
    slug: 'will-sweden-qualify-for-the-2026-fifa-world-cup',
    category: '',
    outcomePrices: [0.21, 0.79],
  });
  const politics = makeMarket('politics-a', 12, {
    question: 'Will Iván Cepeda Castro win the 2026 Colombian presidential election?',
    slug: 'will-ivan-cepeda-castro-win-the-2026-colombian-presidential-election',
    category: '',
  });

  const selected = applyDiversityConstraints([
    soccerA,
    soccerB,
    soccerC,
    soccerD,
    politics,
  ]);

  expect(selected.map((market: PolymarketMarket) => market.id)).toEqual([
    'soccer-a',
    'soccer-b',
    'soccer-c',
    'politics-a',
  ]);
});

test('prioritizeMarkets prefers newer and more informative prices over near-certain markets', async () => {
  const { prioritizeMarkets } = await marketSelectorModule;

  const nearCertain = makeMarket('certain', 96, {
    question: 'Will Bayern Munich win the 2025-26 Bundesliga?',
    category: '',
    outcomePrices: [0.99, 0.01],
  });
  const informative = makeMarket('informative', 96, {
    question: 'Will there be a Russia-Ukraine ceasefire by March 31, 2026?',
    category: '',
    outcomePrices: [0.54, 0.46],
  });
  const fresh = makeMarket('fresh', 12, {
    question: 'Will Bitcoin hit $200,000 by the end of 2026?',
    category: '',
    outcomePrices: [0.42, 0.58],
  });

  const prioritized = prioritizeMarkets([nearCertain, informative, fresh], 72);
  expect(prioritized.map((market: PolymarketMarket) => market.id)).toEqual([
    'fresh',
    'informative',
    'certain',
  ]);
});
