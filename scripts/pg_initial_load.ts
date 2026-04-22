#!/usr/bin/env bun
/**
 * One-time initial load from the SQLite primary (`data/pythia.db`) into the
 * Postgres shadow store.
 *
 * Run this ONCE after `bash scripts/pg_migrate.sh` but BEFORE enabling
 * `DUAL_LOOP_SHADOW_PG=true`. It ensures the shadow store starts at parity
 * with the primary instead of diverging because writes started midway
 * through the history. See `docs/dual_loop/db_migration.md` for runbook.
 *
 * Scope (Phase 1): only the `predictions` table is mirrored via
 * `db_shadow.ts`, so this script loads only `predictions`. Other tables
 * (decision_core / watchlist_engine / attention_engine) are loaded lazily
 * by the running process in later phases once their wrappers exist.
 */
import { Database as BunDatabase } from 'bun:sqlite';
import path from 'node:path';
import { sql } from 'drizzle-orm';
import { CONFIG } from '../src/config';
import * as shadow from '../src/services/db_pg';
import * as pgSchema from '../src/db/pg/schema';

interface RawPredictionRow {
  market_id: string;
  question: string;
  category: string | null;
  volume: number | null;
  created_at: string | null;
  end_date: string | null;
  market_price: number;
  dexter_p: number | null;
  dexter_confidence: string | null;
  dexter_reasoning: string | null;
  dexter_key_risk: string | null;
  dexter_base_rate: string | null;
  naked_p: number | null;
  naked_reasoning: string | null;
  research_brief_json: string | null;
  forecast_timestamp: string;
  is_valid: number | null;
  outcome: number | null;
  resolved_at: string | null;
  brier_dexter: number | null;
  brier_naked: number | null;
  brier_market: number | null;
  api_cost_usd: number | null;
}

function log(msg: string): void {
  // eslint-disable-next-line no-console
  console.log(`[pg_initial_load] ${msg}`);
}

async function main(): Promise<void> {
  const sqlitePath = process.env.DB_PATH || CONFIG.paths.db;
  log(`source: ${path.resolve(sqlitePath)}`);
  log(`target: ${shadow.resolvePgUrl().replace(/:[^:@]*@/, ':***@')}`);

  const db = new BunDatabase(sqlitePath, { readonly: true, strict: true });
  const rows = db
    .query(
      `
      SELECT market_id, question, category, volume, created_at, end_date,
             market_price, dexter_p, dexter_confidence, dexter_reasoning,
             dexter_key_risk, dexter_base_rate, naked_p, naked_reasoning,
             research_brief_json, forecast_timestamp, is_valid, outcome,
             resolved_at, brier_dexter, brier_naked, brier_market, api_cost_usd
      FROM predictions
      ORDER BY id
    `,
    )
    .all() as RawPredictionRow[];
  log(`found ${rows.length} rows in predictions`);

  const ok = await shadow.pingPg();
  if (!ok) {
    throw new Error('cannot reach Postgres shadow store (check DUAL_LOOP_PG_URL)');
  }

  const handle = shadow.getPgDb();
  const BATCH = 500;
  let inserted = 0;

  for (let i = 0; i < rows.length; i += BATCH) {
    const batch = rows.slice(i, i + BATCH).map((row) => ({
      market_id: row.market_id,
      question: row.question,
      category: row.category,
      volume: row.volume,
      created_at: row.created_at,
      end_date: row.end_date,
      market_price: row.market_price,
      dexter_p: row.dexter_p,
      dexter_confidence: row.dexter_confidence,
      dexter_reasoning: row.dexter_reasoning,
      dexter_key_risk: row.dexter_key_risk,
      dexter_base_rate: row.dexter_base_rate,
      naked_p: row.naked_p,
      naked_reasoning: row.naked_reasoning,
      research_brief_json: row.research_brief_json,
      forecast_timestamp: row.forecast_timestamp,
      is_valid: row.is_valid ?? 1,
      outcome: row.outcome,
      resolved_at: row.resolved_at,
      brier_dexter: row.brier_dexter,
      brier_naked: row.brier_naked,
      brier_market: row.brier_market,
      api_cost_usd: row.api_cost_usd ?? 0,
    }));

    if (batch.length === 0) continue;

    await handle.db
      .insert(pgSchema.predictions)
      .values(batch)
      .onConflictDoUpdate({
        target: [
          pgSchema.predictions.market_id,
          pgSchema.predictions.forecast_timestamp,
        ],
        set: {
          outcome: sql`excluded.outcome`,
          resolved_at: sql`excluded.resolved_at`,
          brier_dexter: sql`excluded.brier_dexter`,
          brier_naked: sql`excluded.brier_naked`,
          brier_market: sql`excluded.brier_market`,
          is_valid: sql`excluded.is_valid`,
        },
      });

    inserted += batch.length;
    log(`progress: ${inserted}/${rows.length}`);
  }

  log(`done: loaded ${inserted} rows`);
  db.close();
  await shadow.closePgDb();
}

if (import.meta.main) {
  main().catch((err) => {
    // eslint-disable-next-line no-console
    console.error('pg_initial_load failed:', err);
    process.exit(1);
  });
}
