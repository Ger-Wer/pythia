import { afterAll, beforeEach, expect, test } from 'bun:test';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  createTestDatabase,
  seedOpenOperatorPosition,
  seedPrediction,
  seedWatchDossier,
} from './test_database';

const tempDir = mkdtempSync(path.join(os.tmpdir(), 'pythia-decision-core-test-'));
const dbPath = path.join(tempDir, 'pythia.db');
const logDir = path.join(tempDir, 'logs');

process.env.DEEPSEEK_API_KEY = 'test-deepseek-key';
process.env.DB_PATH = dbPath;
process.env.LOG_DIR = logDir;

const dbModulePromise = import('./db');
const decisionCoreModulePromise = import('./decision_core');

beforeEach(() => {
  rmSync(dbPath, { force: true });
  rmSync(`${dbPath}-shm`, { force: true });
  rmSync(`${dbPath}-wal`, { force: true });
  mkdirSync(logDir, { recursive: true });
});

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

test('shared test database helpers seed dossier and open position scaffolding', () => {
  const db = createTestDatabase();

  try {
    seedWatchDossier(db, {
      market_id: 'watch-helper-market',
      thesis_state: 'weakened',
      current_market_price: 33,
      current_research_probability: 41,
      current_naked_probability: 39,
      current_confidence: 'medium',
      current_research_status: 'ON',
      current_thesis_summary: 'The thesis weakened a bit.',
    });
    seedOpenOperatorPosition(db, { market_id: 'watch-helper-market' });

    const dossier = db
      .query(
        `
          SELECT market_id, thesis_state, current_market_price, current_research_probability
          FROM watch_dossiers
          WHERE market_id = ?1
        `,
      )
      .get('watch-helper-market') as
      | {
          market_id: string;
          thesis_state: string;
          current_market_price: number;
          current_research_probability: number;
        }
      | null;

    const position = db
      .query(
        `
          SELECT market_id, mode, status, side
          FROM positions
          WHERE market_id = ?1
        `,
      )
      .get('watch-helper-market') as
      | {
          market_id: string;
          mode: string;
          status: string;
          side: string;
        }
      | null;

    expect(dossier).not.toBeNull();
    expect(dossier?.market_id).toBe('watch-helper-market');
    expect(dossier?.thesis_state).toBe('weakened');
    expect(position).not.toBeNull();
    expect(position?.mode).toBe('operator');
    expect(position?.status).toBe('open');
    expect(position?.side).toBe('yes');
  } finally {
    db.close();
  }
});

test('syncDecisionCoreForForecastRun creates signals and auto-opens a shadow position for a strong research signal', async () => {
  const { initDb } = await dbModulePromise;
  const { syncDecisionCoreForForecastRun } = await decisionCoreModulePromise;

  const db = initDb();
  const forecastTimestamp = '2026-04-07T06:00:00.000Z';

  try {
    seedPrediction(db, {
      market_id: 'market-shadow-open',
      question: 'Will Arsenal finish in 2nd place?',
      category: 'sports',
      volume: 54_000,
      created_at: '2026-04-06T10:00:00.000Z',
      end_date: '2026-05-07T06:00:00.000Z',
      market_price: 12,
      dexter_p: 30,
      dexter_confidence: 'medium',
      dexter_reasoning: 'Pythia likes Arsenal more than the market.',
      dexter_key_risk: 'Late-season collapse.',
      dexter_base_rate: 'Arsenal finish 2nd in roughly 16% of comparable seasons.',
      naked_p: 35,
      naked_reasoning: 'Baseline is also above market.',
      research_brief_json: JSON.stringify({
        summary: 'Arsenal remain in strong form.',
        primary_driver: 'Результаты последних матчей Арсенала и его позиция в таблице',
        second_order_effects: ['Форма конкурентов ухудшилась'],
        disconfirming_signal: 'Поражение Арсенала в ключевом матче',
        sources: ['https://example.com/arsenal'],
      }),
      forecast_timestamp: forecastTimestamp,
      api_cost_usd: 0,
      is_valid: 1,
    });

    syncDecisionCoreForForecastRun(db, forecastTimestamp);

    const signal = db
      .query(
        `
          SELECT market_id, signal_strength, recommended_action, entry_zone_min, entry_zone_max, confidence, research_status, thesis_summary, primary_driver
          FROM signals
          WHERE market_id = ?1
        `,
      )
      .get('market-shadow-open') as
      | {
          market_id: string;
          signal_strength: number;
          recommended_action: string;
          entry_zone_min: number;
          entry_zone_max: number;
          confidence: string;
          research_status: string;
          thesis_summary: string;
          primary_driver: string;
        }
      | null;

    expect(signal).not.toBeNull();
    expect(signal?.market_id).toBe('market-shadow-open');
    expect(signal?.signal_strength).toBe(18);
    expect(signal?.recommended_action).toBe('enter_yes');
    expect(signal?.entry_zone_min).toBe(12);
    expect(signal?.entry_zone_max).toBe(28);
    expect(signal?.confidence).toBe('medium');
    expect(signal?.research_status).toBe('ON');
    expect(signal?.thesis_summary).toContain('Arsenal');
    expect(signal?.primary_driver).toContain('Арсенала');

    const shadowDecision = db
      .query(
        `
          SELECT actor_type, decision_type, decision_reason
          FROM decisions
          WHERE market_id = ?1
        `,
      )
      .get('market-shadow-open') as
      | {
          actor_type: string;
          decision_type: string;
          decision_reason: string;
        }
      | null;

    expect(shadowDecision).not.toBeNull();
    expect(shadowDecision?.actor_type).toBe('shadow_engine');
    expect(shadowDecision?.decision_type).toBe('enter_yes');
    expect(shadowDecision?.decision_reason).toContain('auto-open');

    const position = db
      .query(
        `
          SELECT mode, side, status, entry_yes_price, entry_cost_per_contract, notional_usd, size_contracts, thesis_cycle_number
          FROM positions
          WHERE market_id = ?1
        `,
      )
      .get('market-shadow-open') as
      | {
          mode: string;
          side: string;
          status: string;
          entry_yes_price: number;
          entry_cost_per_contract: number;
          notional_usd: number;
          size_contracts: number;
          thesis_cycle_number: number;
        }
      | null;

    expect(position).not.toBeNull();
    expect(position?.mode).toBe('shadow');
    expect(position?.side).toBe('yes');
    expect(position?.status).toBe('open');
    expect(position?.entry_yes_price).toBe(12);
    expect(position?.entry_cost_per_contract).toBeCloseTo(0.12, 8);
    expect(position?.notional_usd).toBe(200);
    expect(position?.size_contracts).toBeCloseTo(1666.6666667, 6);
    expect(position?.thesis_cycle_number).toBe(1);

    const events = db
      .query(
        `
          SELECT event_type
          FROM position_events
          ORDER BY id ASC
        `,
      )
      .all() as Array<{ event_type: string }>;

    expect(events).toEqual([{ event_type: 'opened' }]);
  } finally {
    db.close();
  }
});

test('syncDecisionCoreForForecastRun marks an open shadow position as thesis_break when the refreshed signal invalidates it', async () => {
  const { initDb } = await dbModulePromise;
  const { syncDecisionCoreForForecastRun } = await decisionCoreModulePromise;

  const db = initDb();
  const firstForecast = '2026-04-07T06:00:00.000Z';
  const secondForecast = '2026-04-08T06:00:00.000Z';

  try {
    seedPrediction(db, {
      market_id: 'market-shadow-break',
      question: 'Will Example Market stay strong?',
      category: 'tests',
      volume: 25_000,
      created_at: '2026-04-06T10:00:00.000Z',
      end_date: '2026-05-07T06:00:00.000Z',
      market_price: 20,
      dexter_p: 42,
      dexter_confidence: 'high',
      dexter_reasoning: 'Initial thesis is strong.',
      dexter_key_risk: 'Momentum reverses.',
      dexter_base_rate: 'Historical base rate.',
      naked_p: 39,
      naked_reasoning: 'Baseline supports it.',
      research_brief_json: JSON.stringify({
        summary: 'Initial summary.',
        primary_driver: 'Strong catalyst',
        second_order_effects: ['Secondary support'],
        disconfirming_signal: 'Catalyst fades',
        sources: ['https://example.com/one'],
      }),
      forecast_timestamp: firstForecast,
      api_cost_usd: 0,
      is_valid: 1,
    });

    syncDecisionCoreForForecastRun(db, firstForecast);

    seedPrediction(db, {
      market_id: 'market-shadow-break',
      question: 'Will Example Market stay strong?',
      category: 'tests',
      volume: 25_000,
      created_at: '2026-04-06T10:00:00.000Z',
      end_date: '2026-05-07T06:00:00.000Z',
      market_price: 28,
      dexter_p: 30,
      dexter_confidence: 'medium',
      dexter_reasoning: 'The thesis weakened sharply.',
      dexter_key_risk: 'None.',
      dexter_base_rate: 'Historical base rate.',
      naked_p: 31,
      naked_reasoning: 'Baseline also cooled.',
      research_brief_json: JSON.stringify({
        summary: 'Follow-up summary.',
        primary_driver: 'Catalyst faded',
        second_order_effects: ['Momentum gone'],
        disconfirming_signal: 'Momentum gone',
        sources: ['https://example.com/two'],
      }),
      forecast_timestamp: secondForecast,
      api_cost_usd: 0,
      is_valid: 1,
    });

    syncDecisionCoreForForecastRun(db, secondForecast);

    const position = db
      .query(
        `
          SELECT status, close_reason, closed_yes_price
          FROM positions
          WHERE market_id = ?1
          ORDER BY id DESC
          LIMIT 1
        `,
      )
      .get('market-shadow-break') as
      | {
          status: string;
          close_reason: string;
          closed_yes_price: number;
        }
      | null;

    expect(position).not.toBeNull();
    expect(position?.status).toBe('closed');
    expect(position?.close_reason).toBe('thesis_break');
    expect(position?.closed_yes_price).toBe(28);

    const events = db
      .query(
        `
          SELECT event_type
          FROM position_events
          WHERE position_id = (
            SELECT id FROM positions WHERE market_id = ?1 ORDER BY id DESC LIMIT 1
          )
          ORDER BY id ASC
        `,
      )
      .all('market-shadow-break') as Array<{ event_type: string }>;

    expect(events).toEqual([
      { event_type: 'opened' },
      { event_type: 'closed' },
    ]);

    const postmortem = db
      .query(
        `
          SELECT timing_error, management_error, system_note
          FROM postmortems
          WHERE market_id = ?1
        `,
      )
      .get('market-shadow-break') as
      | {
          timing_error: number | null;
          management_error: number | null;
          system_note: string | null;
        }
      | null;

    expect(postmortem).not.toBeNull();
    expect(postmortem?.timing_error).toBe(1);
    expect(postmortem?.management_error).toBe(0);
    expect(postmortem?.system_note).toContain('thesis_break');
  } finally {
    db.close();
  }
});
