import unittest

from python.operator_actions import (
    TELEGRAM_MESSAGE_LIMIT,
    build_help_message,
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


if __name__ == "__main__":
    unittest.main()
