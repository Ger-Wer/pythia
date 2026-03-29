import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
import sqlite3
import os

from python.operator_actions import (
    TELEGRAM_MESSAGE_LIMIT,
    _decode_chunked_body,
    build_help_message,
    format_details_report,
    format_forecast_report,
    format_last_predictions_report,
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
        self.assertIn("/details", help_text)

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
                        dexter_reasoning TEXT,
                        naked_reasoning TEXT,
                        api_cost_usd REAL,
                        volume REAL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        question, market_price, dexter_p, naked_p,
                        dexter_confidence, dexter_reasoning, naked_reasoning,
                        api_cost_usd, volume, forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "Will Team A win the finals?",
                            40.0,
                            62.0,
                            58.0,
                            "high",
                            "Команда A набрала форму и получила преимущество своей площадки.",
                            "Базовый прогноз осторожно поддерживает рынок.",
                            0.11,
                            12345,
                            "2026-03-29T14:00:00",
                            1,
                        ),
                        (
                            "Will inflation fall below 3% by June?",
                            55.0,
                            None,
                            49.0,
                            None,
                            None,
                            "Инфляция замедляется, но путь остаётся неровным.",
                            0.07,
                            67890,
                            "2026-03-29T14:00:00",
                            1,
                        ),
                        (
                            "Invalid legacy row",
                            99.0,
                            1.0,
                            1.0,
                            "low",
                            "Should not appear.",
                            "Should not appear.",
                            0.99,
                            100,
                            "2026-03-29T14:00:00",
                            0,
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
            self.assertIn("Исследование: DEGRADED", rendered)
            self.assertIn("Расхождения", rendered)
            self.assertIn("Почему:", rendered)
            self.assertNotIn("Invalid legacy row", rendered)
            self.assertNotIn("/app/src/services", rendered)

    def test_format_status_report_uses_database_summary_without_raw_stdout(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            previous_base_url = os.environ.get("DEEPSEEK_BASE_URL")
            os.environ["DEEPSEEK_BASE_URL"] = "https://api.groq.com/openai/v1"
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
                        brier_market REAL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO predictions (
                        question, forecast_timestamp, api_cost_usd, outcome,
                        brier_naked, brier_dexter, brier_market, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("Q1", "2026-03-29T10:00:00", 0.10, 1, 0.1, 0.2, 0.3, 1),
                        ("Q2", "2026-03-29T10:00:00", 0.12, None, None, None, None, 1),
                        ("Invalid legacy row", "2026-03-29T10:00:00", 9.99, 1, 0.9, 0.9, 0.9, 0),
                    ],
                )

            try:
                rendered = format_status_report(
                    db_path,
                    services=[{"name": "pythia-scheduler", "status": "healthy"}],
                )
            finally:
                if previous_base_url is None:
                    del os.environ["DEEPSEEK_BASE_URL"]
                else:
                    os.environ["DEEPSEEK_BASE_URL"] = previous_base_url

            self.assertIn("Статус Pythia", rendered)
            self.assertIn("Всего прогнозов: 2", rendered)
            self.assertIn("Разрешено: 1", rendered)
            self.assertIn("бесплатно (Groq)", rendered)
            self.assertNotIn("stderr", rendered)
            self.assertNotIn("Invalid legacy row", rendered)

    def test_format_last_predictions_and_details_use_only_valid_rows(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
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
                            "market-1",
                            "Will the Oklahoma City Thunder win the NBA Western Conference Finals?",
                            48.5,
                            55.0,
                            57.0,
                            "medium",
                            "Thunder выглядят сильнее рынка из-за формы лидеров и глубины состава.",
                            "Травма ключевого игрока до плей-офф.",
                            "Команда с лучшим посевом побеждает конференцию примерно в трети сезонов.",
                            '{"summary":"Короткая справка","key_facts":["Thunder лидируют на Западе"],"sources":["https://example.com/nba"]}',
                            445436,
                            "2026-05-20T00:00:00",
                            "2026-03-29T18:00:00",
                            1,
                        ),
                        (
                            "market-legacy",
                            "Invalid legacy row",
                            99.0,
                            1.0,
                            1.0,
                            "low",
                            "Should not appear.",
                            "Should not appear.",
                            "Should not appear.",
                            '{"summary":"Legacy"}',
                            100,
                            "2026-04-01T00:00:00",
                            "2026-03-29T17:00:00",
                            0,
                        ),
                    ],
                )

            listing = format_last_predictions_report(db_path, limit=10)
            details = format_details_report(db_path, "1")

            self.assertIn("1.", listing)
            self.assertIn("Thunder", listing)
            self.assertNotIn("Invalid legacy row", listing)
            self.assertIn("Подробный анализ", details)
            self.assertIn("Короткая справка", details)
            self.assertIn("Травма ключевого игрока", details)
            self.assertIn("Thunder", details)
            self.assertNotIn("Invalid legacy row", details)


if __name__ == "__main__":
    unittest.main()
