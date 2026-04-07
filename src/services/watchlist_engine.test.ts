import { expect, test } from 'bun:test';
import { ensureWatchlistEngineSchema } from './db';
import { ensureDecisionCoreSchema } from './decision_core';
import { createTestDatabase, seedPrediction, seedWatchDossier, seedOpenOperatorPosition } from './test_database';

const watchlistEngineModule = import('./watchlist_engine');

test('creates a refresh run, snapshots dossiers, and leaves no operator update when nothing changed', async () => {
  const { runWatchlistRefresh } = await watchlistEngineModule;

  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedPrediction(db, {
    market_id: 'watch-arsenal',
    question: 'Will Arsenal finish in 2nd place?',
    market_price: 12,
    dexter_p: 30,
    naked_p: 35,
    dexter_confidence: 'medium',
    forecast_timestamp: '2026-04-07T06:00:00.000Z',
    end_date: '2026-05-01T12:00:00.000Z',
  });
  seedWatchDossier(db, {
    market_id: 'watch-arsenal',
    current_market_price: 12,
    current_research_probability: 30,
    current_naked_probability: 35,
    current_confidence: 'medium',
    current_research_status: 'ON',
    current_thesis_summary: 'Арсенал стабильно держится в топ-2.',
  });

  const result = await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async () => ({ marketPrice: 12, researchProbability: 30, nakedProbability: 35 }),
    fetchNewsDigest: async () => ({ hits: [], summary: 'Новых значимых фактов нет.' }),
    monitorThesis: async () => ({
      monitoringStatus: 'no_change',
      thesisState: 'intact',
      statusLabel: 'без изменений',
      reasonSummary: 'Цена и факты почти не изменились.',
      actionSummary: 'Ничего не делать.',
      shouldDeepReview: false,
      triggerFlags: {},
      operatorFacing: null,
    }),
    deepReview: async () => {
      throw new Error('should not run');
    },
  });

  expect(result.marketsChecked).toBe(1);
  expect(result.marketsChanged).toBe(0);
  expect(result.alertsCreated).toBe(0);

  const run = db
    .query(
      `
        SELECT status, markets_total, markets_checked, alerts_created
        FROM watch_refresh_runs
        ORDER BY id DESC
        LIMIT 1
      `,
    )
    .get() as { status: string; markets_total: number; markets_checked: number; alerts_created: number } | null;

  const snapshot = db
    .query(
      `
        SELECT monitoring_status, thesis_state, research_status
        FROM watch_refresh_snapshots
        ORDER BY id DESC
        LIMIT 1
      `,
    )
    .get() as { monitoring_status: string; thesis_state: string; research_status: string | null } | null;

  const updates = db.query('SELECT COUNT(*) as count FROM watch_updates').get() as { count: number } | null;

  expect(run).not.toBeNull();
  expect(run?.status).toBe('success');
  expect(run?.markets_total).toBe(1);
  expect(run?.markets_checked).toBe(1);
  expect(snapshot).not.toBeNull();
  expect(snapshot?.monitoring_status).toBe('no_change');
  expect(snapshot?.thesis_state).toBe('intact');
  expect(snapshot?.research_status).toBe('ON');
  expect(updates?.count).toBe(0);
});

test('refreshes open positions before regular watchlist markets', async () => {
  const { runWatchlistRefresh } = await watchlistEngineModule;

  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedPrediction(db, {
    market_id: 'position-first',
    question: 'Will first market refresh first?',
    market_price: 50,
    dexter_p: 55,
    naked_p: 52,
    forecast_timestamp: '2026-04-07T06:00:00.000Z',
    end_date: '2026-05-01T12:00:00.000Z',
  });
  seedPrediction(db, {
    market_id: 'watch-second',
    question: 'Will second market refresh second?',
    market_price: 50,
    dexter_p: 55,
    naked_p: 52,
    forecast_timestamp: '2026-04-07T06:00:00.000Z',
    end_date: '2026-05-01T12:00:00.000Z',
  });
  seedWatchDossier(db, { market_id: 'position-first' });
  seedWatchDossier(db, { market_id: 'watch-second' });
  seedOpenOperatorPosition(db, { market_id: 'position-first' });

  const visited: string[] = [];

  await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async (marketId) => {
      visited.push(marketId);
      return { marketPrice: 50, researchProbability: 55, nakedProbability: 52 };
    },
    fetchNewsDigest: async () => ({ hits: [], summary: 'Нет изменений' }),
    monitorThesis: async () => ({
      monitoringStatus: 'no_change',
      thesisState: 'intact',
      statusLabel: 'без изменений',
      reasonSummary: 'Нет изменений',
      actionSummary: 'Ничего не делать',
      shouldDeepReview: false,
      triggerFlags: {},
      operatorFacing: null,
    }),
    deepReview: async () => {
      throw new Error('unexpected');
    },
  });

  expect(visited[0]).toBe('position-first');
  expect(visited[1]).toBe('watch-second');
});

test('applies deep review results and creates operator-facing update', async () => {
  const { runWatchlistRefresh } = await watchlistEngineModule;

  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedPrediction(db, {
    market_id: 'deep-review-market',
    question: 'Will monitored thesis break?',
    market_price: 20,
    dexter_p: 38,
    naked_p: 31,
    dexter_confidence: 'medium',
    forecast_timestamp: '2026-04-07T06:00:00.000Z',
    end_date: '2026-05-01T12:00:00.000Z',
  });
  seedWatchDossier(db, {
    market_id: 'deep-review-market',
    current_market_price: 20,
    current_research_probability: 38,
    current_naked_probability: 31,
    current_confidence: 'medium',
    current_research_status: 'ON',
    current_thesis_summary: 'Старый тезис.',
  });

  const result = await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async () => ({ marketPrice: 33, researchProbability: null, nakedProbability: null }),
    fetchNewsDigest: async () => ({ hits: [], summary: 'Появились новые риски.' }),
    monitorThesis: async () => ({
      monitoringStatus: 'review_due',
      thesisState: 'review_required',
      statusLabel: 'нужен review',
      reasonSummary: 'Рынок сдвинулся и нужен пересмотр тезиса.',
      actionSummary: 'Запустить deep review.',
      shouldDeepReview: true,
      triggerFlags: { priceMove: true },
      operatorFacing: null,
    }),
    deepReview: async () => ({
      summary: 'Обновлённый тезис ослаб после deep review.',
      primaryDriver: 'Новый фактор',
      invalidationSignal: 'Ещё один негативный сигнал',
      researchProbability: 26,
      nakedProbability: 24,
      confidence: 'low',
      researchStatus: 'ON',
      thesisState: 'weakened',
      nextReviewDueAt: '2026-04-08T12:00:00.000Z',
      operatorFacing: {
        severity: 'warning',
        statusLabel: 'тезис ослаб',
        reasonSummary: 'Deep review снизил вероятность с 38% до 26%.',
        actionSummary: 'Открыть разбор и проверить позицию.',
      },
    }),
  });

  const dossier = db
    .query(
      `
        SELECT thesis_state, current_market_price, current_research_probability, current_naked_probability,
               current_confidence, current_thesis_summary, current_primary_driver, current_invalidation_signal,
               last_deep_review_at, next_review_due_at
        FROM watch_dossiers
        WHERE market_id = 'deep-review-market'
      `,
    )
    .get() as Record<string, string | number | null> | null;

  const update = db
    .query(
      `
        SELECT severity, status_label, reason_summary, action_summary, delivered_to_dashboard_at
        FROM watch_updates
        ORDER BY id DESC
        LIMIT 1
      `,
    )
    .get() as Record<string, string | null> | null;

  expect(result.deepReviewsTriggered).toBe(1);
  expect(result.alertsCreated).toBe(1);
  expect(dossier?.thesis_state).toBe('weakened');
  expect(dossier?.current_market_price).toBe(33);
  expect(dossier?.current_research_probability).toBe(26);
  expect(dossier?.current_naked_probability).toBe(24);
  expect(dossier?.current_confidence).toBe('low');
  expect(dossier?.current_thesis_summary).toBe('Обновлённый тезис ослаб после deep review.');
  expect(dossier?.current_primary_driver).toBe('Новый фактор');
  expect(dossier?.current_invalidation_signal).toBe('Ещё один негативный сигнал');
  expect(dossier?.last_deep_review_at).not.toBeNull();
  expect(dossier?.next_review_due_at).toBe('2026-04-08T12:00:00.000Z');
  expect(update?.severity).toBe('warning');
  expect(update?.status_label).toBe('тезис ослаб');
  expect(update?.action_summary).toBe('Открыть разбор и проверить позицию.');
  expect(update?.delivered_to_dashboard_at).not.toBeNull();
});

test('marks dossier with technical error state when refresh of one market fails', async () => {
  const { runWatchlistRefresh } = await watchlistEngineModule;

  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedPrediction(db, {
    market_id: 'error-market',
    question: 'Will one broken market avoid killing the run?',
    market_price: 44,
    dexter_p: 57,
    naked_p: 52,
    forecast_timestamp: '2026-04-07T06:00:00.000Z',
    end_date: '2026-05-01T12:00:00.000Z',
  });
  seedWatchDossier(db, {
    market_id: 'error-market',
    current_market_price: 44,
    current_research_probability: 57,
    current_naked_probability: 52,
    current_confidence: 'medium',
    current_research_status: 'ON',
    current_thesis_summary: 'Следим за рынком.',
  });

  const result = await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async () => {
      throw new Error('Gamma timeout');
    },
    fetchNewsDigest: async () => ({ hits: [], summary: 'Нет данных' }),
    monitorThesis: async () => {
      throw new Error('unexpected');
    },
    deepReview: async () => {
      throw new Error('unexpected');
    },
  });

  const dossier = db
    .query(
      `
        SELECT status, thesis_state, last_status_label, last_reason_summary, last_action_summary
        FROM watch_dossiers
        WHERE market_id = 'error-market'
      `,
    )
    .get() as Record<string, string | null> | null;

  const run = db
    .query(
      `
        SELECT status, error_summary
        FROM watch_refresh_runs
        ORDER BY id DESC
        LIMIT 1
      `,
    )
    .get() as Record<string, string | null> | null;

  expect(result.marketsChecked).toBe(0);
  expect(dossier?.status).toBe('active_error');
  expect(dossier?.thesis_state).toBe('review_required');
  expect(dossier?.last_status_label).toBe('техническая ошибка');
  expect(dossier?.last_action_summary).toBe('Повторить refresh или открыть рынок вручную.');
  expect(run?.status).toBe('partial_success');
  expect(run?.error_summary).toContain('Gamma timeout');
});
