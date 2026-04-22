/**
 * Shadow-write parity diff logger.
 *
 * Compares the primary SQLite store against the Postgres shadow store on
 * the `predictions` table (the only one with active shadow writes in Phase
 * 1). Emits either `PARITY OK` or `PARITY DIVERGE` lines per row, and
 * increments in-process counters consumed by the future Prometheus exporter
 * (see `docs/dual_loop/db_migration.md`).
 *
 * Two modes:
 *   - `runParitySample({ limit })` — cheap cron job, samples N random rows.
 *   - `runParityFullScan()`        — expensive weekly audit of every row.
 *
 * CLI entrypoint (`bun run src/services/db_parity.ts`) runs a sample pass
 * by default; pass `--full` for a full scan. See `package.json` script
 * `db:parity`.
 */
import type { Database as BunDatabase } from 'bun:sqlite';
import { initDb } from './db';
import * as shadow from './db_pg';

export interface ParityRow {
  market_id: string;
  forecast_timestamp: string;
  market_price: number | null;
  dexter_p: number | null;
  naked_p: number | null;
  outcome: number | null;
  is_valid: number;
  api_cost_usd: number | null;
}

export interface ParityResult {
  tableName: string;
  sampled: number;
  matches: number;
  diverges: number;
  missingInShadow: number;
  missingInPrimary: number;
  divergences: ParityDivergence[];
}

export interface ParityDivergence {
  market_id: string;
  forecast_timestamp: string;
  field: string;
  primary: unknown;
  shadow: unknown;
}

const parityCounters = {
  parity_ok_total: 0 as number,
  parity_diverge_total: 0 as number,
  parity_missing_shadow_total: 0 as number,
  parity_missing_primary_total: 0 as number,
};

export function getParityMetrics(): Readonly<typeof parityCounters> {
  return { ...parityCounters };
}

export function resetParityMetrics(): void {
  parityCounters.parity_ok_total = 0;
  parityCounters.parity_diverge_total = 0;
  parityCounters.parity_missing_shadow_total = 0;
  parityCounters.parity_missing_primary_total = 0;
}

// ---------------------------------------------------------------------------
// Comparison helpers
// ---------------------------------------------------------------------------

const PARITY_FIELDS: (keyof ParityRow)[] = [
  'market_price',
  'dexter_p',
  'naked_p',
  'outcome',
  'is_valid',
  'api_cost_usd',
];

// Allow small numeric drift from double-precision rounding. SQLite stores
// REAL as IEEE-754 double so this should only matter if PG ever narrows to
// `real` (we use `double precision` everywhere).
const NUMERIC_EPSILON = 1e-9;

function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null && b == null) return true;
  if (typeof a === 'number' && typeof b === 'number') {
    return Math.abs(a - b) <= NUMERIC_EPSILON;
  }
  // SQLite INTEGER columns round-trip as number; PG integer columns too.
  return false;
}

function keyOf(row: Pick<ParityRow, 'market_id' | 'forecast_timestamp'>): string {
  return `${row.market_id}\u0001${row.forecast_timestamp}`;
}

function fetchPrimarySample(
  db: BunDatabase,
  limit: number,
): ParityRow[] {
  const stmt = db.query(`
    SELECT market_id, forecast_timestamp, market_price, dexter_p, naked_p,
           outcome, is_valid, api_cost_usd
    FROM predictions
    ORDER BY RANDOM()
    LIMIT ?1
  `);
  return stmt.all(limit) as ParityRow[];
}

function fetchPrimaryByKeys(
  db: BunDatabase,
  keys: Array<{ market_id: string; forecast_timestamp: string }>,
): ParityRow[] {
  if (keys.length === 0) return [];
  const placeholders = keys.map(() => '(?, ?)').join(',');
  const stmt = db.query(`
    SELECT market_id, forecast_timestamp, market_price, dexter_p, naked_p,
           outcome, is_valid, api_cost_usd
    FROM predictions
    WHERE (market_id, forecast_timestamp) IN (${placeholders})
  `);
  const args: string[] = [];
  for (const k of keys) {
    args.push(k.market_id, k.forecast_timestamp);
  }
  return stmt.all(...args) as ParityRow[];
}

async function fetchShadowByKeys(
  keys: Array<{ market_id: string; forecast_timestamp: string }>,
): Promise<ParityRow[]> {
  if (keys.length === 0) return [];
  const handle = shadow.getPgDb();
  // Build a VALUES list; use parameterised query so client-side placeholders
  // escape input safely.
  const tuples: string[] = [];
  const params: (string | number)[] = [];
  let i = 1;
  for (const k of keys) {
    tuples.push(`($${i++}, $${i++})`);
    params.push(k.market_id, k.forecast_timestamp);
  }
  const query = `
    SELECT market_id, forecast_timestamp, market_price, dexter_p, naked_p,
           outcome, is_valid, api_cost_usd
    FROM predictions
    WHERE (market_id, forecast_timestamp) IN (${tuples.join(',')})
  `;
  const rows = await handle.client.unsafe<ParityRow[]>(query, params);
  return Array.from(rows).map((row) => ({
    market_id: row.market_id,
    forecast_timestamp: row.forecast_timestamp,
    market_price: row.market_price,
    dexter_p: row.dexter_p,
    naked_p: row.naked_p,
    outcome: row.outcome,
    is_valid: row.is_valid,
    api_cost_usd: row.api_cost_usd,
  }));
}

function diffRows(
  primary: ParityRow,
  shadowRow: ParityRow,
): ParityDivergence[] {
  const out: ParityDivergence[] = [];
  for (const field of PARITY_FIELDS) {
    if (!valuesEqual(primary[field], shadowRow[field])) {
      out.push({
        market_id: primary.market_id,
        forecast_timestamp: primary.forecast_timestamp,
        field,
        primary: primary[field],
        shadow: shadowRow[field],
      });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Public parity runners
// ---------------------------------------------------------------------------

export async function runParitySample(
  options: { limit?: number; db?: BunDatabase } = {},
): Promise<ParityResult> {
  const limit = options.limit ?? 100;
  const db = options.db ?? initDb();
  const sample = fetchPrimarySample(db, limit);
  if (sample.length === 0) {
    const empty: ParityResult = {
      tableName: 'predictions',
      sampled: 0,
      matches: 0,
      diverges: 0,
      missingInShadow: 0,
      missingInPrimary: 0,
      divergences: [],
    };
    // eslint-disable-next-line no-console
    console.log(`PARITY OK {table=predictions,row_count=0} (empty primary)`);
    return empty;
  }

  const shadowRows = await fetchShadowByKeys(
    sample.map((r) => ({
      market_id: r.market_id,
      forecast_timestamp: r.forecast_timestamp,
    })),
  );
  const shadowByKey = new Map(shadowRows.map((r) => [keyOf(r), r]));

  let matches = 0;
  let missingInShadow = 0;
  const divergences: ParityDivergence[] = [];

  for (const row of sample) {
    const shadowRow = shadowByKey.get(keyOf(row));
    if (!shadowRow) {
      missingInShadow += 1;
      parityCounters.parity_missing_shadow_total += 1;
      // eslint-disable-next-line no-console
      console.log(
        `PARITY DIVERGE {table=predictions,market_id=${row.market_id},forecast_timestamp=${row.forecast_timestamp},field=<row>,primary=present,shadow=missing}`,
      );
      continue;
    }
    const rowDiffs = diffRows(row, shadowRow);
    if (rowDiffs.length === 0) {
      matches += 1;
      parityCounters.parity_ok_total += 1;
    } else {
      parityCounters.parity_diverge_total += rowDiffs.length;
      for (const diff of rowDiffs) {
        divergences.push(diff);
        // eslint-disable-next-line no-console
        console.log(
          `PARITY DIVERGE {table=predictions,market_id=${diff.market_id},forecast_timestamp=${diff.forecast_timestamp},field=${diff.field},primary=${JSON.stringify(diff.primary)},shadow=${JSON.stringify(diff.shadow)}}`,
        );
      }
    }
  }

  const diverges = divergences.length;
  const result: ParityResult = {
    tableName: 'predictions',
    sampled: sample.length,
    matches,
    diverges,
    missingInShadow,
    missingInPrimary: 0,
    divergences,
  };
  if (diverges === 0 && missingInShadow === 0) {
    // eslint-disable-next-line no-console
    console.log(
      `PARITY OK {table=predictions,row_count=${sample.length}}`,
    );
  }
  return result;
}

export async function runParityFullScan(options: {
  db?: BunDatabase;
} = {}): Promise<ParityResult> {
  const db = options.db ?? initDb();
  const primaryRows = db
    .query(
      `
      SELECT market_id, forecast_timestamp, market_price, dexter_p, naked_p,
             outcome, is_valid, api_cost_usd
      FROM predictions
      ORDER BY market_id, forecast_timestamp
    `,
    )
    .all() as ParityRow[];

  // Fetch all shadow rows in batches to avoid one giant IN clause.
  const result: ParityResult = {
    tableName: 'predictions',
    sampled: primaryRows.length,
    matches: 0,
    diverges: 0,
    missingInShadow: 0,
    missingInPrimary: 0,
    divergences: [],
  };

  const BATCH = 500;
  const primaryKeys = new Set(primaryRows.map(keyOf));
  for (let i = 0; i < primaryRows.length; i += BATCH) {
    const batch = primaryRows.slice(i, i + BATCH);
    const shadowRows = await fetchShadowByKeys(
      batch.map((r) => ({
        market_id: r.market_id,
        forecast_timestamp: r.forecast_timestamp,
      })),
    );
    const shadowByKey = new Map(shadowRows.map((r) => [keyOf(r), r]));
    for (const row of batch) {
      const s = shadowByKey.get(keyOf(row));
      if (!s) {
        result.missingInShadow += 1;
        parityCounters.parity_missing_shadow_total += 1;
        continue;
      }
      const diffs = diffRows(row, s);
      if (diffs.length === 0) {
        result.matches += 1;
        parityCounters.parity_ok_total += 1;
      } else {
        result.diverges += diffs.length;
        parityCounters.parity_diverge_total += diffs.length;
        result.divergences.push(...diffs);
      }
    }
  }

  // Detect rows present in shadow but missing in primary.
  const shadowCount = await shadow.countPredictions();
  if (shadowCount > primaryKeys.size) {
    // We don't enumerate here (could be large); surface count only.
    result.missingInPrimary = shadowCount - primaryKeys.size;
    parityCounters.parity_missing_primary_total += result.missingInPrimary;
  }

  // eslint-disable-next-line no-console
  console.log(
    `PARITY ${result.diverges === 0 && result.missingInShadow === 0 && result.missingInPrimary === 0 ? 'OK' : 'DIVERGE'} {table=predictions,row_count=${result.sampled},matches=${result.matches},diverges=${result.diverges},missing_shadow=${result.missingInShadow},missing_primary=${result.missingInPrimary}}`,
  );
  return result;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const full = args.includes('--full');
  const limitArg = args.find((a) => a.startsWith('--limit='));
  const limit = limitArg ? Number(limitArg.split('=')[1]) : 100;

  try {
    if (full) {
      await runParityFullScan();
    } else {
      await runParitySample({ limit });
    }
  } finally {
    await shadow.closePgDb().catch(() => {});
  }
}

if (import.meta.main) {
  main().catch((err) => {
    // eslint-disable-next-line no-console
    console.error('db_parity failed:', err);
    process.exit(1);
  });
}
