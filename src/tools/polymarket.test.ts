import { expect, test } from 'bun:test';

const polymarketModule = import('./polymarket');

test('buildActiveMarketsPath overfetches enough records for downstream filtering', async () => {
  const { buildActiveMarketsPath } = await polymarketModule;

  expect(buildActiveMarketsPath()).toBe('/markets?active=true&closed=false&limit=500');
});
