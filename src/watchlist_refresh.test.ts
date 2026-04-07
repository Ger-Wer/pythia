import { expect, test } from 'bun:test';
import type { MonitoringInput } from './services/watchlist_engine';
import { buildHeuristicMonitoringResult } from './watchlist_refresh';

function monitoringInput(overrides?: Partial<MonitoringInput>): MonitoringInput {
  return {
    dossier: {
      id: 1,
      market_id: 'watch-1',
      question: 'Will the thesis weaken?',
      current_market_price: 20,
      current_research_probability: 38,
      current_naked_probability: 31,
      current_confidence: 'medium',
      current_research_status: 'ON',
      current_thesis_summary: 'Старый тезис.',
      current_primary_driver: 'Старый драйвер',
      current_invalidation_signal: 'Новый риск',
      status: 'active',
      next_review_due_at: '2026-04-08T12:00:00.000Z',
      has_open_position: 0,
    },
    current: {
      marketPrice: 31,
      researchProbability: 26,
      nakedProbability: 24,
    },
    newsDigest: {
      hits: [],
      summary: 'Рынок ушёл против тезиса.',
    },
    ...overrides,
  };
}

test('heuristic monitoring fallback uses refreshed probabilities instead of the stale previous value', () => {
  const result = buildHeuristicMonitoringResult(monitoringInput(), {
    dueReview: false,
    largePriceMove: true,
    hasNews: false,
  });

  expect(result.thesisState).toBe('weakened');
  expect(result.monitoringStatus).toBe('weakened');
  expect(result.statusLabel).toBe('тезис ослаб');
  expect(result.shouldDeepReview).toBe(true);
});
