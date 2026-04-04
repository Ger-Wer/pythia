import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from python.operator_core import OperatorCore


class OperatorCoreTests(unittest.TestCase):
    def test_get_overview_and_market_details_from_sqlite(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
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
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                        research_brief_json, volume, end_date, forecast_timestamp, outcome, api_cost_usd, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            overview = core.get_overview()
            details = core.get_market_details("572833")

            self.assertEqual(overview["valid_predictions"], 1)
            self.assertEqual(overview["latest_run"]["markets_processed"], 1)
            self.assertEqual(details["market_id"], "572833")
            self.assertEqual(details["research"]["primary_driver"], "Результаты Wolves в оставшихся турах")
            self.assertEqual(details["research"]["second_order_effects"][0], "Плохие xG-метрики")

    def test_connect_opens_database_read_only(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
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


if __name__ == "__main__":
    unittest.main()
