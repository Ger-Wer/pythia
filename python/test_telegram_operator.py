import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
import sqlite3

from python.operator_actions import (
    TELEGRAM_MESSAGE_LIMIT,
    _decode_chunked_body,
    build_help_message,
    format_forecast_report,
    format_status_report,
    format_health_summary,
    is_authorized_update,
    normalize_command,
    trim_message,
)
from python.telegram_operator import prepare_markdown_message


class TelegramOperatorTests(unittest.TestCase):
    def test_normalize_slash_and_russian_aliases(self):
        self.assertEqual(normalize_command("/status"), "status")
        self.assertEqual(normalize_command("/status@pythia_bot"), "status")
        self.assertEqual(normalize_command("/status extra"), "status")
        self.assertEqual(normalize_command("статус"), "status")
        self.assertEqual(normalize_command("прогноз"), "forecast")
        self.assertEqual(normalize_command("/last_predictions"), "last_predictions")
        self.assertEqual(normalize_command("недельный отчет"), "weekly")

    def test_authorization_allows_only_private_chat_for_single_user(self):
        self.assertTrue(
            is_authorized_update(
                {"message": {"from": {"id": 405690972}, "chat": {"type": "private"}}},
                allowed_user_id=405690972,
            )
        )
        self.assertFalse(
            is_authorized_update(
                {"message": {"from": {"id": 111}, "chat": {"type": "private"}}},
                allowed_user_id=405690972,
            )
        )
        self.assertFalse(
            is_authorized_update(
                {"message": {"from": {"id": 405690972}, "chat": {"type": "group"}}},
                allowed_user_id=405690972,
            )
        )

    def test_trim_message_keeps_telegram_safe_length(self):
        raw = "x" * (TELEGRAM_MESSAGE_LIMIT + 200)
        trimmed = trim_message(raw)
        self.assertLessEqual(len(trimmed), TELEGRAM_MESSAGE_LIMIT)
        self.assertTrue(trimmed.endswith("..."))

    def test_help_message_mentions_russian_aliases(self):
        help_text = build_help_message()
        self.assertIn("/status", help_text)
        self.assertIn("статус", help_text)
        self.assertIn("/forecast", help_text)
        self.assertIn("прогноз", help_text)

    def test_health_summary_formats_service_statuses(self):
        rendered = format_health_summary(
            [
                {"name": "pythia-scheduler", "status": "healthy"},
                {"name": "pythia-telegram", "status": "running"},
            ]
        )
        self.assertIn("pythia-scheduler", rendered)
        self.assertIn("healthy", rendered)
        self.assertIn("pythia-telegram", rendered)

    def test_prepare_markdown_message_keeps_safe_tail(self):
        raw = "[" * 5000
        prepared = prepare_markdown_message(raw)
        self.assertLessEqual(len(prepared), TELEGRAM_MESSAGE_LIMIT)
        self.assertFalse(prepared.endswith("\\"))

    def test_decode_chunked_body_decodes_http_chunks(self):
        chunked = "4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n"
        self.assertEqual(_decode_chunked_body(chunked), "Wikipedia")

    def test_format_forecast_report_renders_human_summary_from_sqlite(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        question TEXT NOT NULL,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        dexter_confidence TEXT,
                        api_cost_usd REAL,
                        volume REAL,
                        forecast_timestamp TEXT NOT NULL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        question, market_price, dexter_p, naked_p,
                        dexter_confidence, api_cost_usd, volume, forecast_timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "Will Team A win the finals?",
                            40.0,
                            62.0,
                            58.0,
                            "high",
                            0.11,
                            12345,
                            "2026-03-29T14:00:00",
                        ),
                        (
                            "Will inflation fall below 3% by June?",
                            55.0,
                            None,
                            49.0,
                            None,
                            0.07,
                            67890,
                            "2026-03-29T14:00:00",
                        ),
                    ],
                )

            rendered = format_forecast_report(
                db_path,
                run_timestamp="2026-03-29T14:00:00",
                research_status="DEGRADED",
                issues=["Exa API вернул 401, используем degraded mode."],
            )

            self.assertIn("Pythia", rendered)
            self.assertIn("Обработано рынков: 2", rendered)
            self.assertIn("Research: DEGRADED", rendered)
            self.assertIn("Расхождения", rendered)
            self.assertNotIn("/app/src/services", rendered)

    def test_format_status_report_uses_database_summary_without_raw_stdout(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        question TEXT NOT NULL,
                        forecast_timestamp TEXT NOT NULL,
                        api_cost_usd REAL,
                        outcome INTEGER,
                        brier_naked REAL,
                        brier_dexter REAL,
                        brier_market REAL
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        question, forecast_timestamp, api_cost_usd, outcome,
                        brier_naked, brier_dexter, brier_market
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("Q1", "2026-03-29T10:00:00", 0.10, 1, 0.1, 0.2, 0.3),
                        ("Q2", "2026-03-29T10:00:00", 0.12, None, None, None, None),
                    ],
                )

            rendered = format_status_report(
                db_path,
                services=[{"name": "pythia-scheduler", "status": "healthy"}],
            )

            self.assertIn("Статус Pythia", rendered)
            self.assertIn("Всего прогнозов: 2", rendered)
            self.assertIn("Разрешено: 1", rendered)
            self.assertNotIn("stderr", rendered)


if __name__ == "__main__":
    unittest.main()
