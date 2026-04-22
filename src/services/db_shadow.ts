/**
 * Shadow-write wrapper for Pythia's primary SQLite store.
 *
 * Phase 1 — Track D (Dual-Loop). Reads stay 100% on SQLite (primary). Writes
 * fan out to Postgres in the background via `Promise.allSettled` so a PG
 * failure never affects the primary loop.
 *
 * Control plane:
 *   - Env flag `DUAL_LOOP_SHADOW_PG=true` enables shadow writes.
 *   - When disabled, this module is a transparent re-export of `./db`. That
 *     invariant is what makes the PR zero-impact on existing behaviour.
 *
 * Observability:
 *   - Errors append to `logs/shadow_pg_errors.log` (NDJSON).
 *   - In-process counters can be read via `getShadowPgMetrics()` for scrape
 *     by a future Prometheus exporter.
 *
 * Risk reference: `docs/dual_loop/07-risks.md` R13 (data divergence).
 */
import { appendFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import type { Database, PredictionRow } from './db';
import * as primary from './db';
import * as defaultShadow from './db_pg';

// Test seam: the shadow target can be swapped (see `__setShadowForTests`).
// Production always uses `defaultShadow` imported above.
type ShadowTarget = Pick<
  typeof defaultShadow,
  'insertPrediction' | 'updateOutcome' | 'pingPg'
>;

let shadow: ShadowTarget = defaultShadow;

/** @internal — for tests only. Pass `null` to restore the real adapter. */
export function __setShadowForTests(override: ShadowTarget | null): void {
  shadow = override ?? defaultShadow;
}

// ---------------------------------------------------------------------------
// Feature flag + logging
// ---------------------------------------------------------------------------

export const SHADOW_FLAG_ENV = 'DUAL_LOOP_SHADOW_PG';
const SHADOW_LOG_DIR = process.env.LOG_DIR ?? path.resolve(process.cwd(), 'logs');
const SHADOW_LOG_PATH = path.join(SHADOW_LOG_DIR, 'shadow_pg_errors.log');

export function isShadowPgEnabled(): boolean {
  const raw = (process.env[SHADOW_FLAG_ENV] ?? '').trim().toLowerCase();
  return raw === '1' || raw === 'true' || raw === 'yes' || raw === 'on';
}

// Minimal Prometheus-style in-process counters (scrape later).
interface ShadowMetrics {
  shadow_pg_writes_total: Record<string, number>;
  shadow_pg_errors_total: Record<string, number>;
}

const metrics: ShadowMetrics = {
  shadow_pg_writes_total: {},
  shadow_pg_errors_total: {},
};

export function getShadowPgMetrics(): ShadowMetrics {
  return {
    shadow_pg_writes_total: { ...metrics.shadow_pg_writes_total },
    shadow_pg_errors_total: { ...metrics.shadow_pg_errors_total },
  };
}

export function resetShadowPgMetrics(): void {
  metrics.shadow_pg_writes_total = {};
  metrics.shadow_pg_errors_total = {};
}

function increment(
  bucket: keyof ShadowMetrics,
  label: string,
  by = 1,
): void {
  const group = metrics[bucket];
  group[label] = (group[label] ?? 0) + by;
}

async function logShadowError(
  operation: string,
  payload: Record<string, unknown>,
  err: unknown,
): Promise<void> {
  const record = {
    ts: new Date().toISOString(),
    operation,
    error: err instanceof Error ? err.message : String(err),
    stack: err instanceof Error ? err.stack : undefined,
    payload,
  };
  try {
    await mkdir(SHADOW_LOG_DIR, { recursive: true });
    await appendFile(SHADOW_LOG_PATH, `${JSON.stringify(record)}\n`, 'utf8');
  } catch {
    // Never let logging crash the primary path.
  }
}

/**
 * Fire-and-forget helper: runs `fn()` in the background, increments metrics
 * on success/failure, and writes structured JSON on failure. Does **not**
 * block the primary write.
 */
function fireAndForgetShadow(
  operation: string,
  payload: Record<string, unknown>,
  fn: () => Promise<void>,
): void {
  if (!isShadowPgEnabled()) return;
  // Run async & attach handlers. Use Promise.allSettled shape for symmetry
  // with future multi-target fan-out.
  queueMicrotask(() => {
    void Promise.allSettled([fn()]).then(([result]) => {
      if (result.status === 'fulfilled') {
        increment('shadow_pg_writes_total', operation);
      } else {
        increment('shadow_pg_errors_total', operation);
        void logShadowError(operation, payload, result.reason);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Re-exports (make this module a drop-in for ./db)
// ---------------------------------------------------------------------------

export type { Database, PredictionRow } from './db';
export {
  ensureAttentionEngineSchema,
  ensureWatchlistEngineSchema,
} from './db';

/**
 * `initDb` initialises the SQLite store exactly as before. When the shadow
 * flag is on we opportunistically ping Postgres so operators see any
 * connectivity issue at startup, but we *never* fail initialisation.
 */
export function initDb(): Database {
  const db = primary.initDb();
  if (isShadowPgEnabled()) {
    void shadow.pingPg().then((ok) => {
      if (!ok) {
        increment('shadow_pg_errors_total', 'ping');
      }
    });
  }
  return db;
}

export function insertPrediction(db: Database, data: PredictionRow): void {
  primary.insertPrediction(db, data);
  fireAndForgetShadow(
    'insertPrediction',
    { market_id: data.market_id, forecast_timestamp: data.forecast_timestamp },
    () => shadow.insertPrediction(data),
  );
}

export function updateOutcome(
  db: Database,
  marketId: string,
  outcome: number,
  resolvedAt: string,
): void {
  primary.updateOutcome(db, marketId, outcome, resolvedAt);
  fireAndForgetShadow(
    'updateOutcome',
    { market_id: marketId, outcome, resolved_at: resolvedAt },
    () => shadow.updateOutcome(marketId, outcome, resolvedAt),
  );
}

// Reads stay on SQLite.
export function getUnresolved(db: Database): PredictionRow[] {
  return primary.getUnresolved(db);
}

export function getTodayCost(db: Database): number {
  return primary.getTodayCost(db);
}

export function getMonthCost(db: Database): number {
  return primary.getMonthCost(db);
}

// Exported for tests / operators.
export { SHADOW_LOG_PATH };
