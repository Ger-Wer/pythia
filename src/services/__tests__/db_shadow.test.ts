/**
 * Tests for the shadow-write wrapper (Dual-Loop Phase 1 Track D).
 *
 * These tests run without Postgres — the shadow target is swapped for an
 * in-process stub via `__setShadowForTests`, so we can verify:
 *   1. When the flag is off, shadow code does not fire at all.
 *   2. When the flag is on, primary writes always succeed even if the
 *      shadow throws.
 *   3. Error logs land in `logs/shadow_pg_errors.log` (NDJSON).
 */
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = mkdtempSync(path.join(os.tmpdir(), 'pythia-shadow-test-'));
const dbPath = path.join(tempDir, 'pythia.db');
const logDir = path.join(tempDir, 'logs');

process.env.DEEPSEEK_API_KEY = 'test-shadow';
process.env.DB_PATH = dbPath;
process.env.LOG_DIR = logDir;

interface StubState {
  insertCalls: number;
  updateCalls: number;
  insertThrows: boolean;
  updateThrows: boolean;
}

const stub: StubState = {
  insertCalls: 0,
  updateCalls: 0,
  insertThrows: false,
  updateThrows: false,
};

let shadowModule: typeof import('../db_shadow');

beforeAll(async () => {
  shadowModule = await import('../db_shadow');
  shadowModule.__setShadowForTests({
    insertPrediction: async () => {
      stub.insertCalls += 1;
      if (stub.insertThrows) throw new Error('shadow offline');
    },
    updateOutcome: async () => {
      stub.updateCalls += 1;
      if (stub.updateThrows) throw new Error('shadow offline');
    },
    pingPg: async () => true,
  });
});

afterAll(() => {
  shadowModule.__setShadowForTests(null);
  rmSync(tempDir, { recursive: true, force: true });
});

beforeEach(() => {
  rmSync(dbPath, { force: true });
  rmSync(`${dbPath}-shm`, { force: true });
  rmSync(`${dbPath}-wal`, { force: true });
  rmSync(logDir, { recursive: true, force: true });
  delete process.env.DUAL_LOOP_SHADOW_PG;
  stub.insertCalls = 0;
  stub.updateCalls = 0;
  stub.insertThrows = false;
  stub.updateThrows = false;
  shadowModule.resetShadowPgMetrics();
});

afterEach(() => {
  delete process.env.DUAL_LOOP_SHADOW_PG;
});

const basePrediction = {
  market_id: 'market-shadow',
  question: 'Is shadow-write zero-impact?',
  category: 'tests',
  volume: 1,
  created_at: '2026-04-22T00:00:00.000Z',
  end_date: '2026-04-23T00:00:00.000Z',
  market_price: 50,
  dexter_p: 60,
  dexter_confidence: 'medium',
  dexter_reasoning: 'r',
  dexter_key_risk: 'risk',
  naked_p: 55,
  naked_reasoning: 'n',
  forecast_timestamp: '2026-04-22T12:00:00.000Z',
  api_cost_usd: 0.05,
  is_valid: 1,
};

test('shadow flag off is a zero-impact re-export of ./db', async () => {
  const db = shadowModule.initDb();
  try {
    shadowModule.insertPrediction(db, basePrediction);
  } finally {
    db.close();
  }
  await new Promise<void>((resolve) => setTimeout(resolve, 50));
  expect(stub.insertCalls).toBe(0);
  expect(existsSync(path.join(logDir, 'shadow_pg_errors.log'))).toBe(false);
});

test('shadow flag on: primary write still succeeds when shadow throws, error is logged', async () => {
  process.env.DUAL_LOOP_SHADOW_PG = 'true';
  stub.insertThrows = true;

  const db = shadowModule.initDb();
  try {
    expect(() =>
      shadowModule.insertPrediction(db, basePrediction),
    ).not.toThrow();

    const row = db
      .query('SELECT market_id FROM predictions WHERE market_id = ?1')
      .get(basePrediction.market_id) as { market_id: string } | null;
    expect(row?.market_id).toBe(basePrediction.market_id);
  } finally {
    db.close();
  }

  await new Promise<void>((resolve) => setTimeout(resolve, 100));

  expect(stub.insertCalls).toBeGreaterThanOrEqual(1);
  const metrics = shadowModule.getShadowPgMetrics();
  expect(metrics.shadow_pg_errors_total.insertPrediction ?? 0).toBeGreaterThanOrEqual(1);

  const logPath = path.join(logDir, 'shadow_pg_errors.log');
  expect(existsSync(logPath)).toBe(true);
  const content = readFileSync(logPath, 'utf8').trim();
  expect(content.length).toBeGreaterThan(0);
  const parsed = JSON.parse(content.split('\n').pop() as string);
  expect(parsed.operation).toBe('insertPrediction');
  expect(parsed.error).toContain('shadow offline');
});

test('updateOutcome fan-out increments writes counter on shadow success', async () => {
  process.env.DUAL_LOOP_SHADOW_PG = 'true';

  const db = shadowModule.initDb();
  try {
    shadowModule.insertPrediction(db, basePrediction);
    shadowModule.updateOutcome(
      db,
      basePrediction.market_id,
      1,
      '2026-04-24T00:00:00.000Z',
    );
  } finally {
    db.close();
  }

  await new Promise<void>((resolve) => setTimeout(resolve, 100));
  expect(stub.updateCalls).toBeGreaterThanOrEqual(1);
  const metrics = shadowModule.getShadowPgMetrics();
  expect(metrics.shadow_pg_writes_total.updateOutcome ?? 0).toBeGreaterThanOrEqual(1);
});
