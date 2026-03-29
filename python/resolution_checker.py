#!/usr/bin/env python3
"""
Pythia Resolution Checker
Проверяет resolved маркеты, обновляет outcomes и Brier scores.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
GAMMA_API = "https://gamma-api.polymarket.com"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def prediction_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
    }


def valid_filter(conn: sqlite3.Connection, alias: str = "") -> str:
    if "is_valid" not in prediction_columns(conn):
        return ""
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}is_valid = 1"


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"Telegram send failed: {exc}")


def mark_market_invalid(conn: sqlite3.Connection, market_id: str, reason: str) -> None:
    if "is_valid" not in prediction_columns(conn):
        print(f"Не удалось инвалидировать {market_id}: в таблице нет is_valid")
        return

    conn.execute(
        "UPDATE predictions SET is_valid = 0 WHERE market_id = ?",
        (market_id,),
    )
    print(f"Маркет {market_id} помечен невалидным: {reason}")


def fetch_market(market_id: str) -> tuple[dict[str, Any] | None, bool]:
    last_error: Exception | None = None
    saw_http_422 = True
    for attempt, timeout in enumerate((20, 20, 20), start=1):
        try:
            response = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=timeout)
            response.raise_for_status()
            return response.json(), False
        except requests.RequestException as exc:
            last_error = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code != 422:
                saw_http_422 = False
            if attempt < 3:
                time.sleep(1 if attempt == 1 else 3)
    if saw_http_422:
        print(f"Gamma API вернул HTTP 422 для {market_id} три раза подряд")
        return None, True
    print(f"Failed to fetch market {market_id}: {last_error}")
    return None, False


def parse_prices(value: Any) -> list[float]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            raw = []
    else:
        raw = []

    prices: list[float] = []
    for item in raw:
        try:
            prices.append(float(item))
        except (TypeError, ValueError):
            continue
    return prices


def derive_outcome(market: dict[str, Any]) -> tuple[int | None, str]:
    resolved_at = (
        market.get("closedTime")
        or market.get("updatedAt")
        or market.get("endDate")
        or utc_now_iso()
    )

    if not market.get("closed"):
        return None, resolved_at

    prices = parse_prices(market.get("outcomePrices"))
    if len(prices) != 2:
        return -1, resolved_at

    yes_price, no_price = prices
    if yes_price >= 0.99 and no_price <= 0.01:
        return 1, resolved_at
    if no_price >= 0.99 and yes_price <= 0.01:
        return 0, resolved_at

    return -1, resolved_at


def update_resolved_market(conn: sqlite3.Connection, market_id: str, outcome: int, resolved_at: str) -> None:
    filter_suffix = valid_filter(conn)
    if outcome in (0, 1):
        conn.execute(
            """
            UPDATE predictions
            SET
              outcome = ?,
              resolved_at = ?,
              brier_dexter = CASE
                WHEN dexter_p IS NOT NULL THEN (dexter_p / 100.0 - ?) * (dexter_p / 100.0 - ?)
                ELSE NULL
              END,
              brier_naked = CASE
                WHEN naked_p IS NOT NULL THEN (naked_p / 100.0 - ?) * (naked_p / 100.0 - ?)
                ELSE NULL
              END,
              brier_market = (market_price / 100.0 - ?) * (market_price / 100.0 - ?)
            WHERE market_id = ?
            """
            + filter_suffix,
            (outcome, resolved_at, outcome, outcome, outcome, outcome, outcome, outcome, market_id),
        )
    else:
        conn.execute(
            """
            UPDATE predictions
            SET
              outcome = -1,
              resolved_at = ?,
              brier_dexter = NULL,
              brier_naked = NULL,
              brier_market = NULL
            WHERE market_id = ?
            """
            + filter_suffix,
            (resolved_at, market_id),
        )


def summarize_market(conn: sqlite3.Connection, market_id: str) -> tuple[str, float] | None:
    filter_suffix = valid_filter(conn)
    row = conn.execute(
        """
        SELECT question, dexter_p, naked_p, market_price, outcome
        FROM predictions
        WHERE market_id = ?
        """
        + filter_suffix
        + """
        ORDER BY forecast_timestamp DESC
        LIMIT 1
        """,
        (market_id,),
    ).fetchone()

    if not row:
        return None

    question, dexter_p, naked_p, market_price, outcome = row
    representative = dexter_p if dexter_p is not None else naked_p if naked_p is not None else market_price
    error = abs(representative / 100.0 - outcome)
    return str(question), float(error)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    if not table_exists:
        print("Таблица predictions ещё не создана")
        conn.close()
        return

    market_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT market_id FROM predictions WHERE outcome IS NULL" + valid_filter(conn)
        ).fetchall()
    ]

    resolved_today: list[str] = []
    cancelled_today: list[str] = []

    for market_id in market_ids:
        market, should_invalidate = fetch_market(str(market_id))
        if should_invalidate:
            mark_market_invalid(conn, str(market_id), "Gamma API returned HTTP 422 three times")
            continue

        if not market:
            continue

        outcome, resolved_at = derive_outcome(market)
        if outcome is None:
            continue

        update_resolved_market(conn, str(market_id), outcome, resolved_at)
        if outcome in (0, 1):
            resolved_today.append(str(market_id))
        else:
            cancelled_today.append(str(market_id))

    conn.commit()

    print(f"Сегодня разрешилось маркетов: {len(resolved_today)}")
    if cancelled_today:
        print(f"Маркетов, помеченных как cancelled/N/A: {len(cancelled_today)}")

    if resolved_today:
        correct: list[str] = []
        wrong: list[str] = []
        for market_id in resolved_today:
            summary = summarize_market(conn, market_id)
            if not summary:
                continue
            question, error = summary
            if error < 0.3:
                correct.append(question[:80])
            elif error > 0.7:
                wrong.append(question[:80])

        cumulative = conn.execute(
            """
            SELECT
              AVG(brier_dexter),
              AVG(brier_naked),
              AVG(brier_market)
            FROM predictions
            WHERE outcome IN (0, 1)
            """
            + valid_filter(conn)
        ).fetchone()

        dexter_avg = f"{cumulative[0]:.4f}" if cumulative[0] is not None else "N/A"
        naked_avg = f"{cumulative[1]:.4f}" if cumulative[1] is not None else "N/A"
        market_avg = f"{cumulative[2]:.4f}" if cumulative[2] is not None else "N/A"

        message = (
            f"📊 Разрешилось маркетов: {len(resolved_today)}\n"
            f"✅ Точные прогнозы (|p - outcome| < 0.3): {', '.join(correct[:5]) or 'нет'}\n"
            f"❌ Сильные промахи (|p - outcome| > 0.7): {', '.join(wrong[:5]) or 'нет'}\n"
            f"Накопленный Brier: research+R1={dexter_avg}, "
            f"naked={naked_avg}, "
            f"market={market_avg}"
        )
        send_telegram(message)

    conn.close()


if __name__ == "__main__":
    main()
