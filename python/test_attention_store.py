import sqlite3
import unittest

from python.test_support import seed_prediction, temp_db


class AttentionStoreTests(unittest.TestCase):
    def test_sync_attention_items_from_watch_updates_and_due_reviews(self):
        from python.attention_store import (
            ensure_attention_engine_schema,
            list_attention_items,
            sync_attention_items,
        )
        from python.watchlist_store import bootstrap_watch_dossier, ensure_watchlist_engine_schema

        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="attention-1",
                question="Will attention item be created?",
                market_price=41.0,
                dexter_p=58.0,
                naked_p=49.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T06:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                ensure_attention_engine_schema(conn)
                bootstrap_watch_dossier(conn, "attention-1", now_iso="2026-04-07T06:00:00Z")
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
                        1, 1, '2026-04-07T12:10:00Z', 'critical', 'thesis break',
                        'Появился сильный сигнал против тезиса.', 'Открыть разбор рынка', '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO signals (
                        prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, entry_zone_min, entry_zone_max,
                        thesis_summary, primary_driver, disconfirming_signal, confidence,
                        research_status, status, created_at, updated_at
                    ) VALUES (
                        1, 'attention-1', 'Will attention item be created?', '2026-04-07T06:00:00Z', 41.0,
                        58.0, 49.0, 58.0, 17.0, 17.0, 'enter_yes', 41.0, 56.0,
                        'Тезис жив.', 'Форма команды', 'Сильный негативный сигнал', 'medium',
                        'ON', 'new', '2026-04-07T06:00:00Z', '2026-04-07T06:00:00Z'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        id, signal_id, book_id, market_id, actor_type, decision_type, decision_reason,
                        operator_confidence, confirmation_criteria, invalidation_criteria,
                        review_due_at, status, created_at
                    ) VALUES (
                        1, 1, 2, 'attention-1', 'operator', 'enter_yes', 'Открываю позицию',
                        'medium', 'Тезис подтверждается', 'Тезис сломан',
                        '2026-04-08T06:00:00Z', 'active', '2026-04-07T06:30:00Z'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO positions (
                        id, book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
                        entry_yes_price, entry_cost_per_contract, entry_zone_min, entry_zone_max,
                        size_contracts, notional_usd, review_due_at, opened_at, thesis_cycle_number
                    ) VALUES (
                        1, 2, 'attention-1', 1, 1, 'operator', 'yes', 'open',
                        41.0, 0.41, 41.0, 56.0, 487.8, 200.0, '2026-04-08T06:00:00Z', '2026-04-07T06:30:00Z', 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO reviews (
                        id, position_id, market_id, review_type, due_at, status,
                        review_note, resolved_at, created_at, updated_at
                    ) VALUES (
                        1, 1, 'attention-1', 'time_review', '2026-04-07T05:00:00Z', 'due',
                        'Нужно срочно перепроверить тезис.', NULL, '2026-04-07T05:00:00Z', '2026-04-07T05:00:00Z'
                    )
                    """
                )
                conn.commit()

                sync_attention_items(
                    conn,
                    system_status="ON",
                    research_status="ON",
                    research_warning="",
                    now_iso="2026-04-07T12:20:00Z",
                )

                items = list_attention_items(conn)

            self.assertEqual(len(items), 2)
            by_category = {item["category"]: item for item in items}
            self.assertEqual(by_category["thesis_break"]["priority"], "urgent")
            self.assertEqual(by_category["review_due"]["priority"], "urgent")
            self.assertEqual(by_category["thesis_break"]["status"], "new")

    def test_transition_attention_item_records_events_and_updates_status(self):
        from python.attention_store import (
            ensure_attention_engine_schema,
            list_attention_items,
            sync_attention_items,
            transition_attention_item,
        )
        from python.watchlist_store import bootstrap_watch_dossier, ensure_watchlist_engine_schema

        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="attention-2",
                question="Will triage transition work?",
                market_price=22.0,
                dexter_p=35.0,
                naked_p=28.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T06:00:00Z",
            )

            with sqlite3.connect(db_path) as conn:
                ensure_watchlist_engine_schema(conn)
                ensure_attention_engine_schema(conn)
                bootstrap_watch_dossier(conn, "attention-2", now_iso="2026-04-07T06:00:00Z")
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
                        1, 1, '2026-04-07T12:10:00Z', 'review', 'нужен review',
                        'Истёк review window.', 'Открыть review', '{}'
                    )
                    """
                )
                conn.commit()

                sync_attention_items(
                    conn,
                    system_status="ON",
                    research_status="ON",
                    research_warning="",
                    now_iso="2026-04-07T12:20:00Z",
                )
                item_id = list_attention_items(conn)[0]["id"]

                transition_attention_item(conn, item_id, "start", now_iso="2026-04-07T12:21:00Z")
                transition_attention_item(conn, item_id, "bump_later", now_iso="2026-04-07T12:22:00Z")
                result = transition_attention_item(conn, item_id, "close", now_iso="2026-04-07T12:23:00Z")

                row = conn.execute(
                    "SELECT status, priority FROM attention_items WHERE id = ?",
                    (item_id,),
                ).fetchone()
                events = conn.execute(
                    "SELECT action FROM attention_item_events WHERE attention_item_id = ? ORDER BY id ASC",
                    (item_id,),
                ).fetchall()

            self.assertEqual(result["status"], "closed")
            self.assertEqual(row[0], "closed")
            self.assertEqual(row[1], "later")
            self.assertEqual([event[0] for event in events], ["create", "start", "bump_later", "close"])
