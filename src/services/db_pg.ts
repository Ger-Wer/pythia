/**
 * Postgres shadow adapter for Pythia (Dual-Loop Phase 1 — Track D).
 *
 * Mirrors the public API of `src/services/db.ts` against a drizzle-orm
 * Postgres connection. Reads still go through SQLite in Phase 1; this module
 * exists so writes can be **shadow-mirrored** to Postgres (see
 * `db_shadow.ts`) and compared via `db_parity.ts`.
 *
 * Connection pooling: uses `postgres-js` (max 10 connections). The pool is
 * cached per-URL so multiple call-sites in the same process share it.
 *
 * Graceful degradation: if Postgres is unreachable the adapter logs a single
 * warning and swallows the error. Shadow writes must never take down the
 * primary loop — see `docs/dual_loop/07-risks.md` R13 ("data divergence").
 */
import { and, eq, gte, lt, sql } from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import type { PredictionRow } from './db';
import * as schema from '../db/pg/schema';

export type PgDatabase = ReturnType<typeof drizzle<typeof schema>>;

// ---------------------------------------------------------------------------
// Connection management
// ---------------------------------------------------------------------------

interface PgHandle {
  client: ReturnType<typeof postgres>;
  db: PgDatabase;
  url: string;
}

const DEFAULT_PG_URL =
  'postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev';

let cached: PgHandle | null = null;
let loggedDegradedOnce = false;

export function resolvePgUrl(): string {
  return (
    process.env.DUAL_LOOP_PG_URL ??
    process.env.DATABASE_URL ??
    DEFAULT_PG_URL
  );
}

/**
 * Return a cached drizzle handle for the given URL (default = dev compose).
 * The underlying `postgres-js` pool is shared across callers.
 */
export function getPgDb(url?: string): PgHandle {
  const resolvedUrl = url ?? resolvePgUrl();
  if (cached && cached.url === resolvedUrl) {
    return cached;
  }
  if (cached && cached.url !== resolvedUrl) {
    void closePgDb().catch(() => {});
  }
  // When `DUAL_LOOP_PG_NOTIFY=true`, every backend we open has the GUC that
  // the Phase 1B LISTEN/NOTIFY triggers check set to 'on'. Without it the
  // trigger function short-circuits, so shadow writes produce no events — by
  // design, since gateway-api (Track B) is not wired yet.
  const notifyEnabled = process.env.DUAL_LOOP_PG_NOTIFY === 'true';
  const client = postgres(resolvedUrl, {
    max: 10,
    idle_timeout: 30,
    connect_timeout: 10,
    onnotice: () => {},
    // Disable automatic type parsing surprises; we match SQLite's text/number.
    transform: { undefined: null },
    connection: notifyEnabled
      ? { 'dual_loop.pg_notify': 'on' }
      : undefined,
  });
  const db = drizzle(client, { schema });
  cached = { client, db, url: resolvedUrl };
  return cached;
}

/** Close the cached pool (used in tests + graceful shutdown). */
export async function closePgDb(): Promise<void> {
  if (!cached) return;
  const handle = cached;
  cached = null;
  try {
    await handle.client.end({ timeout: 5 });
  } catch (err) {
    logDegraded('close', err);
  }
}

function logDegraded(op: string, err: unknown): void {
  if (loggedDegradedOnce) return;
  loggedDegradedOnce = true;
  const msg = err instanceof Error ? err.message : String(err);
  // eslint-disable-next-line no-console
  console.warn(
    `[db_pg] Postgres unavailable for ${op}: ${msg}. Shadow writes will be logged but not retried this session.`,
  );
}

export function resetDegradedLog(): void {
  loggedDegradedOnce = false;
}

// ---------------------------------------------------------------------------
// Schema bootstrap
// ---------------------------------------------------------------------------

/**
 * Apply all drizzle-generated migrations under `sql/migrations/`.
 * Use this from scripts / tests. The dev loop normally calls
 * `bun run db:pg:push` instead.
 */
export async function runPgMigrations(
  url?: string,
  migrationsFolder = 'sql/migrations',
): Promise<void> {
  const { migrate } = await import('drizzle-orm/postgres-js/migrator');
  const handle = getPgDb(url);
  await migrate(handle.db, { migrationsFolder });
}

export async function pingPg(url?: string): Promise<boolean> {
  try {
    const handle = getPgDb(url);
    await handle.client`select 1 as ok`;
    return true;
  } catch (err) {
    logDegraded('ping', err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Write API — mirrors src/services/db.ts
// ---------------------------------------------------------------------------

/**
 * Insert or update a prediction row. Matches the semantics of the SQLite
 * `ON CONFLICT(market_id, forecast_timestamp) DO UPDATE` in
 * `src/services/db.ts`.
 */
export async function insertPrediction(
  data: PredictionRow,
  url?: string,
): Promise<void> {
  const handle = getPgDb(url);
  const values = {
    market_id: data.market_id,
    question: data.question,
    category: data.category ?? null,
    volume: data.volume ?? null,
    created_at: data.created_at ?? null,
    end_date: data.end_date ?? null,
    market_price: data.market_price,
    dexter_p: data.dexter_p ?? null,
    dexter_confidence: data.dexter_confidence ?? null,
    dexter_reasoning: data.dexter_reasoning ?? null,
    dexter_key_risk: data.dexter_key_risk ?? null,
    dexter_base_rate: data.dexter_base_rate ?? null,
    naked_p: data.naked_p ?? null,
    naked_reasoning: data.naked_reasoning ?? null,
    research_brief_json: data.research_brief_json ?? null,
    forecast_timestamp: data.forecast_timestamp,
    api_cost_usd: data.api_cost_usd,
    is_valid: data.is_valid ?? 1,
  };

  await handle.db
    .insert(schema.predictions)
    .values(values)
    .onConflictDoUpdate({
      target: [
        schema.predictions.market_id,
        schema.predictions.forecast_timestamp,
      ],
      set: {
        question: values.question,
        category: values.category,
        volume: values.volume,
        created_at: values.created_at,
        end_date: values.end_date,
        market_price: values.market_price,
        dexter_p: values.dexter_p,
        dexter_confidence: values.dexter_confidence,
        dexter_reasoning: values.dexter_reasoning,
        dexter_key_risk: values.dexter_key_risk,
        dexter_base_rate: values.dexter_base_rate,
        naked_p: values.naked_p,
        naked_reasoning: values.naked_reasoning,
        research_brief_json: values.research_brief_json,
        api_cost_usd: values.api_cost_usd,
        is_valid: values.is_valid,
      },
    });
}

/**
 * Update outcome + Brier scores for every row of `market_id`. Matches the
 * SQL in `src/services/db.ts::updateOutcome`.
 */
export async function updateOutcome(
  marketId: string,
  outcome: number,
  resolvedAt: string,
  url?: string,
): Promise<void> {
  const handle = getPgDb(url);
  await handle.db.execute(sql`
    UPDATE predictions
    SET
      outcome = ${outcome},
      resolved_at = ${resolvedAt},
      brier_dexter = CASE
        WHEN dexter_p IS NOT NULL
        THEN ((dexter_p / 100.0) - ${outcome}) * ((dexter_p / 100.0) - ${outcome})
        ELSE NULL
      END,
      brier_naked = CASE
        WHEN naked_p IS NOT NULL
        THEN ((naked_p / 100.0) - ${outcome}) * ((naked_p / 100.0) - ${outcome})
        ELSE NULL
      END,
      brier_market = ((market_price / 100.0) - ${outcome}) * ((market_price / 100.0) - ${outcome})
    WHERE market_id = ${marketId} AND is_valid = 1
  `);
}

// ---------------------------------------------------------------------------
// Read API — used by parity checks and ad-hoc tooling (NOT the primary loop)
// ---------------------------------------------------------------------------

export async function getUnresolved(url?: string): Promise<PredictionRow[]> {
  const handle = getPgDb(url);
  const rows = await handle.db
    .select({
      market_id: schema.predictions.market_id,
      question: schema.predictions.question,
      category: schema.predictions.category,
      volume: schema.predictions.volume,
      created_at: schema.predictions.created_at,
      end_date: schema.predictions.end_date,
      market_price: schema.predictions.market_price,
      dexter_p: schema.predictions.dexter_p,
      dexter_confidence: schema.predictions.dexter_confidence,
      dexter_reasoning: schema.predictions.dexter_reasoning,
      dexter_key_risk: schema.predictions.dexter_key_risk,
      naked_p: schema.predictions.naked_p,
      naked_reasoning: schema.predictions.naked_reasoning,
      forecast_timestamp: schema.predictions.forecast_timestamp,
      api_cost_usd: schema.predictions.api_cost_usd,
    })
    .from(schema.predictions)
    .where(
      and(
        sql`${schema.predictions.outcome} IS NULL`,
        eq(schema.predictions.is_valid, 1),
      ),
    )
    .orderBy(schema.predictions.forecast_timestamp);
  return rows.map((row) => ({
    ...row,
    // volume/market_price are not nullable in PredictionRow; coerce nulls.
    volume: (row.volume ?? 0) as number,
    market_price: (row.market_price ?? 0) as number,
    created_at: row.created_at ?? '',
    end_date: row.end_date ?? '',
    api_cost_usd: (row.api_cost_usd ?? 0) as number,
  }));
}

export async function getCostInRange(
  start: Date,
  end: Date,
  url?: string,
): Promise<number> {
  const handle = getPgDb(url);
  const rows = await handle.db
    .select({
      total: sql<number>`COALESCE(SUM(${schema.predictions.api_cost_usd}), 0)`,
    })
    .from(schema.predictions)
    .where(
      and(
        gte(schema.predictions.forecast_timestamp, start.toISOString()),
        lt(schema.predictions.forecast_timestamp, end.toISOString()),
        eq(schema.predictions.is_valid, 1),
      ),
    );
  return Number(rows[0]?.total ?? 0);
}

function startOfUtcDay(date: Date): Date {
  return new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
  );
}

function startOfNextUtcDay(date: Date): Date {
  const day = startOfUtcDay(date);
  day.setUTCDate(day.getUTCDate() + 1);
  return day;
}

function startOfUtcMonth(date: Date): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1));
}

function startOfNextUtcMonth(date: Date): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1));
}

export async function getTodayCost(url?: string): Promise<number> {
  const now = new Date();
  return getCostInRange(startOfUtcDay(now), startOfNextUtcDay(now), url);
}

export async function getMonthCost(url?: string): Promise<number> {
  const now = new Date();
  return getCostInRange(startOfUtcMonth(now), startOfNextUtcMonth(now), url);
}

// ---------------------------------------------------------------------------
// Parity helpers — expose raw prediction rows for diff'ing against SQLite.
// ---------------------------------------------------------------------------

export interface ParitySamplePrediction {
  market_id: string;
  forecast_timestamp: string;
  market_price: number | null;
  dexter_p: number | null;
  naked_p: number | null;
  outcome: number | null;
  is_valid: number;
  api_cost_usd: number | null;
  [key: string]: unknown;
}

export async function sampleRandomPredictions(
  limit: number,
  url?: string,
): Promise<ParitySamplePrediction[]> {
  const handle = getPgDb(url);
  const rows = await handle.db.execute<ParitySamplePrediction>(sql`
    SELECT
      market_id,
      forecast_timestamp,
      market_price,
      dexter_p,
      naked_p,
      outcome,
      is_valid,
      api_cost_usd
    FROM predictions
    TABLESAMPLE BERNOULLI (1)
    LIMIT ${limit}
  `);
  return Array.from(rows);
}

export async function countPredictions(url?: string): Promise<number> {
  const handle = getPgDb(url);
  const rows = await handle.db
    .select({ n: sql<number>`COUNT(*)` })
    .from(schema.predictions);
  return Number(rows[0]?.n ?? 0);
}
