/**
 * Topic names for the Dual-Loop bus.
 *
 * **Single source of truth** for TypeScript. Mirrors
 * `services/common/src/topics.rs`; a contract test in
 * `__tests__/contract.test.ts` enforces that the two lists stay in sync.
 *
 * Naming convention: `dl.<wire_version>.<domain>.<event>`.
 */

/** Semver-ish version prefix that matches `dual_loop.v1` protobuf package. */
export const WIRE_VERSION = 'v1' as const;

// ---------- Watchlist (pythia → sniper-ingest) ----------
export const WATCHLIST_UPSERT = 'dl.v1.watchlist.upsert' as const;
export const WATCHLIST_REMOVE = 'dl.v1.watchlist.remove' as const;

// ---------- Signals (pythia → gateway) ----------
export const SIGNALS_EMITTED = 'dl.v1.signals.emitted' as const;

// ---------- Triggers (sniper-ingest → sniper-executor) ----------
export const TRIGGERS_FIRED = 'dl.v1.triggers.fired' as const;

// ---------- Control plane (gateway → sniper-executor) ----------
export const CONTROL_KILL = 'dl.v1.control.kill' as const;
export const CONTROL_KILL_ACK = 'dl.v1.control.kill_ack' as const;

// ---------- Telemetry (sniper-executor → gateway-api, NATS JetStream) ----------
/** Periodic heartbeat from `sniper-executor`; proxied over WSS to Command Center. */
export const EXEC_STATUS = 'dl.v1.exec.status' as const;

/**
 * All topics in canonical order. A contract test checks byte-for-byte
 * equivalence with `services/common/src/topics.rs::ALL_TOPICS`.
 */
export const ALL_TOPICS = [
  WATCHLIST_UPSERT,
  WATCHLIST_REMOVE,
  SIGNALS_EMITTED,
  TRIGGERS_FIRED,
  CONTROL_KILL,
  CONTROL_KILL_ACK,
  EXEC_STATUS,
] as const;

export type BusTopic = (typeof ALL_TOPICS)[number];

/** Default consumer group names. */
export const CONSUMER_GROUPS = {
  SNIPER_INGEST: 'sniper-ingest',
  SNIPER_EXECUTOR: 'sniper-executor',
  GATEWAY: 'gateway-api',
  PYTHIA_CORE: 'pythia-core',
} as const;
