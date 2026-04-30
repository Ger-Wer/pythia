import { expect, test } from 'bun:test';

import { WatchlistPublisher } from './watchlist_publisher';

test('buildUpsert carries explicit marketSpecificKey into the protobuf market identity', async () => {
  const publisher = new WatchlistPublisher({ enabled: false });

  const upsert = await publisher.buildUpsert({
    marketId: 'market-explicit-key',
    ctfAddress: '',
    recommendedAction: 'enter_yes',
    pFundamental: 0.67,
    pMarket: 0.61,
    confidence: 0.8,
    version: 7,
    marketSpecificKey:
      '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  } as any);

  expect(upsert.market?.marketSpecificKey).toBe(
    '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  );
});

test('buildUpsert resolves marketSpecificKey from market metadata when caller does not provide one', async () => {
  const publisher = new WatchlistPublisher({
    enabled: false,
    lookupMarket: async (marketId: string) => ({
      id: marketId,
      question: 'Will test market resolve?',
      category: 'tests',
      volume: 10_000,
      outcomePrices: [0.52, 0.48],
      outcomes: ['Yes', 'No'],
      endDate: '2026-06-01T00:00:00.000Z',
      createdAt: '2026-04-01T00:00:00.000Z',
      active: true,
      closed: false,
      resolutionSource: 'test',
      description: 'test market',
      conditionId:
        '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    }),
  } as any);

  const upsert = await publisher.buildUpsert({
    marketId: 'market-derived-key',
    ctfAddress: '',
    recommendedAction: 'enter_no',
    pFundamental: 0.33,
    pMarket: 0.41,
    confidence: 0.7,
    version: 8,
  });

  expect(upsert.market?.marketSpecificKey).toBe(
    '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  );
});
