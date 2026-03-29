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
