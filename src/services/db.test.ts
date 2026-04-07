import { Database as BunDatabase } from 'bun:sqlite';
import { afterAll, beforeEach, expect, test } from 'bun:test';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = mkdtempSync(path.join(os.tmpdir(), 'pythia-db-test-'));
const dbPath = path.join(tempDir, 'pythia.db');
const logDir = path.join(tempDir, 'logs');

process.env.DEEPSEEK_API_KEY = 'test-deepseek-key';
process.env.DB_PATH = dbPath;
process.env.LOG_DIR = logDir;

const dbModulePromise = import('./db');

beforeEach(() => {
  rmSync(dbPath, { force: true });
  rmSync(`${dbPath}-shm`, { force: true });
  rmSync(`${dbPath}-wal`, { force: true });
  mkdirSync(logDir, { recursive: true });
});

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

function fixedUtcNoonToday(): Date {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 12, 0, 0));
}

test('initDb creates schema and stores unresolved predictions', async () => {
  const {
    getMonthCost,
    getTodayCost,
    getUnresolved,
    initDb,
    insertPrediction,
  } = await dbModulePromise;

  const forecastedAt = fixedUtcNoonToday();
  const db = initDb();

  try {
    insertPrediction(db, {
      market_id: 'market-init',
      question: 'Will the db smoke test pass?',
      category: 'tests',
      volume: 12_345,
      created_at: forecastedAt.toISOString(),
      end_date: new Date(forecastedAt.getTime() + 86_400_000).toISOString(),
      market_price: 52,
      dexter_p: 57,
      dexter_confidence: 'medium',
      dexter_reasoning: 'Short reasoning.',
      dexter_key_risk: 'A breaking runtime import.',
      naked_p: 54,
      naked_reasoning: 'Baseline reasoning.',
      forecast_timestamp: forecastedAt.toISOString(),
      api_cost_usd: 0.1234,
    });
  } finally {
    db.close();
  }

  const reopened = initDb();
  try {
    const rows = getUnresolved(reopened);

    expect(rows).toHaveLength(1);
    expect(rows[0]?.market_id).toBe('market-init');
    expect(rows[0]?.question).toBe('Will the db smoke test pass?');
    expect(getTodayCost(reopened)).toBeCloseTo(0.1234, 8);
    expect(getMonthCost(reopened)).toBeCloseTo(0.1234, 8);
  } finally {
    reopened.close();
  }
});

test('updateOutcome stores outcome timestamps and brier scores', async () => {
  const {
    initDb,
    insertPrediction,
    updateOutcome,
  } = await dbModulePromise;

  const forecastedAt = fixedUtcNoonToday();
  const resolvedAt = new Date(forecastedAt.getTime() + 30 * 3_600_000).toISOString();
  const db = initDb();
  try {
    insertPrediction(db, {
      market_id: 'market-brier',
      question: 'Will outcome update work?',
      category: 'tests',
      volume: 9_999,
      created_at: forecastedAt.toISOString(),
      end_date: new Date(forecastedAt.getTime() + 86_400_000).toISOString(),
      market_price: 40,
      dexter_p: 70,
      dexter_confidence: 'high',
      dexter_reasoning: 'Dexter thinks yes.',
      dexter_key_risk: 'The migration could be wrong.',
      naked_p: 30,
      naked_reasoning: 'Baseline thinks no.',
      forecast_timestamp: forecastedAt.toISOString(),
      api_cost_usd: 0.2,
    });

    updateOutcome(db, 'market-brier', 1, resolvedAt);
  } finally {
    db.close();
  }

  const reopened = initDb();
  try {
    const row = reopened
      .query(
        `
          SELECT outcome, resolved_at, brier_dexter, brier_naked, brier_market
          FROM predictions
          WHERE market_id = ?1
        `,
      )
      .get('market-brier') as
      | {
          outcome: number;
          resolved_at: string;
          brier_dexter: number;
          brier_naked: number;
          brier_market: number;
        }
      | null;

    expect(row).not.toBeNull();
    expect(row?.outcome).toBe(1);
    expect(row?.resolved_at).toBe(resolvedAt);
    expect(row?.brier_dexter).toBeCloseTo(0.09, 8);
    expect(row?.brier_naked).toBeCloseTo(0.49, 8);
    expect(row?.brier_market).toBeCloseTo(0.36, 8);
  } finally {
    reopened.close();
  }
});

test('initDb migrates legacy schema and backfills validity metadata', async () => {
  const { initDb } = await dbModulePromise;

  const legacyDb = new BunDatabase(dbPath, {
    create: true,
    strict: true,
  });

  try {
    legacyDb.run(`
      CREATE TABLE predictions (
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
        naked_p REAL,
        naked_reasoning TEXT,
        forecast_timestamp TEXT NOT NULL,
        outcome INTEGER,
        resolved_at TEXT,
        brier_dexter REAL,
        brier_naked REAL,
        brier_market REAL,
        api_cost_usd REAL DEFAULT 0,
        UNIQUE(market_id, forecast_timestamp)
      )
    `);

    legacyDb
      .query(`
        INSERT INTO predictions (
          market_id,
          question,
          category,
          volume,
          created_at,
          end_date,
          market_price,
          forecast_timestamp,
          api_cost_usd
        ) VALUES
          ('legacy-old', 'Old run', 'tests', 100, '2026-03-29T13:00:00.000Z', '2026-04-01T00:00:00.000Z', 44, '2026-03-29T14:00:00.000Z', 0),
          ('legacy-new', 'Fresh run', 'tests', 100, '2026-03-29T16:00:00.000Z', '2026-04-01T00:00:00.000Z', 55, '2026-03-29T18:00:00.000Z', 0)
      `)
      .run();
  } finally {
    legacyDb.close();
  }

  const migrated = initDb();
  try {
    const columns = migrated
      .query('PRAGMA table_info(predictions)')
      .all() as Array<{ name: string }>;
    const columnNames = columns.map((column) => column.name);

    expect(columnNames).toContain('is_valid');
    expect(columnNames).toContain('dexter_base_rate');
    expect(columnNames).toContain('research_brief_json');

    const rows = migrated
      .query(`
        SELECT market_id, is_valid
        FROM predictions
        ORDER BY market_id ASC
      `)
      .all() as Array<{ market_id: string; is_valid: number }>;

    expect(rows).toEqual([
      { market_id: 'legacy-new', is_valid: 1 },
      { market_id: 'legacy-old', is_valid: 0 },
    ]);
  } finally {
    migrated.close();
  }
});

test('initDb creates decision-core tables and seeds default books', async () => {
  const { initDb } = await dbModulePromise;

  const db = initDb();
  try {
    const tables = (
      db.query(
        `
          SELECT name
          FROM sqlite_master
          WHERE type = 'table'
          ORDER BY name ASC
        `,
      )
        .all() as Array<{ name: string }>
    ).map((row) => row.name);

    expect(tables).toContain('signals');
    expect(tables).toContain('books');
    expect(tables).toContain('decisions');
    expect(tables).toContain('positions');
    expect(tables).toContain('position_events');
    expect(tables).toContain('reviews');
    expect(tables).toContain('postmortems');

    const books = db
      .query(
        `
          SELECT name, mode, currency, starting_balance, current_balance, max_risk_per_trade_pct, max_total_exposure_pct, status
          FROM books
          ORDER BY name ASC
        `,
      )
      .all() as Array<{
      name: string;
      mode: string;
      currency: string;
      starting_balance: number;
      current_balance: number;
      max_risk_per_trade_pct: number;
      max_total_exposure_pct: number;
      status: string;
    }>;

    expect(books).toEqual([
      {
        name: 'operator_default',
        mode: 'operator',
        currency: 'USDC',
        starting_balance: 10_000,
        current_balance: 10_000,
        max_risk_per_trade_pct: 0.02,
        max_total_exposure_pct: 0.2,
        status: 'active',
      },
      {
        name: 'shadow_default',
        mode: 'shadow',
        currency: 'USDC',
        starting_balance: 10_000,
        current_balance: 10_000,
        max_risk_per_trade_pct: 0.02,
        max_total_exposure_pct: 0.2,
        status: 'active',
      },
    ]);
  } finally {
    db.close();
  }
});
