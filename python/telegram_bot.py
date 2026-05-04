#!/usr/bin/env python3
"""
Pythia Telegram Bot
Режимы: --alert "message" | --weekly-report | --send-image path
"""

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from python.attention_store import (
    ensure_attention_engine_schema,
    list_pending_attention_items,
    mark_attention_items_delivered,
    sync_attention_items,
)
from python.operator_core import OperatorCore
from python.watchlist_store import (
    ensure_watchlist_engine_schema,
)
from python.weekly_digest import compose_weekly_digest, format_weekly_digest_markdown

BASE_DIR = Path(__file__).resolve().parent.parent
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))


def dashboard_base_url() -> str:
    return os.getenv("DASHBOARD_PUBLIC_BASE_URL", "").rstrip("/")


def send_message(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Telegram не настроен. Сообщение: {text}")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=20,
        )
    except requests.RequestException as error:
        print(f"Telegram sendMessage failed: {error}")
        return False

    if not response.ok:
        print(f"Telegram sendMessage failed: HTTP {response.status_code} {response.text}")
        return False
    return True


def send_photo(path: str, caption: str = "") -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Telegram не настроен. Изображение: {path}")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as handle:
        try:
            response = requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "caption": caption,
                    "parse_mode": "Markdown",
                },
                files={"photo": handle},
                timeout=60,
            )
        except requests.RequestException as error:
            print(f"Telegram sendPhoto failed: {error}")
            return False

    if not response.ok:
        print(f"Telegram sendPhoto failed: HTTP {response.status_code} {response.text}")
        return False
    return True


def weekly_report():
    """Compose and send the Sunday-night digest.

    Aggregates predictions, decision discipline, position activity, P&L,
    postmortem categories, reviews, and Brier scores via
    ``python.weekly_digest`` (gate #11). The aggregator degrades any
    section to "нет данных" when its underlying tables are missing, so
    this entry-point can be safely run on early-deployment DBs without
    decision_core schema applied yet.
    """

    conn = sqlite3.connect(DB_PATH)
    try:
        digest = compose_weekly_digest(conn)
    finally:
        conn.close()

    msg = format_weekly_digest_markdown(digest)
    send_message(msg)

    cal_path = os.path.join(
        LOG_DIR,
        f"calibration_{datetime.now().strftime('%Y%m%d')}.png",
    )
    if os.path.exists(cal_path):
        send_photo(cal_path, "Калибровочная кривая")


def watch_updates(limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_watchlist_engine_schema(conn)
    ensure_attention_engine_schema(conn)
    health = OperatorCore(DB_PATH, LOG_DIR).get_health()
    sync_attention_items(
        conn,
        system_status=str(health["system_status"]),
        research_status=str(health["research_status"]),
        research_warning=str(health["research_warning"]),
    )
    conn.commit()
    pending = list_pending_attention_items(conn, limit=limit)
    if not pending:
        conn.close()
        print("Нет новых attention updates для Telegram.")
        return

    delivered_ids: list[int] = []
    for item in pending:
        market_id = item.get("market_id")
        market_link = f"{dashboard_base_url()}/markets/{market_id}" if dashboard_base_url() and market_id else ""
        sent = send_message(
            "\n".join(
                [
                    "📬 *Pythia inbox*",
                    f"Рынок: {item.get('question') or item.get('market_id')}",
                    f"Статус: {item.get('title')}",
                    f"Почему: {item.get('detail')}",
                    f"Действие: {item.get('action_label')}",
                    *( [f"Открыть разбор: {market_link}"] if market_link else [] ),
                ]
            )
        )
        if sent:
            delivered_ids.append(int(item["id"]))

    if delivered_ids:
        mark_attention_items_delivered(conn, delivered_ids)
        conn.commit()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert", type=str, help="Send alert message")
    parser.add_argument("--weekly-report", action="store_true")
    parser.add_argument("--send-image", type=str)
    parser.add_argument("--watch-updates", action="store_true")
    args = parser.parse_args()

    if args.alert:
        send_message(args.alert)
    elif args.weekly_report:
        weekly_report()
    elif args.send_image:
        send_photo(args.send_image)
    elif args.watch_updates:
        watch_updates()
