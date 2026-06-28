#!/usr/bin/env python3
"""
LLM Stats Publisher — публикует статистику LLM в Telegram канал.
Запускается по крону каждое воскресенье в 12:00 UTC.
"""

import os
import sys
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.llm_stats import get_llm_stats, format_telegram

BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID", "")


def publish_stats():
    if not BOT_TOKEN or not CHANNEL_ID:
        print("Telegram not configured, printing to stdout only")
        stats = get_llm_stats(days=30)
        print(format_telegram(stats))
        return

    stats = get_llm_stats(days=30)
    text = format_telegram(stats)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        data = resp.json()
        if data.get("ok"):
            print(f"Published stats to channel: message_id={data['result']['message_id']}")
        else:
            print(f"Telegram API error: {data}")
    except Exception as e:
        print(f"Failed to publish: {e}")


if __name__ == "__main__":
    publish_stats()
