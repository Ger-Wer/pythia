import sqlite3
import unittest

from python.test_support import seed_prediction, temp_db
from python.watchlist_store import (
    bootstrap_watch_dossier,
    ensure_watchlist_engine_schema,
    list_watch_dossiers,
    list_pending_watch_updates,
    list_watch_updates,
    mark_watch_updates_delivered,
)


class WatchlistStoreTests(unittest.TestCase):
    def test_bootstrap_watch_dossier_reuses_fresh_prediction_snapshot(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-1",
                question="Will Arsenal finish in 2nd place?",
                market_price=12.0,
                dexter_p=30.0,
                naked_p=35.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                bootstrap_watch_dossier(conn, "watch-1", "Следить внимательно", now_iso="2026-04-07T12:00:00Z")
                row = conn.execute(
                    """
                    SELECT market_id, freshness_policy, initial_snapshot_state, thesis_state,
                           current_market_price, current_research_probability
                    FROM watch_dossiers
                    WHERE market_id = 'watch-1'
                    """
                ).fetchone()

            self.assertEqual(row[0], "watch-1")
            self.assertEqual(row[1], "normal_24h")
            self.assertEqual(row[2], "reused")
            self.assertEqual(row[3], "intact")
            self.assertEqual(row[4], 12.0)
            self.assertEqual(row[5], 30.0)

    def test_bootstrap_short_horizon_market_uses_short_policy(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-short",
                question="Will short horizon policy apply?",
                market_price=48.0,
                dexter_p=57.0,
                naked_p=51.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-04-17T12:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                bootstrap_watch_dossier(conn, "watch-short", now_iso="2026-04-07T12:00:00Z")
                row = conn.execute(
                    """
                    SELECT freshness_policy
                    FROM watch_dossiers
                    WHERE market_id = 'watch-short'
                    """
                ).fetchone()

            self.assertEqual(row[0], "short_12h")

    def test_bootstrap_open_position_forces_24h_review_deadline(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-pos",
                question="Will open position force faster review?",
                market_price=41.0,
                dexter_p=59.0,
                naked_p=52.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T12:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                conn.execute(
                    """
                    INSERT INTO signals (
                        prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, entry_zone_min, entry_zone_max,
                        thesis_summary, primary_driver, disconfirming_signal, confidence,
                        research_status, status, created_at, updated_at
                    ) VALUES (
                        1, 'watch-pos', 'Will open position force faster review?', '2026-04-07T06:00:00Z', 41.0,
                        59.0, 52.0, 59.0, 18.0, 18.0, 'watch', 41.0, 57.0,
                        'Тезис ещё жив.', 'Форма команды', 'Ключевая травма', 'medium',
                        'ON', 'new', '2026-04-07T06:00:00Z', '2026-04-07T06:00:00Z'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        signal_id, book_id, market_id, actor_type, decision_type, decision_reason,
                        operator_confidence, confirmation_criteria, invalidation_criteria,
                        review_due_at, status, created_at
                    ) VALUES (
                        1, 2, 'watch-pos', 'operator', 'watch', 'Следим за позицией',
                        'medium', 'Тезис подтверждается', 'Тезис сломан',
                        '2026-04-08T12:00:00Z', 'active', '2026-04-07T12:00:00Z'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO positions (
                        book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
                        entry_yes_price, entry_cost_per_contract, entry_zone_min, entry_zone_max,
                        size_contracts, notional_usd, review_due_at, opened_at, thesis_cycle_number
                    ) VALUES (
                        2, 'watch-pos', 1, 1, 'operator', 'yes', 'open',
                        41.0, 0.41, 41.0, 57.0, 487.8, 200.0, '2026-04-08T12:00:00Z', '2026-04-07T12:00:00Z', 1
                    )
                    """
                )
                bootstrap_watch_dossier(conn, "watch-pos", now_iso="2026-04-07T12:00:00Z")
                row = conn.execute(
                    """
                    SELECT next_review_due_at
                    FROM watch_dossiers
                    WHERE market_id = 'watch-pos'
                    """
                ).fetchone()

            self.assertEqual(row[0], "2026-04-08T12:00:00Z")

    def test_list_watch_dossiers_and_updates_returns_operator_facing_rows(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-2",
                question="Will Burnley finish in last place?",
                market_price=43.6,
                dexter_p=65.0,
                naked_p=68.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                bootstrap_watch_dossier(conn, "watch-2", "Сильный сигнал", now_iso="2026-04-07T12:00:00Z")
                conn.execute(
                    """
                    INSERT INTO watch_refresh_runs (
                        started_at, finished_at, status, markets_total, markets_checked, markets_changed,
                        deep_reviews_triggered, alerts_created, error_summary
                    ) VALUES (?, ?, ?, 1, 1, 1, 0, 1, '')
                    """,
                    ("2026-04-07T12:00:00Z", "2026-04-07T12:05:00Z", "success"),
                )
                conn.execute(
                    """
                    INSERT INTO watch_updates (
                        watch_dossier_id, refresh_run_id, created_at, severity, status_label,
                        reason_summary, action_summary, market_price_before, market_price_after,
                        research_probability_before, research_probability_after, change_summary_json
                    ) VALUES (
                        1, 1, ?, 'review', 'тезис ослаб',
                        'Рынок сдвинулся против тезиса.', 'Открыть review',
                        43.6, 51.0, 65.0, 58.0, '{}'
                    )
                    """,
                    ("2026-04-07T12:05:00Z",),
                )
                conn.commit()

                dossiers = list_watch_dossiers(conn)
                updates = list_watch_updates(conn)

            self.assertEqual(len(dossiers), 1)
            self.assertEqual(dossiers[0]["market_id"], "watch-2")
            self.assertEqual(dossiers[0]["question"], "Will Burnley finish in last place?")
            self.assertEqual(dossiers[0]["operator_note"], "Сильный сигнал")
            self.assertEqual(dossiers[0]["last_status_label"], "без изменений")

            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0]["market_id"], "watch-2")
            self.assertEqual(updates[0]["status_label"], "тезис ослаб")
            self.assertEqual(updates[0]["action_summary"], "Открыть review")

    def test_pending_watch_updates_can_be_marked_delivered(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-3",
                question="Will monitored thesis update be sent?",
                market_price=31.0,
                dexter_p=44.0,
                naked_p=40.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                bootstrap_watch_dossier(conn, "watch-3")
                conn.execute(
                    """
                    INSERT INTO watch_refresh_runs (
                        started_at, finished_at, status, markets_total, markets_checked, markets_changed,
                        deep_reviews_triggered, alerts_created, error_summary
                    ) VALUES ('2026-04-07T12:00:00Z', '2026-04-07T12:05:00Z', 'success', 1, 1, 1, 0, 1, '')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO watch_updates (
                        watch_dossier_id, refresh_run_id, created_at, severity, status_label,
                        reason_summary, action_summary, change_summary_json
                    ) VALUES (
                        1, 1, '2026-04-07T12:10:00Z', 'warning', 'тезис ослаб',
                        'Рынок двинулся против тезиса.', 'Открыть разбор рынка', '{}'
                    )
                    """
                )
                conn.commit()

                pending = list_pending_watch_updates(conn)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["market_id"], "watch-3")

                updated = mark_watch_updates_delivered(conn, [int(pending[0]["id"])], delivered_at="2026-04-07T12:20:00Z")
                self.assertEqual(updated, 1)

                remaining = list_pending_watch_updates(conn)
                self.assertEqual(remaining, [])
