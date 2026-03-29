import { expect, test } from 'bun:test';
import type { PolymarketMarket } from '../tools/polymarket';

const marketSelectorModule = import('./market_selector');

function makeMarket(id: string, ageHours: number): PolymarketMarket {
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
  };
}

test('buildSelectionPlan uses the relaxed live-market filters', async () => {
  const { buildSelectionPlan } = await marketSelectorModule;

  expect(buildSelectionPlan()).toEqual({
    newMarketHours: 72,
    primary: {
      minVolume: 1_000,
      maxVolume: 100_000,
      maxDaysToResolution: 21,
      limit: 500,
    },
    expanded: {
      minVolume: 500,
      maxVolume: 250_000,
      maxDaysToResolution: 30,
      limit: 500,
    },
  });
});

test('mergeCandidatePools keeps primary quality but expands when needed', async () => {
  const { mergeCandidatePools } = await marketSelectorModule;

  const primaryNew = makeMarket('primary-new', 12);
  const primaryEstablished = makeMarket('primary-established', 90);
  const expandedNew = makeMarket('expanded-new', 8);
  const expandedDuplicate = makeMarket('primary-new', 6);
  const expandedEstablished = makeMarket('expanded-established', 120);

  const selected = mergeCandidatePools(
    [primaryEstablished, primaryNew],
    [expandedEstablished, expandedDuplicate, expandedNew],
    4,
  );

  expect(selected.map((market: PolymarketMarket) => market.id)).toEqual([
    'primary-new',
    'primary-established',
    'expanded-new',
    'expanded-established',
  ]);
});
