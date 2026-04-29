// Environment configuration.
//
// All settings are loaded from env vars at startup. Missing required values
// cause a hard exit with a clear message. Defaults favor dev ergonomics.

export interface Config {
  host: string;
  port: number;
  logLevel: string;

  redisUrl: string;
  natsUrl: string;

  // PASETO v4.local key: 32 bytes, base64-encoded. A random dev key is
  // generated if unset, but a warning is logged.
  pasetoKey: Buffer;

  // Hardcoded single operator for MVP (TODO: replace with OIDC in Phase 3).
  operatorId: string;
  operatorPasswordHash: string;

  // HMAC secret for KillCommand signatures (see 04-bus-protocol.md §59).
  killHmacSecret: string;

  // Token TTL.
  tokenTtlSeconds: number;

  // Per-operator REST rate limit.
  rateLimitPerSecond: number;

  // WS backpressure threshold (buffered patches). When exceeded, server
  // flushes the queue and sends a snapshot instead of delta.
  wsMaxBufferedPatches: number;

  // Snapshot re-sync interval for WS clients (ms).
  wsSnapshotIntervalMs: number;

  // Heartbeat interval (ms). 2 consecutive missed pings → disconnect.
  wsHeartbeatIntervalMs: number;

  // State persistence (Redis hash key + periodic flush).
  stateSnapshotIntervalMs: number;

  // Kill ack timeout (ms).
  killAckTimeoutMs: number;

  // Idempotency TTL for accepted /signals/:id/* responses.
  idempotencyTtlSeconds: number;
}

function parseIntEnv(name: string, defaultValue: number): number {
  const raw = process.env[name];
  if (!raw) return defaultValue;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) {
    throw new Error(`Invalid integer for env ${name}: ${raw}`);
  }
  return n;
}

function decodePasetoKey(raw: string | undefined): Buffer {
  if (!raw) {
    // Dev-only fallback: random 32-byte key. Tokens won't survive restart.
    const buf = Buffer.alloc(32);
    for (let i = 0; i < 32; i++) buf[i] = Math.floor(Math.random() * 256);
    // eslint-disable-next-line no-console
    console.warn(
      "[gateway-api] GATEWAY_PASETO_KEY unset; using ephemeral random key. " +
        "Tokens will be invalidated on restart.",
    );
    return buf;
  }
  const decoded = Buffer.from(raw, "base64");
  if (decoded.length !== 32) {
    throw new Error(
      `GATEWAY_PASETO_KEY must decode to 32 bytes (got ${decoded.length}). ` +
        "Generate with: openssl rand -base64 32",
    );
  }
  return decoded;
}

export function loadConfig(): Config {
  return {
    host: process.env.HOST ?? "0.0.0.0",
    port: parseIntEnv("PORT", 9103),
    logLevel: process.env.LOG_LEVEL ?? "info",

    redisUrl: process.env.REDIS_URL ?? "redis://127.0.0.1:6379",
    natsUrl: process.env.NATS_URL ?? "nats://127.0.0.1:4222",

    pasetoKey: decodePasetoKey(process.env.GATEWAY_PASETO_KEY),

    operatorId: process.env.OPERATOR_ID ?? "admin",
    // Default is bcrypt("changeme") — DEV ONLY. Must be overridden in staging/prod.
    // $2a$10$o8TxQnbbSnYLUQ8b97fj9e.FrX3uuKA9CS5Ftx4QbO9CxVGRbcFTq
    operatorPasswordHash:
      process.env.OPERATOR_PASSWORD_HASH ??
      "$2a$10$o8TxQnbbSnYLUQ8b97fj9e.FrX3uuKA9CS5Ftx4QbO9CxVGRbcFTq",

    killHmacSecret:
      process.env.KILL_HMAC_SECRET ?? "dev-kill-hmac-secret-change-me",

    tokenTtlSeconds: parseIntEnv("TOKEN_TTL_SECONDS", 8 * 3600),
    rateLimitPerSecond: parseIntEnv("RATE_LIMIT_PER_SECOND", 100),
    wsMaxBufferedPatches: parseIntEnv("WS_MAX_BUFFERED_PATCHES", 64),
    wsSnapshotIntervalMs: parseIntEnv("WS_SNAPSHOT_INTERVAL_MS", 30_000),
    wsHeartbeatIntervalMs: parseIntEnv("WS_HEARTBEAT_INTERVAL_MS", 5_000),
    stateSnapshotIntervalMs: parseIntEnv("STATE_SNAPSHOT_INTERVAL_MS", 30_000),
    killAckTimeoutMs: parseIntEnv("KILL_ACK_TIMEOUT_MS", 2_000),
    idempotencyTtlSeconds: parseIntEnv("IDEMPOTENCY_TTL_SECONDS", 24 * 3600),
  };
}
