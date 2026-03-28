import { CONFIG } from '../config';
import {
  fetchActiveMarkets,
  marketAgeHours,
} from '../tools/polymarket';
import type { PolymarketMarket } from '../tools/polymarket';

function dedupeMarkets(markets: PolymarketMarket[]): PolymarketMarket[] {
  const seen = new Set<string>();
  const deduped: PolymarketMarket[] = [];

  for (const market of markets) {
    if (seen.has(market.id)) {
      continue;
    }

    seen.add(market.id);
    deduped.push(market);
  }

  return deduped;
}

export async function selectMarkets(): Promise<PolymarketMarket[]> {
  const primaryPool = await fetchActiveMarkets({
    minVolume: 5_000,
    maxVolume: 50_000,
    maxDaysToResolution: 14,
    limit: 100,
  });

  const pool1 = primaryPool.filter((market) => marketAgeHours(market) < 48);
  const pool2 = primaryPool.filter((market) => marketAgeHours(market) >= 48);

  let selected = dedupeMarkets(pool1);

  if (selected.length < 10) {
    selected = dedupeMarkets([...selected, ...pool2]);
  }

  if (selected.length < 10) {
    const expandedPool = await fetchActiveMarkets({
      minVolume: 5_000,
      maxVolume: 100_000,
      maxDaysToResolution: 14,
      limit: 100,
    });
    selected = dedupeMarkets([...selected, ...expandedPool]);
  }

  selected = selected.slice(0, CONFIG.limits.marketsPerRun);

  const nNew = selected.filter((market) => marketAgeHours(market) < 48).length;
  const nEstablished = selected.length - nNew;
  console.log(`Selected ${selected.length} markets: ${nNew} new (<48h), ${nEstablished} established`);

  return selected;
}
