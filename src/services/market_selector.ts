import { CONFIG } from '../config';
import {
  type ActiveMarketsParams,
  fetchActiveMarkets,
  marketAgeHours,
} from '../tools/polymarket';
import type { PolymarketMarket } from '../tools/polymarket';

interface MarketSelectionPlan {
  newMarketHours: number;
  primary: ActiveMarketsParams;
  expanded: ActiveMarketsParams;
}

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

function sortByPriority(
  markets: PolymarketMarket[],
  newMarketHours: number,
): PolymarketMarket[] {
  return [...markets].sort((a, b) => {
    const aNew = marketAgeHours(a) < newMarketHours ? 0 : 1;
    const bNew = marketAgeHours(b) < newMarketHours ? 0 : 1;

    if (aNew !== bNew) {
      return aNew - bNew;
    }

    return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
  });
}

export function buildSelectionPlan(): MarketSelectionPlan {
  return {
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
  };
}

export function mergeCandidatePools(
  primaryPool: PolymarketMarket[],
  expandedPool: PolymarketMarket[],
  limit: number,
  newMarketHours = buildSelectionPlan().newMarketHours,
): PolymarketMarket[] {
  const primarySorted = sortByPriority(primaryPool, newMarketHours);
  const expandedSorted = sortByPriority(expandedPool, newMarketHours);

  return dedupeMarkets([
    ...primarySorted,
    ...expandedSorted,
  ]).slice(0, limit);
}

export async function selectMarkets(): Promise<PolymarketMarket[]> {
  const plan = buildSelectionPlan();
  const primaryPool = await fetchActiveMarkets(plan.primary);
  const expansionThreshold = Math.min(10, CONFIG.limits.marketsPerRun);

  let selected = mergeCandidatePools(
    primaryPool,
    [],
    CONFIG.limits.marketsPerRun,
    plan.newMarketHours,
  );

  if (selected.length < expansionThreshold) {
    const expandedPool = await fetchActiveMarkets(plan.expanded);
    selected = mergeCandidatePools(
      primaryPool,
      expandedPool,
      CONFIG.limits.marketsPerRun,
      plan.newMarketHours,
    );
  }

  const nNew = selected.filter((market) => marketAgeHours(market) < plan.newMarketHours).length;
  const nEstablished = selected.length - nNew;
  console.log(`Selected ${selected.length} markets: ${nNew} new (<${plan.newMarketHours}h), ${nEstablished} established`);

  return selected;
}
