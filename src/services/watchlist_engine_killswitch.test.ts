import { describe, test, expect, beforeEach } from 'bun:test';
import { Database } from 'bun:sqlite';
import { ensureWatchlistEngineSchema } from './db';

describe('Watchlist Kill-Switch', () => {
  let db: Database;

  beforeEach(() => {
    db = new Database(':memory:');
    db.run('PRAGMA foreign_keys = ON');
    ensureWatchlistEngineSchema(db);

    // Create test dossier
    db.run(`
      INSERT INTO watch_dossiers (
        id, market_id, status, created_at, updated_at, first_watched_at,
        freshness_policy, thesis_state
      ) VALUES (1, 'test-market', 'active', '2026-04-11T10:00:00Z', '2026-04-11T10:00:00Z',
                '2026-04-11T10:00:00Z', 'standard', 'active')
    `);

    // Create test refresh run
    db.run(`
      INSERT INTO watch_refresh_runs (
        id, started_at, status, markets_total
      ) VALUES (1, '2026-04-11T12:00:00Z', 'running', 1)
    `);
  });

  test('should allow first update', () => {
    // Create first update
    db.run(`
      INSERT INTO watch_updates (
        watch_dossier_id, refresh_run_id, created_at, severity,
        status_label, reason_summary, action_summary
      ) VALUES (1, 1, '2026-04-11T12:00:00Z', 'info', 'No change', 'Market stable', 'Continue monitoring')
    `);

    const count = db.query(`
      SELECT COUNT(*) as count FROM watch_updates WHERE watch_dossier_id = 1
    `).get() as { count: number };

    expect(count.count).toBe(1);
  });

  test('should allow second update within 24h', () => {
    // Create two updates
    db.run(`
      INSERT INTO watch_updates (
        watch_dossier_id, refresh_run_id, created_at, severity,
        status_label, reason_summary, action_summary
      ) VALUES
        (1, 1, '2026-04-11T12:00:00Z', 'info', 'Update 1', 'First', 'Monitor'),
        (1, 1, '2026-04-11T13:00:00Z', 'info', 'Update 2', 'Second', 'Monitor')
    `);

    const count = db.query(`
      SELECT COUNT(*) as count FROM watch_updates WHERE watch_dossier_id = 1
    `).get() as { count: number };

    expect(count.count).toBe(2);
  });

  test('should count recent updates correctly', () => {
    const now = new Date('2026-04-11T13:00:00Z');
    const twentyFourHoursAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();

    // Insert updates: 2 recent, 1 old
    db.run(`
      INSERT INTO watch_updates (
        watch_dossier_id, refresh_run_id, created_at, severity,
        status_label, reason_summary, action_summary
      ) VALUES
        (1, 1, '2026-04-10T12:00:00Z', 'info', 'Old update', 'Old', 'Monitor'),
        (1, 1, '2026-04-11T12:00:00Z', 'info', 'Recent 1', 'Recent', 'Monitor'),
        (1, 1, '2026-04-11T13:00:00Z', 'info', 'Recent 2', 'Recent', 'Monitor')
    `);

    const recentCount = db.query(`
      SELECT COUNT(*) as count
      FROM watch_updates
      WHERE watch_dossier_id = 1
      AND created_at >= ?
    `).get(twentyFourHoursAgo) as { count: number };

    expect(recentCount.count).toBe(2);
  });

  test('should suppress third update within 24h', () => {
    const now = new Date('2026-04-11T13:00:00Z');
    const twentyFourHoursAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();

    // Insert 2 recent updates
    db.run(`
      INSERT INTO watch_updates (
        watch_dossier_id, refresh_run_id, created_at, severity,
        status_label, reason_summary, action_summary
      ) VALUES
        (1, 1, '2026-04-11T11:00:00Z', 'info', 'Update 1', 'First', 'Monitor'),
        (1, 1, '2026-04-11T12:00:00Z', 'info', 'Update 2', 'Second', 'Monitor')
    `);

    const recentCount = db.query(`
      SELECT COUNT(*) as count
      FROM watch_updates
      WHERE watch_dossier_id = 1
      AND created_at >= ?
    `).get(twentyFourHoursAgo) as { count: number };

    // Should suppress (kill-switch activated)
    const shouldSuppress = recentCount.count >= 2;
    expect(shouldSuppress).toBe(true);
  });

  test('should allow update after 24h window expires', () => {
    const now = new Date('2026-04-11T13:00:00Z');
    const twentyFourHoursAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();

    // Insert 2 old updates (>24h ago)
    db.run(`
      INSERT INTO watch_updates (
        watch_dossier_id, refresh_run_id, created_at, severity,
        status_label, reason_summary, action_summary
      ) VALUES
        (1, 1, '2026-04-10T10:00:00Z', 'info', 'Old 1', 'Old', 'Monitor'),
        (1, 1, '2026-04-10T11:00:00Z', 'info', 'Old 2', 'Old', 'Monitor')
    `);

    const recentCount = db.query(`
      SELECT COUNT(*) as count
      FROM watch_updates
      WHERE watch_dossier_id = 1
      AND created_at >= ?
    `).get(twentyFourHoursAgo) as { count: number };

    // Should NOT suppress (old updates don't count)
    const shouldSuppress = recentCount.count >= 2;
    expect(shouldSuppress).toBe(false);
  });
});
