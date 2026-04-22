/**
 * Integration tests for the LISTEN/NOTIFY publisher (Dual-Loop Phase 1B).
 *
 * These run only when Postgres is reachable (CI `shadow-pg-tests` service).
 * Without PG the suite skips rather than fails — consistent with the other
 * Track D test files.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { sql } from 'drizzle-orm';
import * as shadow from '../db_pg';
import {
  DUAL_LOOP_CHANNELS,
  PgListener,
  type DualLoopEvent,
} from '../pg_listener';

const PG_TEST_URL =
  process.env.DUAL_LOOP_PG_TEST_URL ?? process.env.DUAL_LOOP_PG_URL ?? '';
const SKIP = !PG_TEST_URL;

const basePrediction = {
  market_id: 'market-notify-1',
  question: 'Does pg_notify fire on insert?',
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
  api_cost_usd: 0.1,
  is_valid: 1,
};

beforeAll(async () => {
  if (SKIP) {
    // eslint-disable-next-line no-console
    console.warn('[pg_listener.test] skipping — no Postgres URL set');
    return;
  }
  process.env.DUAL_LOOP_PG_URL = PG_TEST_URL;
  const ok = await shadow.pingPg(PG_TEST_URL);
  if (!ok) {
    throw new Error(`pg_listener.test: cannot reach Postgres at ${PG_TEST_URL}`);
  }
  await shadow.runPgMigrations(PG_TEST_URL);
});

afterAll(async () => {
  await shadow.closePgDb();
});

beforeEach(async () => {
  if (SKIP) return;
  await shadow.closePgDb();
  const handle = shadow.getPgDb(PG_TEST_URL);
  await handle.db.execute(sql`TRUNCATE predictions RESTART IDENTITY CASCADE`);
});

/**
 * Handler that buffers events and lets the caller await the Nth match. The
 * handler must be registered BEFORE the triggering SQL statement — NOTIFY
 * delivery is best-effort and events dispatched prior to `on()` are lost.
 */
function createCollector(
  listener: PgListener,
  matcher: (event: DualLoopEvent) => boolean,
) {
  const matches: DualLoopEvent[] = [];
  listener.on((event) => {
    if (matcher(event)) matches.push(event);
  });
  return {
    matches,
    async waitFor(count: number, timeoutMs: number): Promise<void> {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline && matches.length < count) {
        await new Promise<void>((resolve) => setTimeout(resolve, 25));
      }
    },
  };
}

describe('pg_listener / NOTIFY triggers', () => {
  test.skipIf(SKIP)(
    'insert on predictions fires dual_loop.predictions NOTIFY when GUC is on',
    async () => {
      const listener = new PgListener({ url: PG_TEST_URL });
      await listener.start();

      try {
        const collector = createCollector(
          listener,
          (e) =>
            e.table === 'predictions' &&
            e.op === 'insert' &&
            e.market_id === basePrediction.market_id,
        );

        process.env.DUAL_LOOP_PG_NOTIFY = 'true';
        await shadow.closePgDb();
        await shadow.insertPrediction(basePrediction, PG_TEST_URL);

        await collector.waitFor(1, 2000);
        expect(collector.matches.length).toBeGreaterThanOrEqual(1);
        const event = collector.matches[0];
        expect(event.v).toBe(1);
        expect(event.pk).toEqual({ id: expect.any(Number) });
        expect(event.ts).toMatch(
          /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/,
        );
      } finally {
        delete process.env.DUAL_LOOP_PG_NOTIFY;
        await listener.stop();
      }
    },
  );

  test.skipIf(SKIP)(
    'when GUC is off (default), no NOTIFY is emitted',
    async () => {
      const listener = new PgListener({ url: PG_TEST_URL });
      await listener.start();

      try {
        const collector = createCollector(
          listener,
          (e) => e.market_id === 'market-notify-off',
        );

        delete process.env.DUAL_LOOP_PG_NOTIFY;
        await shadow.closePgDb();
        await shadow.insertPrediction(
          { ...basePrediction, market_id: 'market-notify-off' },
          PG_TEST_URL,
        );

        // Give Postgres and the listener ample time to deliver a spurious event.
        await new Promise<void>((resolve) => setTimeout(resolve, 500));
        expect(collector.matches).toHaveLength(0);
      } finally {
        await listener.stop();
      }
    },
  );

  test.skipIf(SKIP)(
    'update + delete produce op=update / op=delete envelopes',
    async () => {
      const listener = new PgListener({ url: PG_TEST_URL });
      await listener.start();

      try {
        const updateCollector = createCollector(
          listener,
          (e) =>
            e.table === 'predictions' &&
            e.op === 'update' &&
            e.market_id === basePrediction.market_id,
        );
        const deleteCollector = createCollector(
          listener,
          (e) =>
            e.table === 'predictions' &&
            e.op === 'delete' &&
            e.market_id === basePrediction.market_id,
        );

        process.env.DUAL_LOOP_PG_NOTIFY = 'true';
        await shadow.closePgDb();
        await shadow.insertPrediction(basePrediction, PG_TEST_URL);
        await shadow.updateOutcome(
          basePrediction.market_id,
          1,
          '2026-04-24T00:00:00.000Z',
          PG_TEST_URL,
        );

        await updateCollector.waitFor(1, 2000);
        expect(updateCollector.matches.length).toBeGreaterThanOrEqual(1);

        const handle = shadow.getPgDb(PG_TEST_URL);
        await handle.db.execute(
          sql`DELETE FROM predictions WHERE market_id = ${basePrediction.market_id}`,
        );

        await deleteCollector.waitFor(1, 2000);
        expect(deleteCollector.matches.length).toBeGreaterThanOrEqual(1);
      } finally {
        delete process.env.DUAL_LOOP_PG_NOTIFY;
        await listener.stop();
      }
    },
  );

  test.skipIf(SKIP)('exports stable channel list', () => {
    expect(DUAL_LOOP_CHANNELS).toContain('dual_loop.predictions');
    expect(DUAL_LOOP_CHANNELS).toContain('dual_loop.signals');
    expect(DUAL_LOOP_CHANNELS).toContain('dual_loop.decisions');
    expect(DUAL_LOOP_CHANNELS).toContain('dual_loop.attention_items');
    expect(DUAL_LOOP_CHANNELS).toContain('dual_loop.watch_dossiers');
    expect(DUAL_LOOP_CHANNELS).toContain('dual_loop.watch_updates');
  });
});
