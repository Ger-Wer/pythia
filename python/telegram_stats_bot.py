#!/usr/bin/env python3
"""
Telegram Stats Bot — обработчик команд для просмотра LLM статистики.

Запуск: python python/telegram_stats_bot.py

Команды:
  /stats        — статистика LLM за 30 дней
  /stats 7      — статистика за 7 дней
  /stats 90     — статистика за 90 дней
"""

import os
import sys
import time
import json
import requests
from pathlib import Path
from typing import Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.llm_stats import get_llm_stats, format_telegram

BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS = os.getenv("TELEGRAM_ALLOWED_USERS", "")  # comma-separated user IDs

# State
_offset = 0
_running = True


def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {msg}")


def get_updates(offset: int) -> list:
    """Poll Telegram for new messages."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 30, "allowed_updates": '["message"]'}
    try:
        resp = requests.get(url, params=params, timeout=35)
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        log(f"getUpdates error: {e}")
    return []


def send_message(chat_id: int, text: str, reply_to: Optional[int] = None):
    """Send a message to a chat."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            log(f"sendMessage error: {data}")
    except Exception as e:
        log(f"sendMessage exception: {e}")


def is_allowed(user_id: int) -> bool:
    """Check if user is in allowlist. Empty allowlist = everyone allowed."""
    if not ALLOWED_USERS:
        return True
    allowed = {int(uid.strip()) for uid in ALLOWED_USERS.split(",") if uid.strip()}
    return user_id in allowed


def handle_command(chat_id: int, user_id: int, text: str):
    """Route /commands."""
    if not text.startswith("/"):
        return

    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("/stats", "/weekly"):
        days = 7 if cmd == "/weekly" else 30
        if args:
            try:
                days = int(args[0])
                days = max(1, min(days, 365))
            except ValueError:
                send_message(chat_id, "Usage: /stats [days]  (1-365)")
                return

        send_message(chat_id, f"⏳ Loading {days}-day stats...")
        try:
            stats = get_llm_stats(days=days)
            msg = format_telegram(stats)
            send_message(chat_id, msg)
        except Exception as e:
            send_message(chat_id, f"❌ Error: {e}")

    elif cmd == "/help":
        send_message(chat_id,
            "📊 *Pythia — LLM Stats*\n\n"
            "/stats — LLM comparison (30d)\n"
            "/stats 7 — last 7 days\n"
            "/stats 90 — last 90 days\n"
            "/weekly — shortcut for /stats 7"
        )

    elif cmd == "/start":
        send_message(chat_id,
            "📊 *Pythia — LLM Stats*\n\n"
            "/stats — LLM comparison (30d)\n"
            "/stats 7 — last 7 days\n"
            "/stats 90 — last 90 days\n"
            "/weekly — shortcut for /stats 7"
        )


def main():
    global _offset, _running

    if not BOT_TOKEN:
        print("ERROR: ORACLE_TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    log("Starting Telegram Stats Bot...")
    log(f"Allowed users: {ALLOWED_USERS or 'all'}")

    while _running:
        try:
            updates = get_updates(_offset)
            for update in updates:
                _offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                user_id = msg["from"]["id"]
                text = msg.get("text", "")

                if not is_allowed(user_id):
                    log(f"Rejected user {user_id}")
                    continue

                if text.startswith("/"):
                    log(f"Command from {user_id}: {text}")
                    handle_command(chat_id, user_id, text)

        except KeyboardInterrupt:
            log("Shutting down...")
            _running = False
        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
