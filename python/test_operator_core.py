import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from python.decision_store import create_manual_decision, ensure_decision_core_schema
from python.operator_core import OperatorCore
from python.test_support import seed_prediction, seed_watch_update, temp_db


class OperatorCoreTests(unittest.TestCase):
    def test_shared_python_fixture_helpers_seed_predictions(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="fixture-market",
                question="Will shared fixtures work?",
                market_price=41.0,
                dexter_p=57.0,
                naked_p=49.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T06:00:00Z",
            )

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT market_id, question, market_price, dexter_p, naked_p, forecast_timestamp
                    FROM predictions
                    WHERE market_id = ?
                    """,
                    ("fixture-market",),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "fixture-market")
            self.assertEqual(row[1], "Will shared fixtures work?")
            self.assertEqual(row[2], 41.0)
            self.assertEqual(row[3], 57.0)
            self.assertEqual(row[4], 49.0)
            self.assertEqual(row[5], "2026-04-07T06:00:00Z")

    def test_database_connections_enable_foreign_keys(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path):
                pass

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir) / "logs")

            with core._connect_rw() as conn:
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

            with core._connect() as conn:
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_add_to_watchlist_creates_dossier_from_fresh_prediction(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-1",
                question="Will Arsenal finish in 2nd place?",
                market_price=12.0,
                dexter_p=30.0,
                naked_p=35.0,
                forecast_timestamp="2026-04-08T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )
            core = OperatorCore(db_path=db_path, log_dir=Path(db_path).parent / "logs")

            core.add_to_watchlist("watch-1", "Следить внимательно", now_iso="2026-04-08T12:00:00Z")

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT market_id, freshness_policy, thesis_state, current_market_price,
                           current_research_probability, current_thesis_summary
                    FROM watch_dossiers
                    WHERE market_id = 'watch-1'
                    """
                ).fetchone()

            self.assertEqual(row[0], "watch-1")
            self.assertEqual(row[1], "normal_24h")
            self.assertEqual(row[2], "intact")
            self.assertEqual(row[3], 12.0)
            self.assertEqual(row[4], 30.0)
            self.assertTrue(row[5])

    def test_add_to_watchlist_marks_pending_deep_review_for_stale_snapshot(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-2",
                question="Will stale snapshot be reused?",
                forecast_timestamp="2026-04-05T00:00:00Z",
                end_date="2026-04-20T12:00:00Z",
            )
            core = OperatorCore(db_path=db_path, log_dir=Path(db_path).parent / "logs")

            core.add_to_watchlist("watch-2", "Нужен автопересмотр")

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT initial_snapshot_state, thesis_state
                    FROM watch_dossiers
                    WHERE market_id = 'watch-2'
                    """
                ).fetchone()

            self.assertEqual(row[0], "pending_deep_review")
            self.assertEqual(row[1], "review_required")

    def test_get_overview_does_not_require_rw_access_for_watchlist_count(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="m1",
                question="Will this work read-only?",
                forecast_timestamp="2026-04-04T08:16:48Z",
                end_date="2026-05-01T00:00:00Z",
            )

            temp_dir = str(Path(db_path).parent)
            os.chmod(db_path, 0o444)
            os.chmod(temp_dir, 0o555)
            try:
                core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir) / "logs")
                overview = core.get_overview()
            finally:
                os.chmod(temp_dir, 0o755)
                os.chmod(db_path, 0o644)

            self.assertEqual(overview["watchlist_count"], 0)

    def test_list_open_positions_stays_read_only_when_decision_core_already_exists(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                ensure_decision_core_schema(conn)
                conn.commit()

            os.chmod(db_path, 0o444)
            os.chmod(temp_dir, 0o555)
            try:
                core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir) / "logs")
                positions = core.list_open_positions()
            finally:
                os.chmod(temp_dir, 0o755)
                os.chmod(db_path, 0o644)

            self.assertEqual(positions, [])

    def test_get_overview_includes_summary_first_contract(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        outcome INTEGER,
                        volume REAL,
                        end_date TEXT,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, forecast_timestamp, outcome, volume, end_date, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "m1",
                            "Will Wolves finish last?",
                            "sports",
                            43.6,
                            65.0,
                            68.0,
                            "medium",
                            "2026-04-04T08:16:48Z",
                            None,
                            10000,
                            "2026-05-01T00:00:00Z",
                            1,
                        ),
                        (
                            "m2",
                            "Will Fed cut rates?",
                            "politics",
                            58.0,
                            41.0,
                            46.0,
                            "low",
                            "2026-04-04T08:16:48Z",
                            1,
                            20000,
                            "2026-06-01T00:00:00Z",
                            1,
                        ),
                        (
                            "m3",
                            "Will BTC hit 150k?",
                            "crypto",
                            31.0,
                            48.0,
                            44.0,
                            "medium",
                            "2026-04-04T08:16:48Z",
                            None,
                            30000,
                            "2026-07-01T00:00:00Z",
                            1,
                        ),
                    ],
                )
                conn.execute(
                    """
                    CREATE TABLE watchlist (
                        market_id TEXT PRIMARY KEY,
                        note TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO watchlist (market_id, note, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("m1", "Проверить после следующего прогона", "2026-04-04T09:00:00Z", "2026-04-04T09:00:00Z"),
                )
                conn.commit()

            (log_dir / "operator_action_status.json").write_text(
                json.dumps(
                    {
                        "action": "forecast",
                        "status": "success",
                        "summary": "Обработано 3 рынка, research ON, 2 сильных расхождения.",
                        "started_at": "2026-04-04T09:10:00Z",
                        "finished_at": "2026-04-04T09:10:40Z",
                        "duration_seconds": 40,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            core = OperatorCore(db_path=db_path, log_dir=log_dir)
            overview = core.get_overview()

            self.assertEqual(overview["resolved_predictions"], 1)
            self.assertEqual(overview["watchlist_count"], 1)
            self.assertEqual(len(overview["top_signals"]), 3)
            self.assertEqual(overview["last_action"]["action"], "forecast")
            self.assertEqual(overview["statuses"]["research"], "ON")
            self.assertIn("next_step", overview)
            self.assertTrue(overview["next_step"])
            self.assertIn("attention_items", overview)
            self.assertGreaterEqual(len(overview["attention_items"]), 1)
            self.assertIn("title", overview["attention_items"][0])
            self.assertIn("detail", overview["attention_items"][0])

    def test_get_overview_daily_digest_is_ranked_and_deterministic(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("m1", "Will strongest lead?", "sports", 43.6, 65.0, 68.0, "medium", "2026-04-04T08:16:48Z", 1),
                        ("m2", "Will mid signal follow?", "macro", 55.0, 44.0, 47.0, "low", "2026-04-04T08:16:48Z", 1),
                        ("m3", "Will second-strongest hold?", "crypto", 31.0, 48.0, 44.0, "medium", "2026-04-04T08:16:48Z", 1),
                        ("m4", "Will small edge stay last?", "other", 40.0, 45.0, 44.0, "low", "2026-04-04T08:16:48Z", 1),
                    ],
                )

            core = OperatorCore(db_path=db_path, log_dir=log_dir)
            overview_first = core.get_overview()
            overview_second = core.get_overview()

            self.assertEqual(overview_first["daily_digest"], overview_second["daily_digest"])
            self.assertIn("headline", overview_first["daily_digest"])
            self.assertIn("summary", overview_first["daily_digest"])
            self.assertIn("items", overview_first["daily_digest"])
            self.assertEqual([item["market_id"] for item in overview_first["top_signals"][:3]], ["m1", "m3", "m2"])
            self.assertEqual([item["market_id"] for item in overview_first["daily_digest"]["items"]], ["m1", "m3", "m2"])
            self.assertLessEqual(len(overview_first["daily_digest"]["items"]), 3)

    def test_get_overview_daily_digest_empty_case_is_reasonable(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)):
                pass
            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir) / "logs")
            overview = core.get_overview()

            self.assertEqual(
                overview["daily_digest"],
                {
                    "headline": "Сегодня Pythia пока не видит сильных сигналов в последнем прогоне.",
                    "summary": "Сначала стоит запустить новый прогон, чтобы появились рынки для ручной проверки.",
                    "items": [],
                },
            )

    def test_get_overview_and_market_details_from_sqlite(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        dexter_reasoning TEXT,
                        dexter_key_risk TEXT,
                        dexter_base_rate TEXT,
                        research_brief_json TEXT,
                        volume REAL,
                        end_date TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        outcome INTEGER,
                        api_cost_usd REAL DEFAULT 0,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                        research_brief_json, volume, end_date, forecast_timestamp, outcome, api_cost_usd, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "572833",
                            "Will Wolves finish in last place in the 2025-26 English Premier League?",
                            "sports",
                            53.0,
                            68.0,
                            58.0,
                            "medium",
                            "Команда выглядит уязвимой относительно рынка.",
                            "Риск внезапного улучшения формы.",
                            "Последние команды лиги часто остаются внизу таблицы.",
                            json.dumps(
                                {
                                    "summary": "Короткая сводка",
                                    "key_facts": ["Wolves идут последними"],
                                    "catalyst_type": "sports_form",
                                    "primary_driver": "Результаты Wolves в оставшихся турах",
                                    "second_order_effects": ["Плохие xG-метрики", "Потеря очков с прямыми конкурентами"],
                                    "disconfirming_signal": "Серия побед Wolves",
                                    "sources": ["https://example.com/wolves"],
                                },
                                ensure_ascii=False,
                            ),
                            28974,
                            "2026-05-27T00:00:00",
                            "2026-04-04T08:16:48.766Z",
                            None,
                            0.0,
                            1,
                        ),
                        (
                            "572833",
                            "Will Wolves finish in last place in the 2025-26 English Premier League?",
                            "sports",
                            49.0,
                            61.0,
                            55.0,
                            "low",
                            "Ранний прогноз по Wolves.",
                            "Слабый ранний тезис.",
                            "Исторически около 40%.",
                            json.dumps(
                                {
                                    "summary": "Ранняя сводка",
                                    "primary_driver": "Старт сезона Wolves",
                                    "second_order_effects": ["Слабый календарь"],
                                    "disconfirming_signal": "Три победы подряд",
                                    "sources": ["https://example.com/early-wolves"],
                                },
                                ensure_ascii=False,
                            ),
                            15000,
                            "2026-05-27T00:00:00",
                            "2026-04-03T08:16:48.766Z",
                            None,
                            0.0,
                            1,
                        ),
                    ],
                )
                conn.execute(
                    """
                    CREATE TABLE watchlist (
                        market_id TEXT PRIMARY KEY,
                        note TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO watchlist (market_id, note, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("572833", "Следить за Wolves", "2026-04-04T09:00:00Z", "2026-04-04T09:00:00Z"),
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            overview = core.get_overview()
            details = core.get_market_details("572833")

            self.assertEqual(overview["valid_predictions"], 2)
            self.assertEqual(overview["latest_run"]["markets_processed"], 1)
            self.assertEqual(overview["top_signals"][0]["confidence"], "medium")
            self.assertEqual(overview["top_signals"][0]["research_status"], "ON")
            self.assertEqual(details["market_id"], "572833")
            self.assertTrue(details["is_watched"])
            self.assertEqual(len(details["history"]), 2)
            self.assertEqual(details["history"][0]["forecast_timestamp"], "2026-04-03T08:16:48.766Z")
            self.assertIn("рынок", details["verdict_summary"].lower())
            self.assertEqual(details["research"]["primary_driver"], "Результаты Wolves в оставшихся турах")
            self.assertEqual(details["research"]["second_order_effects"][0], "Плохие xG-метрики")

    def test_connect_opens_database_read_only(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (market_id, question, forecast_timestamp, is_valid)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("1", "Will this work?", "2026-04-04T00:00:00", 1),
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            with core._connect() as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute(
                        """
                        INSERT INTO predictions (market_id, question, forecast_timestamp, is_valid)
                        VALUES (?, ?, ?, ?)
                        """,
                        ("2", "Should fail", "2026-04-04T00:00:00", 1),
                    )

    def test_get_health_degrades_on_invalid_sqlite_file(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            db_path.write_text("not a sqlite database", encoding="utf-8")

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            health = core.get_health()

            self.assertEqual(health["system_status"], "DEGRADED")
            self.assertTrue(any(check["component"] == "sqlite" for check in health["checks"]))

    def test_get_quality_reports_insufficient_data_honestly(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        forecast_timestamp TEXT NOT NULL,
                        outcome INTEGER,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        forecast_timestamp, outcome, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("m1", "Will this resolve?", "Politics", 52.0, 55.0, 54.0, "2026-04-04T08:16:48Z", None, 1),
                        ("m2", "Will this resolve too?", "Sports", 48.0, 51.0, 50.0, "2026-04-04T08:16:48Z", 1, 1),
                    ],
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            quality = core.get_quality()

            self.assertEqual(quality["resolved_predictions"], 1)
            self.assertEqual(quality["minimum_needed"], 20)
            self.assertEqual(quality["status"], "INSUFFICIENT_DATA")
            self.assertEqual(quality["decision"], "COLLECT")
            self.assertIsInstance(quality["summary"], str)
            self.assertIn("нужно", quality["summary"].lower())
            self.assertEqual(quality["metrics"]["brier_research"], None)
            self.assertEqual(quality["metrics"]["brier_naked"], None)
            self.assertEqual(quality["metrics"]["brier_market"], None)
            self.assertEqual(quality["metrics"]["edge_research_vs_market"], None)
            self.assertEqual(quality["metrics"]["edge_naked_vs_market"], None)
            self.assertIsInstance(quality["category_breakdown"], list)
            self.assertIsNone(quality["calibration_image"])
            self.assertGreaterEqual(len(quality["glossary"]), 1)
            self.assertEqual(quality["glossary"][0]["id"], "brier")

    def test_get_quality_uses_real_log_dir_and_ignores_null_market_price_rows(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "db" / "pythia.db"
            log_dir = Path(temp_dir) / "runtime" / "logs"
            db_path.parent.mkdir(parents=True)
            log_dir.mkdir(parents=True)
            (log_dir / "calibration_20260406.png").write_bytes(b"png")

            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        forecast_timestamp TEXT NOT NULL,
                        outcome INTEGER,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                rows = []
                for index in range(21):
                    rows.append(
                        (
                            f"m{index + 1}",
                            f"Will market {index + 1} resolve?",
                            "Politics" if index < 11 else "Sports",
                            None if index == 0 else (50.0 if index % 2 == 0 else 42.0),
                            58.0 if index % 3 == 0 else 44.0,
                            55.0 if index % 4 == 0 else 46.0,
                            f"2026-04-04T08:16:{index:02d}Z",
                            1 if index % 2 == 0 else 0,
                            1,
                        )
                    )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        forecast_timestamp, outcome, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

            core = OperatorCore(db_path=db_path, log_dir=log_dir)
            quality = core.get_quality()

            self.assertEqual(quality["status"], "COMPLETE")
            self.assertEqual(quality["resolved_predictions"], 21)
            self.assertIsNotNone(quality["metrics"]["brier_market"])
            self.assertEqual(quality["calibration_image"], "logs/calibration_20260406.png")
            self.assertGreaterEqual(len(quality["category_breakdown"]), 1)

    def test_get_last_action_summary_normalizes_partial_payload(self):
        with TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()
            (log_dir / "operator_action_status.json").write_text(
                json.dumps({"action": "forecast"}, ensure_ascii=False),
                encoding="utf-8",
            )

            core = OperatorCore(db_path=Path(temp_dir) / "missing.db", log_dir=log_dir)
            payload = core.get_last_action_summary()

            self.assertEqual(payload["action"], "forecast")
            self.assertEqual(payload["status"], "failed")
            self.assertIn("Не удалось", payload["summary"])
            self.assertEqual(payload["technical_log"], "")

    def test_run_safe_action_preserves_stdout_and_stderr_in_technical_log(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.commit()

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir) / "logs")
            with patch("python.operator_core.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stdout = "stdout line"
                mock_run.return_value.stderr = "stderr line"

                payload = core.run_safe_action("forecast")

            self.assertIn("stdout line", payload["technical_log"])
            self.assertIn("stderr line", payload["technical_log"])

    def test_resolve_market_reference_and_canonical_details_schema(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        dexter_reasoning TEXT,
                        dexter_key_risk TEXT,
                        dexter_base_rate TEXT,
                        research_brief_json TEXT,
                        volume REAL,
                        end_date TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        market_id, question, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, dexter_key_risk,
                        dexter_base_rate, research_brief_json, volume, end_date,
                        forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "111111",
                            "Will ordinal one happen?",
                            40.0,
                            44.0,
                            39.0,
                            "medium",
                            "Первый по порядку валидный прогноз.",
                            "Риск разворота рынка.",
                            "Историческая частота около 40%.",
                            "not-json",
                            1200,
                            "2026-05-01T00:00:00",
                            "2026-03-29T18:00:00",
                            1,
                        ),
                        (
                            "572833",
                            "Will Wolves finish last in the 2025-26 English Premier League?",
                            53.0,
                            68.0,
                            58.0,
                            "medium",
                            "Команда выглядит уязвимой относительно рынка.",
                            "Риск резкого улучшения формы конкурентов.",
                            "Последние команды лиги удерживаются внизу примерно в половине похожих сезонов.",
                            '{"summary":"Sports summary","primary_driver":"Форма Wolves","second_order_effects":["Плохие xG-метрики","Потеря очков с прямыми конкурентами"],"disconfirming_signal":"Серия побед над командами нижней части таблицы","sources":["https://example.com/wolves"]}',
                            25000,
                            "2026-05-20T00:00:00",
                            "2026-04-04T08:16:48.766Z",
                            1,
                        ),
                    ],
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            self.assertEqual(core.resolve_market_reference("572833"), "572833")
            self.assertEqual(core.resolve_market_reference("1"), "572833")
            self.assertEqual(core.resolve_market_reference("2"), "111111")

            details = core.get_market_details("111111")
            self.assertEqual(details["market_id"], "111111")
            self.assertEqual(details["research"]["summary"], "")
            self.assertEqual(details["research"]["key_facts"], [])
            self.assertEqual(details["research"]["sources"], [])
            self.assertNotIn("dexter_p", details)
            self.assertNotIn("naked_p", details)
            self.assertNotIn("dexter_confidence", details)
            self.assertNotIn("dexter_reasoning", details)
            self.assertNotIn("dexter_key_risk", details)
            self.assertNotIn("dexter_base_rate", details)

    def test_watchlist_add_list_remove(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, market_price, dexter_p, naked_p, forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "572834",
                        "Will Burnley finish in last place?",
                        43.6,
                        65.0,
                        68.0,
                        "2026-04-04T08:16:48.766Z",
                        1,
                    ),
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            core.add_to_watchlist("572834", "Сильный сигнал по борьбе за выживание")
            items = core.list_watchlist()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["market_id"], "572834")
            self.assertEqual(items[0]["question"], "Will Burnley finish in last place?")
            self.assertEqual(items[0]["note"], "Сильный сигнал по борьбе за выживание")

            core.remove_from_watchlist("572834")
            self.assertEqual(core.list_watchlist(), [])
            self.assertEqual(core.list_watch_dossiers(), [])

    def test_workspace_and_overview_include_decision_core_entities(self):
        with TemporaryDirectory() as temp_dir:
            from python.decision_store import ensure_decision_core_schema

            db_path = Path(temp_dir) / "pythia.db"
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()

            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        dexter_reasoning TEXT,
                        dexter_key_risk TEXT,
                        dexter_base_rate TEXT,
                        research_brief_json TEXT,
                        volume REAL,
                        end_date TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (
                        id, market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                        research_brief_json, volume, end_date, forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "market-workspace",
                        "Will Arsenal finish in 2nd place?",
                        "sports",
                        12.0,
                        30.0,
                        35.0,
                        "medium",
                        "Арсенал выглядит сильнее рынка.",
                        "Поздний спад формы.",
                        "Исторически около 16%.",
                        '{"summary":"Арсенал в хорошей форме","primary_driver":"Результаты последних матчей Арсенала и его позиция в таблице","second_order_effects":["Осечки конкурентов"],"disconfirming_signal":"Поражение в ключевом матче","sources":["https://example.com/arsenal"]}',
                        54066914,
                        "2026-05-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                        1,
                    ),
                )
                ensure_decision_core_schema(conn)
                conn.execute(
                    """
                    INSERT INTO signals (
                        prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, entry_zone_min, entry_zone_max,
                        thesis_summary, primary_driver, disconfirming_signal, confidence,
                        research_status, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "market-workspace",
                        "Will Arsenal finish in 2nd place?",
                        "2026-04-07T06:00:00Z",
                        12.0,
                        30.0,
                        35.0,
                        30.0,
                        18.0,
                        18.0,
                        "enter_yes",
                        12.0,
                        28.0,
                        "Арсенал выглядит сильнее рынка.",
                        "Результаты последних матчей Арсенала и его позиция в таблице",
                        "Поражение в ключевом матче",
                        "medium",
                        "ON",
                        "new",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO decisions (
                        id, signal_id, book_id, market_id, actor_type, decision_type, decision_reason,
                        operator_confidence, confirmation_criteria, invalidation_criteria,
                        review_due_at, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        2,
                        "market-workspace",
                        "operator",
                        "enter_yes",
                        "Хочу отслеживать рынок как operator paper trade.",
                        "medium",
                        "Арсенал удерживает форму",
                        "Поражение в ключевом матче",
                        "2026-04-08T06:00:00Z",
                        "active",
                        "2026-04-07T06:30:00Z",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO positions (
                        id, book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
                        entry_yes_price, entry_cost_per_contract, entry_zone_min, entry_zone_max,
                        size_contracts, notional_usd, review_due_at, opened_at, thesis_cycle_number
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        2,
                        "market-workspace",
                        1,
                        1,
                        "operator",
                        "yes",
                        "open",
                        12.0,
                        0.12,
                        12.0,
                        28.0,
                        1666.6667,
                        200.0,
                        "2026-04-08T06:00:00Z",
                        "2026-04-07T06:30:00Z",
                        1,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO reviews (
                        id, position_id, market_id, review_type, due_at, status,
                        review_note, resolved_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        "market-workspace",
                        "time_review",
                        "2026-04-08T06:00:00Z",
                        "due",
                        "Нужно проверить тезис после следующего прогона.",
                        None,
                        "2026-04-08T06:00:00Z",
                        "2026-04-08T06:00:00Z",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO postmortems (
                        position_id, market_id, outcome_label, pnl_usd, pnl_percent,
                        forecast_error, entry_error, timing_error, management_error,
                        discipline_error, operator_note, system_note, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "market-workspace",
                        "early_exit",
                        18.5,
                        9.25,
                        0,
                        0,
                        1,
                        0,
                        0,
                        "Закрыл рано после слома тезиса.",
                        "Position closed via thesis_break.",
                        "2026-04-08T06:00:00Z",
                        "2026-04-08T06:00:00Z",
                    ),
                )

            core = OperatorCore(db_path=db_path, log_dir=log_dir)
            overview = core.get_overview()
            workspace = core.get_market_workspace("market-workspace")

            self.assertEqual(overview["open_shadow_positions"], 0)
            self.assertEqual(overview["open_operator_positions"], 1)
            self.assertEqual(overview["due_reviews_count"], 1)

            self.assertEqual(workspace["signal"]["recommended_action"], "enter_yes")
            self.assertEqual(len(workspace["open_positions"]), 1)
            self.assertEqual(workspace["open_positions"][0]["mode"], "operator")
            self.assertEqual(len(workspace["due_reviews"]), 1)
            self.assertEqual(workspace["due_reviews"][0]["status"], "due")
            self.assertEqual(len(workspace["recent_decisions"]), 1)
            self.assertEqual(workspace["recent_decisions"][0]["decision_type"], "enter_yes")
            self.assertEqual(workspace["latest_postmortem"]["outcome_label"], "early_exit")
            self.assertIsNone(workspace["watch_dossier"])
            self.assertEqual(workspace["watch_updates"], [])

    def test_market_workspace_includes_watch_dossier_and_updates(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-workspace",
                question="Will watch workspace include dossier?",
                market_price=22.0,
                dexter_p=37.0,
                naked_p=31.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )
            seed_watch_update(
                db_path,
                market_id="watch-workspace",
                status_label="тезис ослаб",
                reason_summary="Рынок ушёл против тезиса.",
                action_summary="Открыть разбор рынка",
            )

            core = OperatorCore(db_path=db_path, log_dir=Path(db_path).parent / "logs")
            workspace = core.get_market_workspace("watch-workspace")

            self.assertIsNotNone(workspace["watch_dossier"])
            self.assertEqual(workspace["watch_dossier"]["market_id"], "watch-workspace")
            self.assertEqual(workspace["watch_updates"][0]["status_label"], "тезис ослаб")
            self.assertEqual(workspace["watch_updates"][0]["action_summary"], "Открыть разбор рынка")

    def test_overview_and_workspace_include_attention_engine_views(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="attention-workspace",
                question="Will attention be visible in overview?",
                market_price=22.0,
                dexter_p=37.0,
                naked_p=31.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )
            seed_watch_update(
                db_path,
                market_id="attention-workspace",
                status_label="тезис ослаб",
                reason_summary="Рынок ушёл против тезиса.",
                action_summary="Открыть разбор рынка",
            )

            core = OperatorCore(db_path=db_path, log_dir=Path(db_path).parent / "logs")
            overview = core.get_overview()
            workspace = core.get_market_workspace("attention-workspace")
            dossiers = core.list_watch_dossiers()

            self.assertIn("attention_summary", overview)
            self.assertIn("urgent_items", overview)
            self.assertIn("today_items", overview)
            self.assertIn("later_items", overview)
            self.assertIn("watchlist_changed_count", overview)
            grouped_items = overview["urgent_items"] + overview["today_items"] + overview["later_items"]
            self.assertTrue(any(item["market_id"] == "attention-workspace" for item in grouped_items))
            self.assertEqual(workspace["active_attention_items"][0]["market_id"], "attention-workspace")
            self.assertTrue(dossiers[0]["has_open_attention"])
            self.assertEqual(dossiers[0]["attention_priority"], "urgent")

    def test_get_market_workspace_bootstraps_decision_core_on_legacy_db(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        category TEXT,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        dexter_reasoning TEXT,
                        dexter_key_risk TEXT,
                        dexter_base_rate TEXT,
                        research_brief_json TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        outcome INTEGER,
                        volume REAL,
                        end_date TEXT,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                        research_brief_json, forecast_timestamp, outcome, volume, end_date, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-workspace",
                        "Will the workspace boot on a legacy DB?",
                        "tests",
                        45.0,
                        55.0,
                        48.0,
                        "medium",
                        "Legacy reasoning.",
                        "Legacy risk.",
                        "Legacy base rate.",
                        json.dumps({"summary": "Legacy summary"}),
                        "2026-04-07T06:00:00Z",
                        None,
                        1000.0,
                        "2026-05-01T00:00:00Z",
                        1,
                    ),
                )

            core = OperatorCore(db_path=db_path, log_dir=log_dir)
            workspace = core.get_market_workspace("legacy-workspace")

            self.assertEqual(workspace["market"]["market_id"], "legacy-workspace")
            self.assertIsNone(workspace["signal"])
            self.assertEqual(workspace["open_positions"], [])
            self.assertEqual(workspace["due_reviews"], [])
            self.assertEqual(workspace["recent_decisions"], [])
            self.assertIsNone(workspace["latest_postmortem"])

    def test_create_manual_decision_is_atomic_when_open_position_validation_fails(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (market_id, question, forecast_timestamp, is_valid)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("atomic-market", "Will atomicity hold?", "2026-04-07T06:00:00Z", 1),
                )
                ensure_decision_core_schema(conn)
                conn.execute(
                    """
                    INSERT INTO signals (
                        prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, confidence, research_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "atomic-market",
                        "Will atomicity hold?",
                        "2026-04-07T06:00:00Z",
                        51.0,
                        60.0,
                        55.0,
                        60.0,
                        9.0,
                        9.0,
                        "review",
                        "medium",
                        "ON",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
                    ),
                )

                with self.assertRaises(ValueError):
                    create_manual_decision(
                        conn,
                        signal_id=1,
                        decision_type="watch",
                        decision_reason="Trying to open an invalid position.",
                        operator_confidence="medium",
                        confirmation_criteria=None,
                        invalidation_criteria=None,
                        open_position=True,
                        review_due_at=None,
                    )

                decision_count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
                position_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

            self.assertEqual(decision_count, 0)
            self.assertEqual(position_count, 0)


if __name__ == "__main__":
    unittest.main()
