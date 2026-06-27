const GAMMA_API = 'https://gamma-api.polymarket.com';
const BACKOFF_MS = [1000, 3000, 9000] as const;
const ACTIVE_MARKETS_FETCH_LIMIT = 500;

export interface PolymarketMarket {
  id: string;
  question: string;
  category: string;
  volume: number;
  outcomePrices: [number, number];
  outcomes: string[];
  endDate: string;
  createdAt: string;
  active: boolean;
  closed: boolean;
  resolutionSource: string;
  description: string;
  slug?: string;
  conditionId?: string;
  groupSlug?: string;
  eventSlug?: string;
  parentId?: string;
  parentMarketId?: string;
  groupItemTitle?: string;
}

export interface ActiveMarketsParams {
  minVolume: number;
  maxVolume: number;
  maxDaysToResolution: number;
  limit: number;
}

interface GammaMarketRaw {
  id?: string | number;
  question?: string;
  category?: string | null;
  volume?: string | number | null;
  outcomePrices?: string | number[] | string[] | null;
  outcomes?: string | string[] | null;
  endDate?: string | null;
  createdAt?: string | null;
  active?: boolean;
  closed?: boolean;
  resolutionSource?: string | null;
  description?: string | null;
  closedTime?: string | null;
  updatedAt?: string | null;
  slug?: string | null;
  conditionId?: string | null;
  groupSlug?: string | null;
  eventSlug?: string | null;
  parentId?: string | number | null;
  parentMarketId?: string | number | null;
  groupItemTitle?: string | null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseJsonArray(value: unknown): unknown[] {
  if (Array.isArray(value)) {
    return value;
  }

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  return [];
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function toIsoString(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) {
    return null;
  }

  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function normalizeMarket(raw: GammaMarketRaw): PolymarketMarket | null {
  const outcomes = parseJsonArray(raw.outcomes)
    .map((item) => String(item))
    .filter(Boolean);
  const prices = parseJsonArray(raw.outcomePrices)
    .map((item) => toNumber(item))
    .filter((item): item is number => item !== null);

  const id = raw.id !== undefined && raw.id !== null ? String(raw.id) : null;
  const volume = toNumber(raw.volume) ?? 0;
  const endDate = toIsoString(raw.endDate);
  const createdAt = toIsoString(raw.createdAt);

  if (!id || !raw.question || outcomes.length !== 2 || prices.length !== 2 || !endDate || !createdAt) {
    return null;
  }

  return {
    id,
    question: raw.question,
    category: typeof raw.category === 'string' ? raw.category : '',
    volume,
    outcomePrices: [prices[0], prices[1]],
    outcomes,
    endDate,
    createdAt,
    active: Boolean(raw.active),
    closed: Boolean(raw.closed),
    resolutionSource: typeof raw.resolutionSource === 'string' ? raw.resolutionSource : '',
    description: typeof raw.description === 'string' ? raw.description : '',
    slug: typeof raw.slug === 'string' ? raw.slug : undefined,
    conditionId: typeof raw.conditionId === 'string' ? raw.conditionId : undefined,
    groupSlug: typeof raw.groupSlug === 'string' ? raw.groupSlug : undefined,
    eventSlug: typeof raw.eventSlug === 'string' ? raw.eventSlug : undefined,
    parentId: raw.parentId !== undefined && raw.parentId !== null ? String(raw.parentId) : undefined,
    parentMarketId: raw.parentMarketId !== undefined && raw.parentMarketId !== null ? String(raw.parentMarketId) : undefined,
    groupItemTitle: typeof raw.groupItemTitle === 'string' ? raw.groupItemTitle : undefined,
  };
}

async function gammaFetch<T>(pathname: string): Promise<T> {
  let lastError: unknown;

  for (let attempt = 0; attempt < BACKOFF_MS.length; attempt += 1) {
    try {
      const response = await fetch(`${GAMMA_API}${pathname}`, {
        signal: AbortSignal.timeout(20_000),
      });

      if (!response.ok) {
        throw new Error(`Gamma API HTTP ${response.status}: ${response.statusText}`);
      }

      return (await response.json()) as T;
    } catch (error) {
      lastError = error;

      if (attempt < BACKOFF_MS.length - 1) {
        await sleep(BACKOFF_MS[attempt]);
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Gamma API request failed');
}

export function buildActiveMarketsPath(): string {
  return `/markets?active=true&closed=false&limit=${ACTIVE_MARKETS_FETCH_LIMIT}`;
}

function hoursSinceCreated(createdAt: string): number {
  return (Date.now() - new Date(createdAt).getTime()) / 3_600_000;
}

function daysUntilEnd(endDate: string): number {
  return (new Date(endDate).getTime() - Date.now()) / 86_400_000;
}

function resolvedOutcome(raw: GammaMarketRaw): { outcome?: number; resolvedAt?: string } {
  const prices = parseJsonArray(raw.outcomePrices)
    .map((item) => toNumber(item))
    .filter((item): item is number => item !== null);

  if (prices.length !== 2) {
    return {};
  }

  const [yesPrice, noPrice] = prices;
  const resolvedAt = typeof raw.closedTime === 'string'
    ? raw.closedTime
    : typeof raw.updatedAt === 'string'
      ? raw.updatedAt
      : undefined;

  if (yesPrice >= 0.99 && noPrice <= 0.01) {
    return { outcome: 1, resolvedAt };
  }

  if (noPrice >= 0.99 && yesPrice <= 0.01) {
    return { outcome: 0, resolvedAt };
  }

  return { resolvedAt };
}

const GAMMA_PAGE_SIZE = 100;
const GAMMA_MAX_PAGES = 5;

export async function fetchActiveMarkets(params: ActiveMarketsParams): Promise<PolymarketMarket[]> {
  const now = Date.now();
  const allRaw: GammaMarketRaw[] = [];

  for (let offset = 0; offset < GAMMA_MAX_PAGES * GAMMA_PAGE_SIZE; offset += GAMMA_PAGE_SIZE) {
    const page = await gammaFetch<GammaMarketRaw[]>(
      `/markets?active=true&closed=false&limit=${GAMMA_PAGE_SIZE}&offset=${offset}`,
    );
    allRaw.push(...page);
    if (page.length < GAMMA_PAGE_SIZE) {
      break;
    }
  }

  const markets = allRaw
    .map(normalizeMarket)
    .filter((market): market is PolymarketMarket => market !== null)
    .filter((market) => market.outcomes.length === 2)
    .filter((market) => market.volume >= params.minVolume && market.volume <= params.maxVolume)
    .filter((market) => {
      const endTime = new Date(market.endDate).getTime();
      return endTime <= now + params.maxDaysToResolution * 86_400_000;
    })
    .filter((market) => new Date(market.endDate).getTime() > now + 86_400_000)
    .sort((a, b) => {
      const aNew = hoursSinceCreated(a.createdAt) < 48 ? 0 : 1;
      const bNew = hoursSinceCreated(b.createdAt) < 48 ? 0 : 1;

      if (aNew !== bNew) {
        return aNew - bNew;
      }

      return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
    })
    .slice(0, params.limit);

  if (markets.length < 5 && markets.length > 0) {
    console.warn(`Gamma API warning: only ${markets.length} markets matched current filters.`);
  }

  return markets;
}

export async function fetchMarket(id: string): Promise<PolymarketMarket | null> {
  try {
    const raw = await gammaFetch<GammaMarketRaw>(`/markets/${id}`);
    return normalizeMarket(raw);
  } catch (error) {
    if (error instanceof Error && /404/.test(error.message)) {
      return null;
    }

    throw error;
  }
}

export async function checkResolutions(marketIds: string[]): Promise<{
  id: string;
  resolved: boolean;
  outcome?: number;
  resolvedAt?: string;
}[]> {
  const results: {
    id: string;
    resolved: boolean;
    outcome?: number;
    resolvedAt?: string;
  }[] = [];

  for (const marketId of marketIds) {
    try {
      const raw = await gammaFetch<GammaMarketRaw>(`/markets/${marketId}`);
      const outcome = resolvedOutcome(raw);

      if (raw.closed) {
        results.push({
          id: marketId,
          resolved: true,
          outcome: outcome.outcome,
          resolvedAt: outcome.resolvedAt ?? new Date().toISOString(),
        });
      } else {
        results.push({ id: marketId, resolved: false });
      }
    } catch (error) {
      console.error(`Resolution check failed for market ${marketId}:`, error);
      results.push({ id: marketId, resolved: false });
    }
  }

  return results;
}

export function marketPriceFromYesOutcome(market: PolymarketMarket): number {
  return market.outcomePrices[0] * 100;
}

export function marketAgeHours(market: PolymarketMarket): number {
  return hoursSinceCreated(market.createdAt);
}

export function marketDaysRemaining(market: PolymarketMarket): number {
  return daysUntilEnd(market.endDate);
}
