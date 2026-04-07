import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import requests

from python.decision_store import ensure_decision_core_schema
from python import resolution_checker


class ResolutionCheckerTests(unittest.TestCase):
    @staticmethod
    def _create_predictions_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                question TEXT,
                market_price REAL,
                dexter_p REAL,
                naked_p REAL,
                outcome INTEGER,
                resolved_at TEXT,
                brier_dexter REAL,
                brier_naked REAL,
                brier_market REAL,
                is_valid INTEGER DEFAULT 1
            )
            """
        )

    def test_settle_positions_for_resolution_closes_open_paper_positions(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                self._create_predictions_schema(conn)
                ensure_decision_core_schema(conn)
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, market_price, dexter_p, naked_p, outcome, is_valid
                    ) VALUES (?, ?, ?, ?, ?, NULL, 1)
                    """,
                    ("market-a", "Will market-a resolve YES?", 60.0, 72.0, 65.0),
                )
                prediction_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                book_id = int(conn.execute("SELECT id FROM books WHERE name = 'shadow_default'").fetchone()[0])

                conn.execute(
                    """
                    INSERT INTO signals (
                        prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, entry_zone_min, entry_zone_max,
                        thesis_summary, primary_driver, disconfirming_signal, confidence,
                        research_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prediction_id,
                        "market-a",
                        "Will market-a resolve YES?",
                        "2026-04-07T06:00:00Z",
                        60.0,
                        72.0,
                        65.0,
                        72.0,
                        12.0,
                        12.0,
                        "enter_yes",
                        60.0,
                        70.0,
                        "Strong YES thesis.",
                        "Primary driver",
                        "Disconfirming signal",
                        "medium",
                        "ON",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )
                signal_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO decisions (
                        signal_id, book_id, market_id, actor_type, decision_type,
                        decision_reason, review_due_at, status, created_at
                    ) VALUES (?, ?, ?, 'shadow_engine', 'enter_yes', ?, ?, 'active', ?)
                    """,
                    (
                        signal_id,
                        book_id,
                        "market-a",
                        "Auto-opened shadow position",
                        "2026-04-08T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )
                decision_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO positions (
                        book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
                        entry_yes_price, entry_cost_per_contract, entry_zone_min, entry_zone_max,
                        size_contracts, notional_usd, review_due_at, opened_at, thesis_cycle_number
                    ) VALUES (?, ?, ?, ?, 'shadow', 'yes', 'open', ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        book_id,
                        "market-a",
                        signal_id,
                        decision_id,
                        60.0,
                        0.6,
                        60.0,
                        70.0,
                        100.0,
                        60.0,
                        "2026-04-08T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )
                position_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO reviews (
                        position_id, market_id, review_type, due_at, status,
                        review_note, created_at, updated_at
                    ) VALUES (?, ?, 'time_review', ?, 'due', ?, ?, ?)
                    """,
                    (
                        position_id,
                        "market-a",
                        "2026-04-08T06:00:00Z",
                        "Нужно перепроверить тезис.",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )

                resolution_checker.update_resolved_market(conn, "market-a", 1, "2026-04-09T06:00:00Z")
                resolution_checker.settle_positions_for_resolution(conn, "market-a", 1, "2026-04-09T06:00:00Z")

                position = conn.execute(
                    """
                    SELECT status, close_reason, closed_yes_price, pnl_usd, pnl_percent
                    FROM positions
                    WHERE id = ?
                    """,
                    (position_id,),
                ).fetchone()
                postmortem = conn.execute(
                    """
                    SELECT outcome_label, forecast_error, timing_error, system_note
                    FROM postmortems
                    WHERE position_id = ?
                    """,
                    (position_id,),
                ).fetchone()
                review = conn.execute(
                    "SELECT status FROM reviews WHERE position_id = ?",
                    (position_id,),
                ).fetchone()

            self.assertEqual(position[0], "closed")
            self.assertEqual(position[1], "resolved")
            self.assertEqual(position[2], 100.0)
            self.assertAlmostEqual(position[3], 40.0)
            self.assertAlmostEqual(position[4], 66.66666666666666)
            self.assertEqual(postmortem[0], "yes")
            self.assertEqual(postmortem[1], 0)
            self.assertEqual(postmortem[2], 0)
            self.assertIn("resolved", postmortem[3])
            self.assertEqual(review[0], "cancelled")

    def test_settle_positions_for_resolution_backfills_existing_postmortem_for_early_exit(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                self._create_predictions_schema(conn)
                ensure_decision_core_schema(conn)
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, market_price, dexter_p, naked_p, outcome, is_valid
                    ) VALUES (?, ?, ?, ?, ?, NULL, 1)
                    """,
                    ("market-b", "Will market-b resolve YES?", 44.0, 35.0, 39.0),
                )
                prediction_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                book_id = int(conn.execute("SELECT id FROM books WHERE name = 'operator_default'").fetchone()[0])

                conn.execute(
                    """
                    INSERT INTO signals (
                        prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, thesis_summary, confidence,
                        research_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prediction_id,
                        "market-b",
                        "Will market-b resolve YES?",
                        "2026-04-07T06:00:00Z",
                        44.0,
                        35.0,
                        39.0,
                        35.0,
                        -9.0,
                        9.0,
                        "review",
                        "Weak NO thesis.",
                        "medium",
                        "ON",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )
                signal_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO decisions (
                        signal_id, book_id, market_id, actor_type, decision_type,
                        decision_reason, review_due_at, status, created_at
                    ) VALUES (?, ?, ?, 'operator', 'enter_no', ?, ?, 'active', ?)
                    """,
                    (
                        signal_id,
                        book_id,
                        "market-b",
                        "Manual NO position",
                        "2026-04-08T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )
                decision_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO positions (
                        book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
                        entry_yes_price, entry_cost_per_contract, size_contracts, notional_usd,
                        review_due_at, opened_at, closed_at, closed_yes_price, close_reason,
                        thesis_cycle_number, pnl_usd, pnl_percent
                    ) VALUES (?, ?, ?, ?, 'operator', 'no', 'closed', ?, ?, ?, ?, ?, ?, ?, ?, 'manual_exit', 1, ?, ?)
                    """,
                    (
                        book_id,
                        "market-b",
                        signal_id,
                        decision_id,
                        44.0,
                        0.56,
                        100.0,
                        56.0,
                        "2026-04-08T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T12:00:00Z",
                        48.0,
                        -4.0,
                        -7.142857142857143,
                    ),
                )
                position_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO postmortems (
                        position_id, market_id, outcome_label, pnl_usd, pnl_percent,
                        forecast_error, entry_error, timing_error, management_error,
                        discipline_error, operator_note, system_note, created_at, updated_at
                    ) VALUES (?, ?, 'early_exit', ?, ?, NULL, NULL, 1, 0, 0, ?, ?, ?, ?)
                    """,
                    (
                        position_id,
                        "market-b",
                        -4.0,
                        -7.142857142857143,
                        "Exited too early.",
                        "Position closed via manual_exit.",
                        "2026-04-07T12:00:00Z",
                        "2026-04-07T12:00:00Z",
                    ),
                )

                resolution_checker.update_resolved_market(conn, "market-b", 1, "2026-04-09T06:00:00Z")
                resolution_checker.settle_positions_for_resolution(conn, "market-b", 1, "2026-04-09T06:00:00Z")

                postmortem = conn.execute(
                    """
                    SELECT outcome_label, forecast_error, timing_error, system_note
                    FROM postmortems
                    WHERE position_id = ?
                    """,
                    (position_id,),
                ).fetchone()

            self.assertEqual(postmortem[0], "yes")
            self.assertEqual(postmortem[1], 1)
            self.assertEqual(postmortem[2], 1)
            self.assertIn("resolved", postmortem[3])

    def test_fetch_market_marks_repeated_422_as_invalid(self):
        response = requests.Response()
        response.status_code = 422
        response._content = b'{"error":"unprocessable entity"}'
        http_error = requests.HTTPError(response=response)

        with patch(
            "python.resolution_checker.requests.get",
            side_effect=[http_error, http_error, http_error],
        ):
            market, should_invalidate = resolution_checker.fetch_market("missing-market")

        self.assertIsNone(market)
        self.assertTrue(should_invalidate)

    def test_mark_market_invalid_sets_is_valid_zero(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        outcome INTEGER,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (market_id, outcome, is_valid)
                    VALUES (?, ?, ?)
                    """,
                    [
                        ("market-a", None, 1),
                        ("market-a", None, 1),
                    ],
                )

                resolution_checker.mark_market_invalid(
                    conn, "market-a", "Gamma API returned HTTP 422"
                )

                rows = conn.execute(
                    "SELECT is_valid FROM predictions WHERE market_id = ?",
                    ("market-a",),
                ).fetchall()

            self.assertEqual(rows, [(0,), (0,)])


if __name__ == "__main__":
    unittest.main()
