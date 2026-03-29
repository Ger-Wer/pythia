import { CONFIG } from '../config';
import {
  type ActiveMarketsParams,
  fetchActiveMarkets,
  marketAgeHours,
  marketPriceFromYesOutcome,
} from '../tools/polymarket';
import type { PolymarketMarket } from '../tools/polymarket';

interface MarketSelectionStage extends ActiveMarketsParams {
  label: string;
}

interface MarketSelectionPlan {
  newMarketHours: number;
  minimumUniqueEvents: number;
  maxPerEvent: number;
  maxPerCategory: number;
  stages: MarketSelectionStage[];
}

const CANDIDATE_PATTERNS = [
  /^will\s+.+?\s+(win\b.*)$/i,
  /^will\s+.+?\s+(be elected\b.*)$/i,
  /^will\s+.+?\s+(be the next\b.*)$/i,
  /^will\s+.+?\s+(qualify\b.*)$/i,
  /^will\s+.+?\s+(make\b.*)$/i,
  /^will\s+.+?\s+(reach\b.*)$/i,
  /^will\s+.+?\s+(pass\b.*)$/i,
  /^will\s+.+?\s+(sign\b.*)$/i,
  /^will\s+.+?\s+(hit\b.*)$/i,
] as const;

function normalizeText(value: string): string {
  return value
    .toLowerCase()
    .replace(/-/g, ' ')
    .replace(/\?+$/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function explicitEventKey(market: PolymarketMarket): string | null {
  for (const value of [
    market.groupSlug,
    market.eventSlug,
    market.parentMarketId,
    market.parentId,
  ]) {
    if (value && value.trim()) {
      return normalizeText(value);
    }
  }

  return null;
}

function dedupeMarketsById(markets: PolymarketMarket[]): PolymarketMarket[] {
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

function candidateCoreFromText(value: string): string | null {
  const normalized = normalizeText(value);

  for (const pattern of CANDIDATE_PATTERNS) {
    const match = normalized.match(pattern);
    if (match?.[1]) {
      return normalizeText(match[1]);
    }
  }

  return null;
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

    const marketEdge = marketPriceFromYesOutcome(b) - marketPriceFromYesOutcome(a);
    if (marketEdge !== 0) {
      return marketEdge;
    }

    return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
  });
}

export function extractEventKey(market: PolymarketMarket): string {
  const explicit = explicitEventKey(market);
  if (explicit) {
    return explicit;
  }

  for (const source of [market.question, market.slug ?? '']) {
    const core = candidateCoreFromText(source);
    if (core) {
      return core;
    }
  }

  return normalizeText(market.question);
}

export function buildSelectionPlan(): MarketSelectionPlan {
  return {
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
  };
}

export function deduplicateByEvent(markets: PolymarketMarket[]): PolymarketMarket[] {
  const groups = new Map<string, PolymarketMarket[]>();

  for (const market of markets) {
    const key = extractEventKey(market);
    const group = groups.get(key);
    if (group) {
      group.push(market);
    } else {
      groups.set(key, [market]);
    }
  }

  const result: PolymarketMarket[] = [];
  for (const [key, group] of groups.entries()) {
    const selected = [...group].sort((a, b) => {
      const priceDiff = marketPriceFromYesOutcome(b) - marketPriceFromYesOutcome(a);
      if (priceDiff !== 0) {
        return priceDiff;
      }

      return b.volume - a.volume;
    })[0];

    if (group.length > 1) {
      console.log(`Deduplicated event "${key}": ${group.length} sub-markets -> keeping ${selected.id}`);
    }

    result.push(selected);
  }

  return result;
}

export function applyDiversityConstraints(
  markets: PolymarketMarket[],
  maxPerEvent = buildSelectionPlan().maxPerEvent,
  maxPerCategory = buildSelectionPlan().maxPerCategory,
): PolymarketMarket[] {
  const categoryCounts = new Map<string, number>();
  const eventCounts = new Map<string, number>();
  const selected: PolymarketMarket[] = [];

  for (const market of markets) {
    const eventKey = extractEventKey(market);
    const categoryKey = normalizeText(market.category || 'uncategorized');
    const eventCount = eventCounts.get(eventKey) ?? 0;
    const categoryCount = categoryCounts.get(categoryKey) ?? 0;

    if (eventCount >= maxPerEvent || categoryCount >= maxPerCategory) {
      continue;
    }

    selected.push(market);
    eventCounts.set(eventKey, eventCount + 1);
    categoryCounts.set(categoryKey, categoryCount + 1);
  }

  return selected;
}

export async function selectMarkets(): Promise<PolymarketMarket[]> {
  const plan = buildSelectionPlan();
  const requiredUniqueEvents = Math.min(plan.minimumUniqueEvents, CONFIG.limits.marketsPerRun);
  let accumulated: PolymarketMarket[] = [];
  let selected: PolymarketMarket[] = [];

  for (let index = 0; index < plan.stages.length; index += 1) {
    const stage = plan.stages[index];
    const stageMarkets = await fetchActiveMarkets(stage);
    if (stageMarkets.length === 0) {
      console.warn(`Selection stage "${stage.label}" returned 0 markets after filters.`);
    }

    accumulated = dedupeMarketsById([...accumulated, ...stageMarkets]);
    const deduplicated = deduplicateByEvent(accumulated);
    const prioritized = sortByPriority(deduplicated, plan.newMarketHours);
    selected = applyDiversityConstraints(
      prioritized,
      plan.maxPerEvent,
      plan.maxPerCategory,
    ).slice(0, CONFIG.limits.marketsPerRun);

    if (selected.length >= requiredUniqueEvents) {
      break;
    }

    const nextStage = plan.stages[index + 1];
    if (nextStage) {
      console.warn(
        `Only ${selected.length} diversified markets after stage "${stage.label}". Expanding to "${nextStage.label}".`,
      );
    }
  }

  if (selected.length < requiredUniqueEvents) {
    console.warn(`Only ${selected.length} diversified markets selected after all stages.`);
  }

  const nNew = selected.filter((market) => marketAgeHours(market) < plan.newMarketHours).length;
  const nEstablished = selected.length - nNew;
  console.log(`Selected ${selected.length} markets: ${nNew} new (<${plan.newMarketHours}h), ${nEstablished} established`);

  return selected;
}
