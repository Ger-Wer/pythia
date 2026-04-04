import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from python.dashboard_api import create_app


class DashboardApiTests(unittest.TestCase):
    def test_login_and_overview_flow(self):
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
                    "INSERT INTO predictions (market_id, question, forecast_timestamp, is_valid) VALUES (?, ?, ?, ?)",
                    ("m1", "Will example happen?", "2026-04-04T08:16:48.766Z", 1),
                )

            app = create_app(
                db_path=db_path,
                log_dir=Path(temp_dir),
                session_secret="test-secret",
                dashboard_username="operator",
                dashboard_password_hash="plain:secret-pass",
            )
            client = TestClient(app)

            login_res = client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})
            self.assertEqual(login_res.status_code, 200)
            overview_res = client.get("/api/overview")
            self.assertEqual(overview_res.status_code, 200)
            self.assertEqual(overview_res.json()["valid_predictions"], 1)

    def test_market_details_and_watchlist_endpoints(self):
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
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, category, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                        research_brief_json, volume, end_date, forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "572833",
                        "Will Wolves finish in last place in the 2025-26 English Premier League?",
                        "sports",
                        53.0,
                        65.0,
                        68.0,
                        "medium",
                        "Детальный разбор.",
                        "Риск разворота формы.",
                        "Исторически около 45%.",
                        '{"summary":"Короткая сводка","primary_driver":"Форма Wolves","second_order_effects":["xG ниже среднего"],"disconfirming_signal":"Серия побед","sources":["https://example.com"]}',
                        28974,
                        "2026-05-27T00:00:00",
                        "2026-04-04T08:16:48.766Z",
                        1,
                    ),
                )

            app = create_app(
                db_path=db_path,
                log_dir=Path(temp_dir),
                session_secret="test-secret",
                dashboard_username="operator",
                dashboard_password_hash="plain:secret-pass",
            )
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

            details = client.get("/api/markets/572833")
            self.assertEqual(details.status_code, 200)
            self.assertEqual(details.json()["market_id"], "572833")

            add_watch = client.post("/api/watchlist", json={"market_id": "572833", "note": "Следить"})
            self.assertEqual(add_watch.status_code, 200)

            watchlist = client.get("/api/watchlist")
            self.assertEqual(watchlist.status_code, 200)
            self.assertEqual(len(watchlist.json()["items"]), 1)
            self.assertEqual(watchlist.json()["items"][0]["market_id"], "572833")
            self.assertEqual(watchlist.json()["items"][0]["note"], "Следить")

            remove_watch = client.delete("/api/watchlist/572833")
            self.assertEqual(remove_watch.status_code, 200)
            self.assertEqual(client.get("/api/watchlist").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
