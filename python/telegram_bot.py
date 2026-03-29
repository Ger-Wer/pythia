#!/usr/bin/env python3
"""
Pythia Telegram Bot
Режимы: --alert "message" | --weekly-report | --send-image path
"""

import argparse
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))


def send_message(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Telegram не настроен. Сообщение: {text}")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=20,
    )


def send_photo(path: str, caption: str = ""):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Telegram не настроен. Изображение: {path}")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as handle:
        requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
                "parse_mode": "Markdown",
            },
            files={"photo": handle},
            timeout=60,
        )


def weekly_report():
    conn = sqlite3.connect(DB_PATH)
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    if not table_exists:
        conn.close()
        send_message("Pythia: таблица predictions ещё не создана, еженедельный отчёт пока недоступен.")
        return

    total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    resolved = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE outcome IS NOT NULL AND outcome IN (0,1)"
    ).fetchone()[0]

    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    new_this_week = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE forecast_timestamp > ?",
        (week_ago,),
    ).fetchone()[0]
    resolved_this_week = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE resolved_at > ? AND outcome IN (0,1)",
        (week_ago,),
    ).fetchone()[0]

    brier_data = conn.execute(
        """
        SELECT
            AVG(brier_dexter),
            AVG(brier_naked),
            AVG(brier_market)
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome IN (0,1)
        """
    ).fetchone()

    month_start = datetime.now().replace(day=1).isoformat()
    month_cost = conn.execute(
        "SELECT COALESCE(SUM(api_cost_usd), 0) FROM predictions WHERE forecast_timestamp > ?",
        (month_start,),
    ).fetchone()[0]

    conn.close()

    msg = f"""📊 *Еженедельный отчёт Pythia*
_{datetime.now().strftime('%Y-%m-%d')}_

*За неделю:*
• Новых прогнозов: {new_this_week}
• Разрешившихся маркетов: {resolved_this_week}

*С начала наблюдений:*
• Всего прогнозов: {total}
• Разрешилось: {resolved}
• В ожидании: {total - resolved}

*Brier score (↓ меньше = лучше):*
• Dexter: {f'{brier_data[0]:.4f}' if brier_data[0] is not None else 'N/A'}
• Naked R1: {f'{brier_data[1]:.4f}' if brier_data[1] is not None else 'N/A'}
• Рыночная цена: {f'{brier_data[2]:.4f}' if brier_data[2] is not None else 'N/A'}

*Расход API в этом месяце:* ${month_cost:.2f}
"""

    send_message(msg)

    cal_path = os.path.join(
        LOG_DIR,
        f"calibration_{datetime.now().strftime('%Y%m%d')}.png",
    )
    if os.path.exists(cal_path):
        send_photo(cal_path, "Калибровочная кривая")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert", type=str, help="Send alert message")
    parser.add_argument("--weekly-report", action="store_true")
    parser.add_argument("--send-image", type=str)
    args = parser.parse_args()

    if args.alert:
        send_message(args.alert)
    elif args.weekly_report:
        weekly_report()
    elif args.send_image:
        send_photo(args.send_image)
