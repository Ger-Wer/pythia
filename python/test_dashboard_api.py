import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from python.dashboard_api import create_app
from python.test_support import create_test_app, seed_prediction, seed_watch_update, temp_db


class DashboardApiTests(unittest.TestCase):
    def test_shared_dashboard_fixture_helpers_create_app_and_seed_predictions(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="fixture-api-market",
                question="Will API fixtures work?",
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T06:00:00Z",
            )

            app = create_test_app(db_path=db_path)
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

            overview = client.get("/api/overview")

            self.assertEqual(overview.status_code, 200)
            self.assertEqual(overview.json()["valid_predictions"], 1)

    def test_dashboard_api_returns_health_logs_and_human_action_summary(self):
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
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO predictions (market_id, question, forecast_timestamp, is_valid) VALUES (?, ?, ?, ?)",
                    ("m1", "Will example happen?", "2026-04-04T08:16:48Z", 1),
                )
                conn.commit()

            (log_dir / "forecast_20260404.log").write_text(
                "[2026-04-04T09:10:00Z] Forecast completed successfully\n",
                encoding="utf-8",
            )

            app = create_app(
                db_path=db_path,
                log_dir=log_dir,
                session_secret="test-secret",
                dashboard_username="operator",
                dashboard_password_hash="plain:secret-pass",
            )
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

            with patch("python.operator_core.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "Forecast completed successfully"
                mock_run.return_value.stderr = ""

                health_res = client.get("/api/health")
                logs_res = client.get("/api/logs")
                action_res = client.post("/api/actions/forecast")

            self.assertEqual(health_res.status_code, 200)
            self.assertEqual(logs_res.status_code, 200)
            self.assertEqual(action_res.status_code, 200)
            self.assertEqual(health_res.json()["system_status"], "ON")
            self.assertEqual(logs_res.json()["latest_file"], "forecast_20260404.log")
            self.assertIn("summary", action_res.json())
            components = {item["component"] for item in health_res.json()["checks"]}
            self.assertTrue({"sqlite", "llm", "gamma", "exa", "tavily", "scheduler", "telegram"}.issubset(components))

    def test_login_and_overview_flow(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="m1",
                question="Will example happen?",
                forecast_timestamp="2026-04-04T08:16:48.766Z",
                end_date="2026-05-01T00:00:00Z",
            )

            app = create_test_app(db_path=db_path)
            client = TestClient(app)

            login_res = client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})
            self.assertEqual(login_res.status_code, 200)
            overview_res = client.get("/api/overview")
            self.assertEqual(overview_res.status_code, 200)
            self.assertEqual(overview_res.json()["valid_predictions"], 1)
            self.assertIn("daily_digest", overview_res.json())
            self.assertIn("headline", overview_res.json()["daily_digest"])
            self.assertIn("summary", overview_res.json()["daily_digest"])
            self.assertIn("items", overview_res.json()["daily_digest"])
            self.assertIn("next_step", overview_res.json())
            self.assertIn("attention_items", overview_res.json())

    def test_market_details_and_watchlist_endpoints(self):
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
                        49.0,
                        61.0,
                        55.0,
                        "low",
                        "Ранний разбор.",
                        "Ранний риск.",
                        "Исторически около 40%.",
                        '{"summary":"Ранняя сводка","primary_driver":"Старт сезона Wolves","second_order_effects":["Слабый календарь"],"disconfirming_signal":"Три победы подряд","sources":["https://example.com/early"]}',
                        18000,
                        "2026-05-27T00:00:00",
                        "2026-04-03T08:16:48.766Z",
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

            details_after_watch = client.get("/api/markets/572833")
            self.assertTrue(details_after_watch.json()["is_watched"])
            self.assertEqual(len(details_after_watch.json()["history"]), 2)
            self.assertIn("рынок", details_after_watch.json()["verdict_summary"].lower())

            watchlist = client.get("/api/watchlist")
            self.assertEqual(watchlist.status_code, 200)
            self.assertEqual(len(watchlist.json()["items"]), 1)
            self.assertEqual(watchlist.json()["items"][0]["market_id"], "572833")
            self.assertEqual(watchlist.json()["items"][0]["note"], "Следить")

            remove_watch = client.delete("/api/watchlist/572833")
            self.assertEqual(remove_watch.status_code, 200)
            self.assertEqual(client.get("/api/watchlist").json()["items"], [])

    def test_watchlist_dossier_and_update_endpoints(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-99",
                question="Will Burnley finish in last place?",
                market_price=43.6,
                dexter_p=65.0,
                naked_p=68.0,
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-01T12:00:00Z",
            )
            seed_watch_update(
                db_path,
                market_id="watch-99",
                status_label="тезис ослаб",
                reason_summary="Рынок ушёл против тезиса.",
                action_summary="Открыть review",
            )
            app = create_test_app(db_path)
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

            dossiers = client.get("/api/watchlist/dossiers")
            self.assertEqual(dossiers.status_code, 200)
            self.assertEqual(len(dossiers.json()["items"]), 1)
            self.assertEqual(dossiers.json()["items"][0]["market_id"], "watch-99")
            self.assertEqual(dossiers.json()["items"][0]["question"], "Will Burnley finish in last place?")

            updates = client.get("/api/watchlist/updates")
            self.assertEqual(updates.status_code, 200)
            self.assertEqual(len(updates.json()["items"]), 1)
            self.assertEqual(updates.json()["items"][0]["market_id"], "watch-99")
            self.assertEqual(updates.json()["items"][0]["status_label"], "тезис ослаб")

    def test_quality_endpoint_returns_complete_quality_payload(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            log_dir = Path(temp_dir) / "logs"
            log_dir.mkdir()
            calibration_path = log_dir / "calibration_20260406.png"
            calibration_path.write_bytes(b"png")

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
                for index in range(20):
                    rows.append(
                        (
                            f"m{index + 1}",
                            f"Will market {index + 1} resolve?",
                            "Politics" if index < 10 else "Sports",
                            50.0 if index % 2 == 0 else 42.0,
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

            app = create_app(
                db_path=db_path,
                log_dir=log_dir,
                session_secret="test-secret",
                dashboard_username="operator",
                dashboard_password_hash="plain:secret-pass",
            )
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

            response = client.get("/api/quality")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["resolved_predictions"], 20)
            self.assertEqual(payload["minimum_needed"], 20)
            self.assertEqual(payload["status"], "COMPLETE")
            self.assertIn(payload["decision"], {"GO", "PIVOT", "STOP"})
            self.assertIn("metrics", payload)
            self.assertIn("brier_research", payload["metrics"])
            self.assertIn("edge_research_vs_market", payload["metrics"])
            self.assertGreaterEqual(len(payload["category_breakdown"]), 1)
            self.assertIn("category", payload["category_breakdown"][0])
            self.assertEqual(payload["calibration_image"], str(calibration_path.relative_to(Path(temp_dir))))
            self.assertGreaterEqual(len(payload["glossary"]), 1)
            self.assertEqual(payload["glossary"][0]["id"], "brier")

    def test_quality_endpoint_returns_insufficient_data_when_only_nineteen_rows_are_usable(self):
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
                for index in range(20):
                    rows.append(
                        (
                            f"m{index + 1}",
                            f"Will market {index + 1} resolve?",
                            "Politics" if index < 10 else "Sports",
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

            app = create_app(
                db_path=db_path,
                log_dir=log_dir,
                session_secret="test-secret",
                dashboard_username="operator",
                dashboard_password_hash="plain:secret-pass",
            )
            client = TestClient(app)
            client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

            response = client.get("/api/quality")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "INSUFFICIENT_DATA")
            self.assertEqual(payload["decision"], "COLLECT")
            self.assertEqual(payload["resolved_predictions"], 20)
            self.assertEqual(payload["calibration_image"], "logs/calibration_20260406.png")
            self.assertIn("19", payload["summary"])
            self.assertIn("нужно ещё 1", payload["summary"].lower())
            self.assertNotIn("достаточно", payload["summary"].lower())
            self.assertIsNone(payload["metrics"]["brier_research"])
            self.assertIsNone(payload["metrics"]["edge_research_vs_market"])

    def test_workspace_and_manual_position_endpoints(self):
        with TemporaryDirectory() as temp_dir:
            from python.decision_store import ensure_decision_core_schema

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
                        "market-api-workspace",
                        "Will Example operator workflow pass?",
                        "tests",
                        22.0,
                        41.0,
                        38.0,
                        "medium",
                        "Pythia likes the setup.",
                        "Momentum may reverse.",
                        "Base rate exists.",
                        '{"summary":"Operator summary","primary_driver":"Strong catalyst","second_order_effects":["Secondary support"],"disconfirming_signal":"Catalyst fades","sources":["https://example.com/operator"]}',
                        12345,
                        "2026-05-01T00:00:00Z",
                        "2026-04-07T06:00:00Z",
                        1,
                    ),
                )
                ensure_decision_core_schema(conn)
                conn.execute(
                    """
                    INSERT INTO signals (
                        id, prediction_id, market_id, question, forecast_timestamp, market_price,
                        research_probability, naked_probability, best_probability, signal_edge,
                        signal_strength, recommended_action, entry_zone_min, entry_zone_max,
                        thesis_summary, primary_driver, disconfirming_signal, confidence,
                        research_status, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        "market-api-workspace",
                        "Will Example operator workflow pass?",
                        "2026-04-07T06:00:00Z",
                        22.0,
                        41.0,
                        38.0,
                        41.0,
                        19.0,
                        19.0,
                        "enter_yes",
                        22.0,
                        39.0,
                        "Operator summary",
                        "Strong catalyst",
                        "Catalyst fades",
                        "medium",
                        "ON",
                        "new",
                        "2026-04-07T06:00:00Z",
                        "2026-04-07T06:00:00Z",
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

            workspace_before = client.get("/api/markets/market-api-workspace/workspace")
            self.assertEqual(workspace_before.status_code, 200)
            self.assertEqual(workspace_before.json()["signal"]["recommended_action"], "enter_yes")
            self.assertEqual(workspace_before.json()["open_positions"], [])
            self.assertIsNone(workspace_before.json()["watch_dossier"])
            self.assertEqual(workspace_before.json()["watch_updates"], [])

            create_decision = client.post(
                "/api/decisions",
                json={
                    "signal_id": 1,
                    "decision_type": "enter_yes",
                    "decision_reason": "Открываю operator paper position по сильному сигналу.",
                    "operator_confidence": "medium",
                    "confirmation_criteria": "Сигнал подтверждается следующим прогоном.",
                    "invalidation_criteria": "Сигнал пропадает или рынок резко уходит против.",
                    "open_position": True,
                },
            )
            self.assertEqual(create_decision.status_code, 200)
            create_payload = create_decision.json()
            self.assertTrue(create_payload["ok"])
            self.assertIsNotNone(create_payload["decision_id"])
            self.assertIsNotNone(create_payload["position_id"])

            open_positions = client.get("/api/positions/open")
            self.assertEqual(open_positions.status_code, 200)
            self.assertEqual(len(open_positions.json()["items"]), 1)
            self.assertEqual(open_positions.json()["items"][0]["mode"], "operator")

            workspace_after = client.get("/api/markets/market-api-workspace/workspace")
            self.assertEqual(len(workspace_after.json()["recent_decisions"]), 1)
            self.assertEqual(len(workspace_after.json()["open_positions"]), 1)

            position_id = create_payload["position_id"]
            close_position = client.post(
                f"/api/positions/{position_id}/close",
                json={"note": "Закрываю operator paper position вручную."},
            )
            self.assertEqual(close_position.status_code, 200)
            self.assertEqual(close_position.json()["status"], "closed")

            review_due = client.get("/api/reviews/due")
            self.assertEqual(review_due.status_code, 200)
            self.assertEqual(review_due.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
