import os
import sqlite3
import unittest
from unittest.mock import Mock, patch

from python import telegram_bot
from python.test_support import seed_prediction, seed_watch_update, temp_db
from python.attention_store import list_pending_attention_items


class WeeklyReportEntrypointTests(unittest.TestCase):
    def test_weekly_report_sends_digest_via_send_message(self):
        """``weekly_report()`` must compose the digest from the new
        ``python.weekly_digest`` aggregator and ship it via
        ``send_message`` even when the DB is empty (gate #11)."""

        with temp_db() as db_path:
            with patch.object(telegram_bot, "DB_PATH", db_path), \
                patch.object(telegram_bot, "BOT_TOKEN", "test-token"), \
                patch.object(telegram_bot, "CHAT_ID", "123"), \
                patch("python.telegram_bot.requests.post") as mock_post:
                mock_post.return_value = Mock(ok=True, status_code=200)

                telegram_bot.weekly_report()

                # exactly one Telegram call (no calibration PNG present)
                self.assertEqual(mock_post.call_count, 1)
                sent_text = mock_post.call_args.kwargs["json"]["text"]
                self.assertIn("Еженедельный отчёт Pythia", sent_text)


class TelegramBotTests(unittest.TestCase):
    def test_watch_updates_include_dashboard_link_and_mark_delivery_only_after_success(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-telegram",
                question="Will Telegram delivery work?",
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T06:00:00Z",
            )
            seed_watch_update(
                db_path,
                market_id="watch-telegram",
                status_label="тезис ослаб",
                reason_summary="Рынок двинулся против тезиса.",
                action_summary="Открыть разбор рынка",
            )

            with patch.object(telegram_bot, "DB_PATH", db_path), \
                patch.object(telegram_bot, "BOT_TOKEN", "test-token"), \
                patch.object(telegram_bot, "CHAT_ID", "123"), \
                patch.dict(
                    os.environ,
                    {
                        "DASHBOARD_PUBLIC_BASE_URL": "https://b1w.duckdns.org",
                        "OPENAI_API_KEY": "test-openai-key",
                        "EXA_API_KEY": "test-exa-key",
                    },
                    clear=False,
                ), \
                patch("python.telegram_bot.requests.post") as mock_post:
                mock_post.return_value = Mock(ok=True, status_code=200)

                telegram_bot.watch_updates(limit=10)

                sent_text = mock_post.call_args.kwargs["json"]["text"]
                self.assertIn("📬 *Pythia inbox*", sent_text)
                self.assertIn("https://b1w.duckdns.org/markets/watch-telegram", sent_text)
                self.assertIn("Почему: Рынок двинулся против тезиса.", sent_text)
                self.assertIn("Действие: Открыть разбор рынка", sent_text)

                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    self.assertEqual(list_pending_attention_items(conn), [])

    def test_watch_updates_do_not_mark_delivery_when_telegram_send_fails(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="watch-telegram-fail",
                question="Will failed delivery stay pending?",
                forecast_timestamp="2026-04-07T06:00:00Z",
                end_date="2026-05-07T06:00:00Z",
            )
            seed_watch_update(
                db_path,
                market_id="watch-telegram-fail",
                status_label="нужен review",
                reason_summary="Наступил срок review.",
                action_summary="Открыть разбор рынка",
            )

            with patch.object(telegram_bot, "DB_PATH", db_path), \
                patch.object(telegram_bot, "BOT_TOKEN", "test-token"), \
                patch.object(telegram_bot, "CHAT_ID", "123"), \
                patch.dict(
                    os.environ,
                    {
                        "DASHBOARD_PUBLIC_BASE_URL": "https://b1w.duckdns.org",
                        "OPENAI_API_KEY": "test-openai-key",
                        "EXA_API_KEY": "test-exa-key",
                    },
                    clear=False,
                ), \
                patch("python.telegram_bot.requests.post") as mock_post:
                mock_post.return_value = Mock(ok=False, status_code=500, text="boom")

                telegram_bot.watch_updates(limit=10)

                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    pending = list_pending_attention_items(conn)
                    self.assertEqual(len(pending), 1)
                    self.assertEqual(pending[0]["market_id"], "watch-telegram-fail")
