/**
 * Tests for the Postgres shadow adapter (Dual-Loop Phase 1 Track D).
 *
 * Requires a live Postgres. In CI we run against the `postgres:16-alpine`
 * service on 127.0.0.1:5432 defined in `.github/workflows/dual-loop-ci.yml`.
 * Locally: `make dev-up && bash scripts/pg_migrate.sh`.
 *
 * Tests skip themselves if Postgres is unreachable so the suite still runs
 * against a laptop without the dev stack.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { sql } from 'drizzle-orm';
import * as shadow from '../db_pg';

const PG_TEST_URL =
  process.env.DUAL_LOOP_PG_TEST_URL ?? process.env.DUAL_LOOP_PG_URL ?? '';
const SKIP = !PG_TEST_URL;

beforeAll(async () => {
  if (SKIP) {
    // eslint-disable-next-line no-console
    console.warn(
      '[db_pg.test] skipping — no Postgres at DUAL_LOOP_PG_TEST_URL / DUAL_LOOP_PG_URL',
    );
    return;
  }
  process.env.DUAL_LOOP_PG_URL = PG_TEST_URL;
  const ok = await shadow.pingPg(PG_TEST_URL);
  if (!ok) {
    throw new Error(`db_pg.test: cannot reach Postgres at ${PG_TEST_URL}`);
  }
  // Ensure schema is present. Safe to run repeatedly — drizzle migrator is idempotent.
  await shadow.runPgMigrations(PG_TEST_URL);
});

beforeEach(async () => {
  if (SKIP) return;
  const handle = shadow.getPgDb(PG_TEST_URL);
  await handle.db.execute(sql`TRUNCATE predictions RESTART IDENTITY CASCADE`);
});

afterAll(async () => {
  await shadow.closePgDb();
});

const basePrediction = {
  market_id: 'market-pg-1',
  question: 'Is PG reachable?',
  category: 'tests',
  volume: 100,
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
  api_cost_usd: 0.25,
  is_valid: 1,
};

describe('db_pg adapter', () => {
  test.skipIf(SKIP)('insertPrediction upserts on conflict', async () => {
    await shadow.insertPrediction(basePrediction);

    // Second call with new dexter_p should update, not insert.
    await shadow.insertPrediction({
      ...basePrediction,
      dexter_p: 70,
      api_cost_usd: 0.5,
    });

    const rows = await shadow.getUnresolved();
    expect(rows).toHaveLength(1);
    expect(rows[0]?.dexter_p).toBe(70);
    expect(rows[0]?.api_cost_usd).toBeCloseTo(0.5, 8);
  });

  test.skipIf(SKIP)(
    'updateOutcome writes outcome and brier scores',
    async () => {
      await shadow.insertPrediction(basePrediction);
      await shadow.updateOutcome(
        basePrediction.market_id,
        1,
        '2026-04-24T00:00:00.000Z',
      );

      const handle = shadow.getPgDb(PG_TEST_URL);
      const rows = await handle.db.execute<{
        outcome: number;
        resolved_at: string;
        brier_dexter: number;
        brier_naked: number;
        brier_market: number;
      }>(sql`
        SELECT outcome, resolved_at, brier_dexter, brier_naked, brier_market
        FROM predictions WHERE market_id = ${basePrediction.market_id}
      `);
      const row = Array.from(rows)[0];
      expect(row?.outcome).toBe(1);
      expect(row?.resolved_at).toBe('2026-04-24T00:00:00.000Z');
      // dexter_p = 60 => brier = (0.6 - 1)^2 = 0.16
      expect(row?.brier_dexter).toBeCloseTo(0.16, 8);
      expect(row?.brier_naked).toBeCloseTo(0.2025, 8);
      expect(row?.brier_market).toBeCloseTo(0.25, 8);
    },
  );

  test.skipIf(SKIP)(
    'getTodayCost / getMonthCost reflect inserted costs',
    async () => {
      const now = new Date();
      const stamp = new Date(
        Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 6, 0, 0),
      ).toISOString();
      await shadow.insertPrediction({
        ...basePrediction,
        market_id: 'market-pg-cost',
        forecast_timestamp: stamp,
        api_cost_usd: 1.5,
      });
      const today = await shadow.getTodayCost();
      const month = await shadow.getMonthCost();
      expect(today).toBeCloseTo(1.5, 8);
      expect(month).toBeGreaterThanOrEqual(1.5);
    },
  );

  test.skipIf(SKIP)('pingPg returns true on a live connection', async () => {
    expect(await shadow.pingPg()).toBe(true);
  });
});
