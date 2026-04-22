/**
 * Pythia → sniper-ingest watchlist publisher.
 *
 * Hooks into `decision_core` after a DB commit and forwards relevant
 * decisions to the Dual-Loop bus as `WatchlistUpsert` messages. Controlled
 * by the `DUAL_LOOP_BUS_ENABLED` env var (default: `false`). When the flag
 * is off every call is a no-op — the existing Pythia pipeline is untouched.
 *
 * Not on the hot path: publishing happens from a post-commit async
 * post-processor, never inline with SQLite writes.
 */

import { randomUUID } from 'node:crypto';

import { StreamPublisher } from './bus/redis_streams';
import { WATCHLIST_UPSERT, WATCHLIST_REMOVE } from './bus/topics';
// NOTE: proto types are imported *lazily* below so that any file which
// transitively imports this module (via `decision_core` → `db.ts`) can still
// load when the ts-proto output in `src/generated/` has not yet been built.
// Static imports would turn a missing `bun run proto:gen` into a hard parse
// error for every consumer, including CI jobs that only run Track D tests.
import type { Side as SideType } from '../generated/dual_loop/v1/common';
import type {
  WatchlistRemove as WatchlistRemoveType,
  WatchlistUpsert as WatchlistUpsertType,
} from '../generated/dual_loop/v1/watchlist';

type ProtoBundle = {
  Side: typeof import('../generated/dual_loop/v1/common').Side;
  WatchlistUpsert: typeof import('../generated/dual_loop/v1/watchlist').WatchlistUpsert;
  WatchlistRemove: typeof import('../generated/dual_loop/v1/watchlist').WatchlistRemove;
};

let protoCache: Promise<ProtoBundle> | null = null;
async function loadProto(): Promise<ProtoBundle> {
  if (!protoCache) {
    protoCache = (async () => {
      const [common, watchlist] = await Promise.all([
        import('../generated/dual_loop/v1/common'),
        import('../generated/dual_loop/v1/watchlist'),
      ]);
      return {
        Side: common.Side,
        WatchlistUpsert: watchlist.WatchlistUpsert,
        WatchlistRemove: watchlist.WatchlistRemove,
      };
    })();
  }
  return protoCache;
}

/**
 * Slim shape of a decision-core signal needed for publishing. Kept narrow so
 * callers don't have to pull the full `SignalSnapshot` interface from
 * `decision_core.ts`.
 */
export interface WatchlistDecisionInput {
  marketId: string;
  /** CTF contract address (may be missing in SQLite; pass empty string). */
  ctfAddress?: string;
  /** One of the decision-core actions. */
  recommendedAction: 'watch' | 'review' | 'enter_yes' | 'enter_no';
  /** Internal probability estimate, 0..1. */
  pFundamental: number;
  /** Market-implied probability at signal time, 0..1. If unknown, pass null. */
  pMarket: number | null;
  /** Numeric confidence, 0..1. If unknown, pass null — defaults to 0.5. */
  confidence?: number | null;
  /** Monotonic per-market version (signal id or update index). */
  version: number;
  /** Max suggested position in USD (micros applied internally). */
  maxPositionUsd?: number;
  /** Max slippage in basis points. Defaults to 50 (0.5%). */
  maxSlippageBps?: number;
  /** TTL for the watchlist entry, ms. Defaults to 6h. */
  ttlMs?: number;
  /** Correlation trace id for structured logs. */
  traceId?: string;
}

export interface WatchlistPublisherConfig {
  /** Master flag. When false, all publish calls no-op. */
  enabled: boolean;
  /** Redis URL, e.g. `redis://127.0.0.1:6379`. */
  redisUrl?: string;
  /** Optional preconstructed publisher (for tests). */
  publisher?: StreamPublisher;
  /** Optional structured logger. */
  log?: (event: Record<string, unknown>) => void;
}

/** Env-var-driven defaults. */
export function configFromEnv(env: NodeJS.ProcessEnv = process.env): WatchlistPublisherConfig {
  return {
    enabled: env.DUAL_LOOP_BUS_ENABLED === 'true' || env.DUAL_LOOP_BUS_ENABLED === '1',
    redisUrl: env.DUAL_LOOP_REDIS_URL ?? env.REDIS_URL ?? 'redis://127.0.0.1:6379',
  };
}

const DEFAULT_TTL_MS = 6 * 60 * 60 * 1000;

function structuredLog(event: Record<string, unknown>) {
  console.log(JSON.stringify({ level: 'info', component: 'watchlist_publisher', ...event }));
}

/** Map decision-core action → ACCEPT / SNOOZE / skip. */
export function classifyDecision(
  action: WatchlistDecisionInput['recommendedAction'],
): 'accept' | 'snooze' | 'skip' {
  switch (action) {
    case 'enter_yes':
    case 'enter_no':
      return 'accept';
    case 'review':
      return 'snooze';
    case 'watch':
    default:
      return 'skip';
  }
}

function sideHintFor(
  action: WatchlistDecisionInput['recommendedAction'],
  Side: ProtoBundle['Side'],
): SideType {
  switch (action) {
    case 'enter_yes':
      return Side.SIDE_YES;
    case 'enter_no':
      return Side.SIDE_NO;
    default:
      return Side.SIDE_UNSPECIFIED;
  }
}

function uuidBytes(): Buffer {
  // Node's randomUUID gives v4; for Dual-Loop's dedup cache any 128-bit value
  // is fine. Switching to UUIDv7 is deferred until we depend on a library.
  return Buffer.from(randomUUID().replace(/-/g, ''), 'hex');
}

/** 1.0 probability = 1_000_000 ppm (matches common.proto `FixedPointScales`). */
const PROBABILITY_PPM_PER_UNIT = 1_000_000;

/** Convert a unit-interval probability (0..1) to signed int64 ppm. */
function probToPpm(p: number): bigint {
  const scaled = Math.round(p * PROBABILITY_PPM_PER_UNIT);
  const clamped = Math.max(
    -PROBABILITY_PPM_PER_UNIT,
    Math.min(PROBABILITY_PPM_PER_UNIT, scaled),
  );
  return BigInt(clamped);
}

/**
 * Thin wrapper that owns a `StreamPublisher` and exposes two domain calls.
 * Cheap to construct with `enabled=false` (no Redis connection).
 */
export class WatchlistPublisher {
  private readonly cfg: WatchlistPublisherConfig;
  private publisher: StreamPublisher | null;
  private readonly log: (event: Record<string, unknown>) => void;

  constructor(cfg: WatchlistPublisherConfig) {
    this.cfg = cfg;
    this.log = cfg.log ?? structuredLog;
    if (!cfg.enabled) {
      this.publisher = null;
      return;
    }
    this.publisher = cfg.publisher ?? new StreamPublisher({ url: cfg.redisUrl });
  }

  /** True when publishing is actually wired. */
  get enabled(): boolean {
    return this.publisher !== null;
  }

  /**
   * Convert a decision-core input → `WatchlistUpsert` proto. Exposed for
   * testing without a live Redis. Async because proto types are lazy-loaded.
   */
  async buildUpsert(input: WatchlistDecisionInput): Promise<WatchlistUpsertType> {
    const { Side } = await loadProto();
    const edge = input.pMarket === null ? 0 : input.pFundamental - input.pMarket;
    const upsert: WatchlistUpsertType = {
      messageId: { value: uuidBytes() },
      market: {
        polymarketId: input.marketId,
        ctfAddress: input.ctfAddress ?? '',
      },
      version: BigInt(Math.max(0, input.version)),
      edgePpm: probToPpm(edge),
      pFundamentalPpm: probToPpm(input.pFundamental),
      confidencePpm: probToPpm(input.confidence ?? 0.5),
      sideHint: sideHintFor(input.recommendedAction, Side),
      maxPositionUsd: {
        amountMicros: BigInt(Math.round((input.maxPositionUsd ?? 0) * 1_000_000)),
      },
      maxSlippageBps: input.maxSlippageBps ?? 50,
      ttlMs: BigInt(input.ttlMs ?? DEFAULT_TTL_MS),
      issuedAtUnixMs: BigInt(Date.now()),
      triggerPolicy: undefined,
    };
    return upsert;
  }

  /**
   * Publish a decision if (a) the bus is enabled and (b) the decision is
   * ACCEPT or SNOOZE. Returns the assigned stream id, or `null` on skip.
   *
   * Caller controls hot-path: this is intended to run after the SQLite
   * commit in a `queueMicrotask` / background loop.
   */
  async maybePublishDecision(input: WatchlistDecisionInput): Promise<string | null> {
    const kind = classifyDecision(input.recommendedAction);
    if (kind === 'skip') return null;
    if (!this.publisher) return null;

    const { WatchlistUpsert } = await loadProto();
    const upsert = await this.buildUpsert(input);
    try {
      const id = await this.publisher.publish(
        WATCHLIST_UPSERT,
        WatchlistUpsert,
        upsert,
        input.traceId,
      );
      this.log({
        msg: 'watchlist_upsert published',
        topic: WATCHLIST_UPSERT,
        id,
        market_id: input.marketId,
        action: input.recommendedAction,
        kind,
        version: input.version,
        trace_id: input.traceId,
      });
      return id;
    } catch (err) {
      // Publish failures must not kill the Slow Loop.
      this.log({
        msg: 'watchlist_upsert publish failed',
        level: 'warn',
        error: err instanceof Error ? err.message : String(err),
        market_id: input.marketId,
        trace_id: input.traceId,
      });
      return null;
    }
  }

  /** Publish a `WatchlistRemove`. Returns stream id, or null on skip/err. */
  async publishRemove(
    marketId: string,
    opts: {
      ctfAddress?: string;
      version: number;
      reason?: number;
      traceId?: string;
    },
  ): Promise<string | null> {
    if (!this.publisher) return null;
    const { WatchlistRemove } = await loadProto();
    const remove: WatchlistRemoveType = {
      messageId: { value: uuidBytes() },
      market: {
        polymarketId: marketId,
        ctfAddress: opts.ctfAddress ?? '',
      },
      version: BigInt(Math.max(0, opts.version)),
      reason: opts.reason ?? 0,
      issuedAtUnixMs: BigInt(Date.now()),
    };
    try {
      const id = await this.publisher.publish(
        WATCHLIST_REMOVE,
        WatchlistRemove,
        remove,
        opts.traceId,
      );
      this.log({
        msg: 'watchlist_remove published',
        topic: WATCHLIST_REMOVE,
        id,
        market_id: marketId,
        trace_id: opts.traceId,
      });
      return id;
    } catch (err) {
      this.log({
        msg: 'watchlist_remove publish failed',
        level: 'warn',
        error: err instanceof Error ? err.message : String(err),
        market_id: marketId,
        trace_id: opts.traceId,
      });
      return null;
    }
  }

  async close(): Promise<void> {
    await this.publisher?.close();
    this.publisher = null;
  }
}

// ---------- Module-level singleton (opt-in) ----------

let singleton: WatchlistPublisher | null = null;

/**
 * Get or create a process-wide publisher using `configFromEnv()`. Safe to
 * call from anywhere; when `DUAL_LOOP_BUS_ENABLED=false` (default) it
 * returns a no-op instance that never touches the network.
 */
export function getWatchlistPublisher(): WatchlistPublisher {
  if (!singleton) {
    singleton = new WatchlistPublisher(configFromEnv());
  }
  return singleton;
}

/** Resets the singleton — for tests. */
export function __resetWatchlistPublisherForTesting(): void {
  void singleton?.close();
  singleton = null;
}
