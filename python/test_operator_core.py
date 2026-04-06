import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from python.operator_core import OperatorCore


class OperatorCoreTests(unittest.TestCase):
    def test_get_overview_does_not_require_rw_access_for_watchlist_count(self):
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
                    ("m1", "Will this work read-only?", "2026-04-04T08:16:48Z", 1),
                )
                conn.commit()

            os.chmod(db_path, 0o444)
            os.chmod(temp_dir, 0o555)
            try:
                core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir) / "logs")
                overview = core.get_overview()
            finally:
                os.chmod(temp_dir, 0o755)
                os.chmod(db_path, 0o644)

            self.assertEqual(overview["watchlist_count"], 0)

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


if __name__ == "__main__":
    unittest.main()
