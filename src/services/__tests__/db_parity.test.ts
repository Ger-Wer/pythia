/**
 * Tests for the parity diff logger (Dual-Loop Phase 1 Track D).
 *
 * Requires a live Postgres shadow (same env vars as `db_pg.test.ts`). If
 * PG is unavailable, tests skip rather than fail — they're not part of the
 * fast unit-test loop.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { Database as BunDatabase } from 'bun:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { sql } from 'drizzle-orm';

const PG_TEST_URL =
  process.env.DUAL_LOOP_PG_TEST_URL ?? process.env.DUAL_LOOP_PG_URL ?? '';

const SKIP = !PG_TEST_URL;
const tempDir = mkdtempSync(path.join(os.tmpdir(), 'pythia-parity-test-'));
const dbPath = path.join(tempDir, 'pythia.db');

// Must be set BEFORE importing db_pg / db_parity (transitively loads src/config).
process.env.DEEPSEEK_API_KEY = 'parity-test';
process.env.DB_PATH = dbPath;

// Lazy handles populated inside beforeAll once env vars are in place.
let shadow: typeof import('../db_pg');
let parity: typeof import('../db_parity');

beforeAll(async () => {
  if (SKIP) {
    // eslint-disable-next-line no-console
    console.warn(
      '[db_parity.test] skipping — no Postgres at DUAL_LOOP_PG_TEST_URL / DUAL_LOOP_PG_URL',
    );
    return;
  }
  shadow = await import('../db_pg');
  parity = await import('../db_parity');
  process.env.DUAL_LOOP_PG_URL = PG_TEST_URL;
  const ok = await shadow.pingPg(PG_TEST_URL);
  if (!ok) {
    throw new Error(`db_parity.test: cannot reach Postgres at ${PG_TEST_URL}`);
  }
  await shadow.runPgMigrations(PG_TEST_URL);
});

afterAll(async () => {
  if (shadow) {
    await shadow.closePgDb();
  }
  rmSync(tempDir, { recursive: true, force: true });
});

function initPrimary(): BunDatabase {
  rmSync(dbPath, { force: true });
  rmSync(`${dbPath}-shm`, { force: true });
  rmSync(`${dbPath}-wal`, { force: true });
  const db = new BunDatabase(dbPath, { create: true, strict: true });
  db.run(`
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
    )
  `);
  return db;
}

function sqliteInsert(
  db: BunDatabase,
  row: {
    market_id: string;
    forecast_timestamp: string;
    market_price: number;
    dexter_p: number | null;
    naked_p: number | null;
    outcome: number | null;
    is_valid: number;
    api_cost_usd: number;
  },
): void {
  db.run(
    `INSERT INTO predictions (market_id, question, market_price, dexter_p, naked_p, forecast_timestamp, is_valid, outcome, api_cost_usd)
     VALUES (?1, 'q', ?2, ?3, ?4, ?5, ?6, ?7, ?8)`,
    [
      row.market_id,
      row.market_price,
      row.dexter_p,
      row.naked_p,
      row.forecast_timestamp,
      row.is_valid,
      row.outcome,
      row.api_cost_usd,
    ],
  );
}

async function pgInsert(row: {
  market_id: string;
  forecast_timestamp: string;
  market_price: number;
  dexter_p: number | null;
  naked_p: number | null;
  outcome: number | null;
  is_valid: number;
  api_cost_usd: number;
}): Promise<void> {
  const handle = shadow.getPgDb(PG_TEST_URL);
  await handle.client`
    INSERT INTO predictions (market_id, question, market_price, dexter_p, naked_p, forecast_timestamp, is_valid, outcome, api_cost_usd)
    VALUES (${row.market_id}, 'q', ${row.market_price}, ${row.dexter_p}, ${row.naked_p}, ${row.forecast_timestamp}, ${row.is_valid}, ${row.outcome}, ${row.api_cost_usd})
  `;
}

beforeEach(async () => {
  if (SKIP) return;
  const handle = shadow.getPgDb(PG_TEST_URL);
  await handle.db.execute(sql`TRUNCATE predictions RESTART IDENTITY CASCADE`);
  parity.resetParityMetrics();
});

describe('db_parity', () => {
  test.skipIf(SKIP)('PARITY OK when primary and shadow match', async () => {
    const db = initPrimary();
    try {
      const row = {
        market_id: 'parity-ok-1',
        forecast_timestamp: '2026-04-22T12:00:00.000Z',
        market_price: 50,
        dexter_p: 60,
        naked_p: 55,
        outcome: null,
        is_valid: 1,
        api_cost_usd: 0.1,
      };
      sqliteInsert(db, row);
      await pgInsert(row);

      const result = await parity.runParitySample({ db, limit: 10 });
      expect(result.diverges).toBe(0);
      expect(result.missingInShadow).toBe(0);
      expect(result.matches).toBe(1);
    } finally {
      db.close();
    }
  });

  test.skipIf(SKIP)(
    'DIVERGE detected when primary and shadow differ on market_price',
    async () => {
      const db = initPrimary();
      try {
        const row = {
          market_id: 'parity-diverge-1',
          forecast_timestamp: '2026-04-22T12:00:00.000Z',
          market_price: 50,
          dexter_p: 60,
          naked_p: 55,
          outcome: null,
          is_valid: 1,
          api_cost_usd: 0.1,
        };
        sqliteInsert(db, row);
        await pgInsert({ ...row, market_price: 51 });

        const result = await parity.runParitySample({ db, limit: 10 });
        expect(result.diverges).toBeGreaterThanOrEqual(1);
        expect(result.divergences.some((d) => d.field === 'market_price')).toBe(true);
      } finally {
        db.close();
      }
    },
  );

  test.skipIf(SKIP)(
    'missingInShadow counted when row exists only in primary',
    async () => {
      const db = initPrimary();
      try {
        const row = {
          market_id: 'parity-missing-1',
          forecast_timestamp: '2026-04-22T12:00:00.000Z',
          market_price: 50,
          dexter_p: 60,
          naked_p: 55,
          outcome: null,
          is_valid: 1,
          api_cost_usd: 0.1,
        };
        sqliteInsert(db, row);

        const result = await parity.runParitySample({ db, limit: 10 });
        expect(result.missingInShadow).toBe(1);
        expect(result.matches).toBe(0);
      } finally {
        db.close();
      }
    },
  );

  test.skipIf(SKIP)(
    'full scan detects shadow rows without a primary counterpart',
    async () => {
      const db = initPrimary();
      try {
        const row = {
          market_id: 'parity-extra-shadow',
          forecast_timestamp: '2026-04-22T12:00:00.000Z',
          market_price: 50,
          dexter_p: 60,
          naked_p: 55,
          outcome: null,
          is_valid: 1,
          api_cost_usd: 0.1,
        };
        await pgInsert(row);

        const result = await parity.runParityFullScan({ db });
        expect(result.missingInPrimary).toBeGreaterThanOrEqual(1);
      } finally {
        db.close();
      }
    },
  );
});
