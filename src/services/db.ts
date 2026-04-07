import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { Database as BunDatabase } from 'bun:sqlite';
import { CONFIG } from '../config';
import { ensureDecisionCoreSchema } from './decision_core';

export type Database = BunDatabase;

export interface PredictionRow {
  market_id: string;
  question: string;
  category?: string | null;
  volume: number;
  created_at: string;
  end_date: string;
  market_price: number;
  dexter_p?: number | null;
  dexter_confidence?: string | null;
  dexter_reasoning?: string | null;
  dexter_key_risk?: string | null;
  dexter_base_rate?: string | null;
  naked_p?: number | null;
  naked_reasoning?: string | null;
  research_brief_json?: string | null;
  forecast_timestamp: string;
  api_cost_usd: number;
  is_valid?: number | null;
}

const INVALID_DATA_CUTOFF = '2026-03-29T17:37:00';

const BASE_TABLE_SQL = `
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    question TEXT NOT NULL,
    category TEXT,
    volume REAL,
    created_at TEXT,
    end_date TEXT,
    market_price REAL NOT NULL,

    dexter_p REAL,
    dexter_confidence TEXT,
    dexter_reasoning TEXT,
    dexter_key_risk TEXT,
    dexter_base_rate TEXT,

    naked_p REAL,
    naked_reasoning TEXT,
    research_brief_json TEXT,

    forecast_timestamp TEXT NOT NULL,
    is_valid INTEGER NOT NULL DEFAULT 1,

    outcome INTEGER,
    resolved_at TEXT,

    brier_dexter REAL,
    brier_naked REAL,
    brier_market REAL,

    api_cost_usd REAL DEFAULT 0,

    UNIQUE(market_id, forecast_timestamp)
);

`;

const INDEX_SQL = [
  'CREATE INDEX IF NOT EXISTS idx_outcome ON predictions(outcome)',
  'CREATE INDEX IF NOT EXISTS idx_end_date ON predictions(end_date)',
  'CREATE INDEX IF NOT EXISTS idx_forecast_ts ON predictions(forecast_timestamp)',
  'CREATE INDEX IF NOT EXISTS idx_predictions_valid ON predictions(is_valid)',
] as const;

const REQUIRED_MIGRATIONS = [
  {
    column: 'dexter_base_rate',
    sql: 'ALTER TABLE predictions ADD COLUMN dexter_base_rate TEXT',
  },
  {
    column: 'research_brief_json',
    sql: 'ALTER TABLE predictions ADD COLUMN research_brief_json TEXT',
  },
  {
    column: 'is_valid',
    sql: 'ALTER TABLE predictions ADD COLUMN is_valid INTEGER NOT NULL DEFAULT 1',
  },
] as const;

const WATCHLIST_ENGINE_SQL_PATH = path.resolve(import.meta.dir, '../../sql/watchlist_engine.sql');

function runSqlScript(db: Database, sql: string): void {
  for (const statement of sql
    .split(/;\s*(?:\r?\n|$)/g)
    .map((value) => value.trim())
    .filter(Boolean)) {
    db.run(statement);
  }
}

function ensurePredictionMigrations(db: Database): void {
  const columns = db
    .query('PRAGMA table_info(predictions)')
    .all() as Array<{ name: string }>;
  const columnNames = new Set(columns.map((column) => column.name));
  let shouldBackfillValidity = false;

  for (const migration of REQUIRED_MIGRATIONS) {
    if (!columnNames.has(migration.column)) {
      db.run(migration.sql);
      if (migration.column === 'is_valid') {
        shouldBackfillValidity = true;
      }
    }
  }

  if (shouldBackfillValidity) {
    db.run(
      `
        UPDATE predictions
        SET is_valid = CASE
          WHEN forecast_timestamp < ?1 THEN 0
          ELSE COALESCE(is_valid, 1)
        END
      `,
      [INVALID_DATA_CUTOFF],
    );
  }
}

function ensurePredictionIndexes(db: Database): void {
  for (const sql of INDEX_SQL) {
    db.run(sql);
  }
}

function resolveDbPath(): string {
  return process.env.DB_PATH || CONFIG.paths.db;
}

export function ensureWatchlistEngineSchema(db: Database): void {
  const sql = readFileSync(WATCHLIST_ENGINE_SQL_PATH, 'utf8');
  runSqlScript(db, sql);
}

export function initDb(): Database {
  const dbPath = resolveDbPath();
  const dbDir = path.dirname(dbPath);
  mkdirSync(dbDir, { recursive: true });

  const db = new BunDatabase(dbPath, {
    create: true,
    strict: true,
  });
  db.run('PRAGMA foreign_keys = ON;');
  db.run('PRAGMA journal_mode = WAL;');
  db.run(BASE_TABLE_SQL);
  ensurePredictionMigrations(db);
  ensurePredictionIndexes(db);
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);

  return db;
}

export function insertPrediction(db: Database, data: PredictionRow): void {
  const stmt = db.query(`
    INSERT INTO predictions (
      market_id,
      question,
      category,
      volume,
      created_at,
      end_date,
      market_price,
      dexter_p,
      dexter_confidence,
      dexter_reasoning,
      dexter_key_risk,
      dexter_base_rate,
      naked_p,
      naked_reasoning,
      research_brief_json,
      forecast_timestamp,
      api_cost_usd,
      is_valid
    ) VALUES (
      @market_id,
      @question,
      @category,
      @volume,
      @created_at,
      @end_date,
      @market_price,
      @dexter_p,
      @dexter_confidence,
      @dexter_reasoning,
      @dexter_key_risk,
      @dexter_base_rate,
      @naked_p,
      @naked_reasoning,
      @research_brief_json,
      @forecast_timestamp,
      @api_cost_usd,
      @is_valid
    )
    ON CONFLICT(market_id, forecast_timestamp) DO UPDATE SET
      question = excluded.question,
      category = excluded.category,
      volume = excluded.volume,
      created_at = excluded.created_at,
      end_date = excluded.end_date,
      market_price = excluded.market_price,
      dexter_p = excluded.dexter_p,
      dexter_confidence = excluded.dexter_confidence,
      dexter_reasoning = excluded.dexter_reasoning,
      dexter_key_risk = excluded.dexter_key_risk,
      dexter_base_rate = excluded.dexter_base_rate,
      naked_p = excluded.naked_p,
      naked_reasoning = excluded.naked_reasoning,
      research_brief_json = excluded.research_brief_json,
      api_cost_usd = excluded.api_cost_usd,
      is_valid = excluded.is_valid
  `);

  stmt.run({
    ...data,
    category: data.category ?? null,
    dexter_p: data.dexter_p ?? null,
    dexter_confidence: data.dexter_confidence ?? null,
    dexter_reasoning: data.dexter_reasoning ?? null,
    dexter_key_risk: data.dexter_key_risk ?? null,
    dexter_base_rate: data.dexter_base_rate ?? null,
    naked_p: data.naked_p ?? null,
    naked_reasoning: data.naked_reasoning ?? null,
    research_brief_json: data.research_brief_json ?? null,
    is_valid: data.is_valid ?? 1,
  });
}

export function getUnresolved(db: Database): PredictionRow[] {
  const stmt = db.query(`
    SELECT
      market_id,
      question,
      category,
      volume,
      created_at,
      end_date,
      market_price,
      dexter_p,
      dexter_confidence,
      dexter_reasoning,
      dexter_key_risk,
      naked_p,
      naked_reasoning,
      forecast_timestamp,
      api_cost_usd
    FROM predictions
    WHERE outcome IS NULL AND is_valid = 1
    ORDER BY forecast_timestamp ASC
  `);

  return stmt.all() as PredictionRow[];
}

export function updateOutcome(
  db: Database,
  marketId: string,
  outcome: number,
  resolvedAt: string,
): void {
  const stmt = db.query(`
    UPDATE predictions
    SET
      outcome = @outcome,
      resolved_at = @resolved_at,
      brier_dexter = CASE
        WHEN dexter_p IS NOT NULL THEN ((dexter_p / 100.0) - @outcome) * ((dexter_p / 100.0) - @outcome)
        ELSE NULL
      END,
      brier_naked = CASE
        WHEN naked_p IS NOT NULL THEN ((naked_p / 100.0) - @outcome) * ((naked_p / 100.0) - @outcome)
        ELSE NULL
      END,
      brier_market = ((market_price / 100.0) - @outcome) * ((market_price / 100.0) - @outcome)
    WHERE market_id = @market_id AND is_valid = 1
  `);

  stmt.run({
    market_id: marketId,
    outcome,
    resolved_at: resolvedAt,
  });
}

function startOfUtcDay(date: Date): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
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

function getCostInRange(db: Database, start: Date, end: Date): number {
  const stmt = db.query(`
    SELECT COALESCE(SUM(api_cost_usd), 0) AS total
    FROM predictions
    WHERE forecast_timestamp >= ?1 AND forecast_timestamp < ?2 AND is_valid = 1
  `);

  const row = stmt.get(start.toISOString(), end.toISOString()) as { total?: number } | undefined;
  return row?.total ?? 0;
}

export function getTodayCost(db: Database): number {
  const now = new Date();
  return getCostInRange(db, startOfUtcDay(now), startOfNextUtcDay(now));
}

export function getMonthCost(db: Database): number {
  const now = new Date();
  return getCostInRange(db, startOfUtcMonth(now), startOfNextUtcMonth(now));
}
