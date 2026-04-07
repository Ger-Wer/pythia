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
from python.decision_store import ensure_decision_core_schema

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


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def fetchall_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    columns = [str(description[0]) for description in cursor.description or []]
    return [
        {columns[index]: row[index] for index in range(len(columns))}
        for row in rows
    ]


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


def _resolved_yes_price(outcome: int) -> float:
    return 100.0 if outcome == 1 else 0.0


def _payout_per_contract(side: str, outcome: int) -> float:
    normalized = side.lower()
    if normalized == "yes" and outcome == 1:
        return 1.0
    if normalized == "no" and outcome == 0:
        return 1.0
    return 0.0


def _upsert_resolution_postmortem(
    conn: sqlite3.Connection,
    position: dict[str, Any],
    *,
    outcome: int,
    resolved_at: str,
) -> None:
    outcome_label = "yes" if outcome == 1 else "no"
    forecast_error = 0 if _payout_per_contract(str(position["side"]), outcome) == 1.0 else 1
    entry_error = 0 if forecast_error == 0 else 0

    existing = conn.execute(
        """
        SELECT timing_error, management_error, discipline_error, operator_note, system_note
        FROM postmortems
        WHERE position_id = ?
        LIMIT 1
        """,
        (position["id"],),
    ).fetchone()
    existing_timing = existing[0] if existing else None
    existing_management = existing[1] if existing else None
    existing_discipline = existing[2] if existing else None
    existing_operator_note = existing[3] if existing else None
    existing_system_note = existing[4] if existing else None

    if existing_timing is not None:
        timing_error = existing_timing
    elif position.get("close_reason") == "resolved" or position.get("status") == "open":
        timing_error = 0
    else:
        timing_error = 1
    management_error = existing_management if existing_management is not None else 0
    discipline_error = existing_discipline if existing_discipline is not None else 0
    system_note = f"Position resolved via resolved outcome {outcome_label}."
    if existing_system_note:
        system_note = f"{existing_system_note} Resolution backfilled after resolved outcome {outcome_label}."

    conn.execute(
        """
        INSERT INTO postmortems (
            position_id, market_id, outcome_label, pnl_usd, pnl_percent,
            forecast_error, entry_error, timing_error, management_error,
            discipline_error, operator_note, system_note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            outcome_label = excluded.outcome_label,
            pnl_usd = COALESCE(postmortems.pnl_usd, excluded.pnl_usd),
            pnl_percent = COALESCE(postmortems.pnl_percent, excluded.pnl_percent),
            forecast_error = excluded.forecast_error,
            entry_error = COALESCE(postmortems.entry_error, excluded.entry_error),
            timing_error = COALESCE(postmortems.timing_error, excluded.timing_error),
            management_error = COALESCE(postmortems.management_error, excluded.management_error),
            discipline_error = COALESCE(postmortems.discipline_error, excluded.discipline_error),
            operator_note = COALESCE(postmortems.operator_note, excluded.operator_note),
            system_note = excluded.system_note,
            updated_at = excluded.updated_at
        """,
        (
            position["id"],
            position["market_id"],
            outcome_label,
            position.get("pnl_usd"),
            position.get("pnl_percent"),
            forecast_error,
            entry_error,
            timing_error,
            management_error,
            discipline_error,
            existing_operator_note,
            system_note,
            resolved_at,
            resolved_at,
        ),
    )


def settle_positions_for_resolution(conn: sqlite3.Connection, market_id: str, outcome: int, resolved_at: str) -> None:
    if outcome not in (0, 1):
        return
    if not table_exists(conn, "positions"):
        return

    ensure_decision_core_schema(conn)

    positions = fetchall_dicts(
        conn,
        """
        SELECT
            id, book_id, market_id, side, status, entry_cost_per_contract,
            size_contracts, notional_usd, review_due_at, close_reason,
            pnl_usd, pnl_percent
        FROM positions
        WHERE market_id = ?
        ORDER BY id ASC
        """,
        (market_id,),
    )
    if not positions:
        return

    resolved_yes_price = _resolved_yes_price(outcome)

    for position in positions:
        if position["status"] == "open":
            payout = _payout_per_contract(str(position["side"]), outcome)
            size_contracts = float(position["size_contracts"] or 0)
            entry_cost_per_contract = float(position["entry_cost_per_contract"] or 0)
            notional_usd = float(position["notional_usd"] or 0)
            pnl_usd = size_contracts * (payout - entry_cost_per_contract)
            pnl_percent = 0.0 if notional_usd == 0 else (pnl_usd / notional_usd) * 100

            conn.execute(
                """
                UPDATE positions
                SET status = 'closed',
                    closed_at = ?,
                    closed_yes_price = ?,
                    close_reason = 'resolved',
                    pnl_usd = ?,
                    pnl_percent = ?
                WHERE id = ?
                """,
                (resolved_at, resolved_yes_price, pnl_usd, pnl_percent, position["id"]),
            )
            conn.execute(
                """
                UPDATE books
                SET current_balance = current_balance + ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (pnl_usd, resolved_at, position["book_id"]),
            )
            conn.execute(
                """
                UPDATE reviews
                SET status = 'cancelled',
                    resolved_at = ?,
                    updated_at = ?
                WHERE position_id = ? AND status IN ('due', 'scheduled')
                """,
                (resolved_at, resolved_at, position["id"]),
            )
            conn.execute(
                """
                INSERT INTO position_events (
                    position_id, decision_id, event_type, event_note,
                    market_price, research_probability, created_at, payload_json
                ) VALUES (?, NULL, 'closed', ?, ?, NULL, ?, ?)
                """,
                (
                    position["id"],
                    f"Position resolved with outcome {'YES' if outcome == 1 else 'NO'}.",
                    resolved_yes_price,
                    resolved_at,
                    json.dumps(
                        {
                            "close_reason": "resolved",
                            "outcome": outcome,
                            "pnl_usd": pnl_usd,
                            "pnl_percent": pnl_percent,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            position["status"] = "closed"
            position["close_reason"] = "resolved"
            position["pnl_usd"] = pnl_usd
            position["pnl_percent"] = pnl_percent

        _upsert_resolution_postmortem(conn, position, outcome=outcome, resolved_at=resolved_at)


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
        settle_positions_for_resolution(conn, str(market_id), outcome, resolved_at)
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
