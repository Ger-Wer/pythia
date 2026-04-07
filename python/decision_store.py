from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DECISION_CORE_SQL_PATH = BASE_DIR / "sql" / "decision_core.sql"

SHADOW_BOOK_NAME = "shadow_default"
OPERATOR_BOOK_NAME = "operator_default"
POSITION_REVIEW_WINDOW = timedelta(hours=24)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetchone_dict(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [str(description[0]) for description in cursor.description or []]
    return {columns[index]: row[index] for index in range(len(columns))}


def _fetchall_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    columns = [str(description[0]) for description in cursor.description or []]
    return [
        {columns[index]: row[index] for index in range(len(columns))}
        for row in rows
    ]


def ensure_decision_core_schema(conn: sqlite3.Connection) -> None:
    sql = DECISION_CORE_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _book(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    row = _fetchone_dict(
        conn,
        """
        SELECT id, name, mode, currency, starting_balance, current_balance,
               max_risk_per_trade_pct, max_total_exposure_pct, status
        FROM books
        WHERE name = ?
        LIMIT 1
        """,
        (name,),
    )
    if not row:
        raise LookupError(f"BOOK_NOT_FOUND:{name}")
    return row


def _signal(conn: sqlite3.Connection, signal_id: int) -> dict[str, Any]:
    row = _fetchone_dict(
        conn,
        """
        SELECT *
        FROM signals
        WHERE id = ?
        LIMIT 1
        """,
        (signal_id,),
    )
    if not row:
        raise LookupError(f"SIGNAL_NOT_FOUND:{signal_id}")
    return row


def _latest_signal_for_market(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    return _fetchone_dict(
        conn,
        """
        SELECT *
        FROM signals
        WHERE market_id = ?
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    )


def _latest_prediction_for_market(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    return _fetchone_dict(
        conn,
        """
        SELECT id, market_id, question, category, market_price, dexter_p, naked_p,
               dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
               research_brief_json, volume, end_date, forecast_timestamp
        FROM predictions
        WHERE market_id = ? AND is_valid = 1
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    )


def _open_exposure_notional(conn: sqlite3.Connection, book_id: int) -> float:
    row = _fetchone_dict(
        conn,
        """
        SELECT COALESCE(SUM(notional_usd), 0) AS total
        FROM positions
        WHERE book_id = ? AND status = 'open'
        """,
        (book_id,),
    )
    return float(row["total"] or 0)


def _next_thesis_cycle(conn: sqlite3.Connection, book_id: int, market_id: str) -> int:
    row = _fetchone_dict(
        conn,
        """
        SELECT COALESCE(MAX(thesis_cycle_number), 0) AS max_cycle
        FROM positions
        WHERE book_id = ? AND market_id = ?
        """,
        (book_id, market_id),
    )
    return int(row["max_cycle"] or 0) + 1


def _position_value_per_contract(side: str, yes_price: float) -> float:
    return yes_price / 100 if side == "yes" else (100 - yes_price) / 100


def _review_due_at(now_iso: str, end_date: str | None) -> str:
    due = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) + POSITION_REVIEW_WINDOW
    if not end_date:
        return due.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
    chosen = min(due, end_dt)
    return chosen.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _position_summary_rows(conn: sqlite3.Connection, *, market_id: str | None = None, status: str = "open") -> list[dict[str, Any]]:
    where = ["p.status = ?"]
    params: list[Any] = [status]
    if market_id is not None:
        where.append("p.market_id = ?")
        params.append(market_id)

    return _fetchall_dicts(
        conn,
        f"""
        SELECT
            p.id,
            p.market_id,
            p.mode,
            p.side,
            p.status,
            p.entry_yes_price,
            p.notional_usd,
            p.review_due_at,
            p.opened_at,
            p.closed_at,
            p.closed_yes_price,
            p.close_reason,
            p.pnl_usd,
            p.pnl_percent,
            p.thesis_cycle_number,
            pr.question
        FROM positions p
        LEFT JOIN predictions pr
          ON pr.id = (
            SELECT p2.id
            FROM predictions p2
            WHERE p2.market_id = p.market_id AND p2.is_valid = 1
            ORDER BY p2.forecast_timestamp DESC, p2.id DESC
            LIMIT 1
          )
        WHERE {" AND ".join(where)}
        ORDER BY p.opened_at DESC, p.id DESC
        """,
        tuple(params),
    )


def _review_rows(conn: sqlite3.Connection, *, market_id: str | None = None, status: str = "due") -> list[dict[str, Any]]:
    where = ["status = ?"]
    params: list[Any] = [status]
    if market_id is not None:
        where.append("market_id = ?")
        params.append(market_id)

    return _fetchall_dicts(
        conn,
        f"""
        SELECT id, position_id, market_id, review_type, due_at, status,
               review_note, resolved_at, created_at, updated_at
        FROM reviews
        WHERE {" AND ".join(where)}
        ORDER BY due_at ASC, id ASC
        """,
        tuple(params),
    )


def _recent_decisions(conn: sqlite3.Connection, market_id: str, limit: int = 5) -> list[dict[str, Any]]:
    return _fetchall_dicts(
        conn,
        """
        SELECT
            d.id,
            d.signal_id,
            d.market_id,
            d.actor_type,
            d.decision_type,
            d.decision_reason,
            d.operator_confidence,
            d.confirmation_criteria,
            d.invalidation_criteria,
            d.review_due_at,
            d.status,
            d.created_at,
            p.id AS position_id
        FROM decisions d
        LEFT JOIN positions p ON p.opened_by_decision_id = d.id
        WHERE d.market_id = ?
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
        """,
        (market_id, limit),
    )


def _latest_postmortem(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    return _fetchone_dict(
        conn,
        """
        SELECT
            position_id,
            market_id,
            outcome_label,
            pnl_usd,
            pnl_percent,
            forecast_error,
            entry_error,
            timing_error,
            management_error,
            discipline_error,
            operator_note,
            system_note,
            created_at,
            updated_at
        FROM postmortems
        WHERE market_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    )


def _close_position(
    conn: sqlite3.Connection,
    position: dict[str, Any],
    *,
    closed_at: str,
    closed_yes_price: float,
    close_reason: str,
    note: str,
) -> dict[str, Any]:
    entry_cost = float(position["entry_cost_per_contract"])
    size_contracts = float(position["size_contracts"])
    notional_usd = float(position["notional_usd"])
    side = str(position["side"])
    book_id = int(position["book_id"])
    position_id = int(position["id"])
    market_id = str(position["market_id"])

    exit_value = _position_value_per_contract(side, float(closed_yes_price))
    pnl_usd = size_contracts * (exit_value - entry_cost)
    pnl_percent = 0.0 if notional_usd == 0 else (pnl_usd / notional_usd) * 100

    conn.execute(
        """
        UPDATE positions
        SET status = 'closed',
            closed_at = ?,
            closed_yes_price = ?,
            close_reason = ?,
            pnl_usd = ?,
            pnl_percent = ?
        WHERE id = ?
        """,
        (closed_at, closed_yes_price, close_reason, pnl_usd, pnl_percent, position_id),
    )
    conn.execute(
        """
        UPDATE books
        SET current_balance = current_balance + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (pnl_usd, closed_at, book_id),
    )
    conn.execute(
        """
        UPDATE reviews
        SET status = 'cancelled',
            review_note = COALESCE(review_note, ?),
            resolved_at = ?,
            updated_at = ?
        WHERE position_id = ? AND status IN ('due', 'scheduled')
        """,
        (note, closed_at, closed_at, position_id),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            position_id, decision_id, event_type, event_note,
            market_price, research_probability, created_at, payload_json
        ) VALUES (?, NULL, 'closed', ?, ?, NULL, ?, ?)
        """,
        (
            position_id,
            note,
            closed_yes_price,
            closed_at,
            json.dumps(
                {
                    "close_reason": close_reason,
                    "pnl_usd": pnl_usd,
                    "pnl_percent": pnl_percent,
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO postmortems (
            position_id, market_id, outcome_label, pnl_usd, pnl_percent,
            forecast_error, entry_error, timing_error, management_error,
            discipline_error, operator_note, system_note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            outcome_label = excluded.outcome_label,
            pnl_usd = excluded.pnl_usd,
            pnl_percent = excluded.pnl_percent,
            timing_error = excluded.timing_error,
            management_error = excluded.management_error,
            discipline_error = excluded.discipline_error,
            operator_note = COALESCE(excluded.operator_note, postmortems.operator_note),
            system_note = COALESCE(excluded.system_note, postmortems.system_note),
            updated_at = excluded.updated_at
        """,
        (
            position_id,
            market_id,
            "early_exit" if close_reason != "resolved" else "resolved",
            pnl_usd,
            pnl_percent,
            None,
            None,
            1 if close_reason != "resolved" else 0,
            0,
            0,
            note if close_reason == "manual_exit" else None,
            f"Position closed via {close_reason}.",
            closed_at,
            closed_at,
        ),
    )
    conn.commit()
    return {
        "position_id": position_id,
        "status": "closed",
        "close_reason": close_reason,
        "closed_yes_price": float(closed_yes_price),
        "pnl_usd": pnl_usd,
        "pnl_percent": pnl_percent,
    }


def create_manual_decision(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    decision_type: str,
    decision_reason: str,
    operator_confidence: str | None,
    confirmation_criteria: str | None,
    invalidation_criteria: str | None,
    open_position: bool,
    review_due_at: str | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    ensure_decision_core_schema(conn)

    signal = _signal(conn, signal_id)
    book = _book(conn, OPERATOR_BOOK_NAME)
    created_at = created_at or utc_now_iso()
    due_at = review_due_at or _review_due_at(created_at, None)

    book_id = int(book["id"])
    market_id = str(signal["market_id"])
    position_id: int | None = None
    side: str | None = None
    entry_yes_price: float | None = None
    entry_cost_per_contract: float | None = None
    notional_usd: float | None = None
    size_contracts: float | None = None
    thesis_cycle_number: int | None = None

    if open_position:
        if decision_type not in {"enter_yes", "enter_no"}:
            raise ValueError("open_position requires enter_yes or enter_no decision")

        existing = _fetchone_dict(
            conn,
            """
            SELECT id
            FROM positions
            WHERE market_id = ? AND book_id = ? AND mode = 'operator' AND status = 'open'
            LIMIT 1
            """,
            (market_id, book_id),
        )
        if existing:
            raise ValueError("operator position already open for this market")

        open_exposure = _open_exposure_notional(conn, book_id)
        risk_budget = float(book["current_balance"]) * float(book["max_risk_per_trade_pct"])
        remaining_exposure = float(book["current_balance"]) * float(book["max_total_exposure_pct"]) - open_exposure
        notional_usd = min(risk_budget, remaining_exposure)
        if notional_usd <= 0:
            raise ValueError("operator book has no remaining exposure budget")

        side = "yes" if decision_type == "enter_yes" else "no"
        entry_yes_price = float(signal["market_price"])
        entry_cost_per_contract = _position_value_per_contract(side, entry_yes_price)
        if entry_cost_per_contract <= 0:
            raise ValueError("invalid entry cost per contract")

        size_contracts = notional_usd / entry_cost_per_contract
        thesis_cycle_number = _next_thesis_cycle(conn, book_id, market_id)

    conn.execute("SAVEPOINT manual_decision")
    try:
        conn.execute(
            """
            INSERT INTO decisions (
                signal_id, book_id, market_id, actor_type, decision_type, decision_reason,
                operator_confidence, confirmation_criteria, invalidation_criteria,
                review_due_at, status, created_at
            ) VALUES (?, ?, ?, 'operator', ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                signal_id,
                book_id,
                market_id,
                decision_type,
                decision_reason,
                operator_confidence,
                confirmation_criteria,
                invalidation_criteria,
                due_at,
                created_at,
            ),
        )
        decision_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        if open_position:
            conn.execute(
                """
                INSERT INTO positions (
                    book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
                    entry_yes_price, entry_cost_per_contract, entry_zone_min, entry_zone_max,
                    size_contracts, notional_usd, review_due_at, opened_at, thesis_cycle_number
                ) VALUES (?, ?, ?, ?, 'operator', ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    market_id,
                    signal_id,
                    decision_id,
                    side,
                    entry_yes_price,
                    entry_cost_per_contract,
                    signal.get("entry_zone_min"),
                    signal.get("entry_zone_max"),
                    size_contracts,
                    notional_usd,
                    due_at,
                    created_at,
                    thesis_cycle_number,
                ),
            )
            position_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
            """
            INSERT INTO position_events (
                position_id, decision_id, event_type, event_note,
                market_price, research_probability, created_at, payload_json
            ) VALUES (?, ?, 'opened', ?, ?, ?, ?, ?)
            """,
                (
                    position_id,
                    decision_id,
                    "Operator paper position opened from manual decision.",
                    signal.get("market_price"),
                    signal.get("research_probability"),
                    created_at,
                    json.dumps(
                        {
                            "decision_type": decision_type,
                            "notional_usd": notional_usd,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        conn.execute("RELEASE SAVEPOINT manual_decision")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT manual_decision")
        conn.execute("RELEASE SAVEPOINT manual_decision")
        raise

    conn.commit()
    return {
        "ok": True,
        "decision_id": decision_id,
        "position_id": position_id,
        "market_id": str(signal["market_id"]),
    }


def close_position_manually(conn: sqlite3.Connection, position_id: int, note: str = "") -> dict[str, Any]:
    ensure_decision_core_schema(conn)
    position = _fetchone_dict(
        conn,
        """
        SELECT *
        FROM positions
        WHERE id = ?
        LIMIT 1
        """,
        (position_id,),
    )
    if not position:
        raise LookupError(f"POSITION_NOT_FOUND:{position_id}")
    if position["status"] != "open":
        return {
            "position_id": position_id,
            "status": str(position["status"]),
            "close_reason": position.get("close_reason"),
        }

    latest_signal = _latest_signal_for_market(conn, str(position["market_id"]))
    latest_prediction = _latest_prediction_for_market(conn, str(position["market_id"]))
    if latest_signal and latest_signal.get("market_price") is not None:
        closed_yes_price = float(latest_signal["market_price"])
    elif latest_prediction and latest_prediction.get("market_price") is not None:
        closed_yes_price = float(latest_prediction["market_price"])
    else:
        closed_yes_price = float(position["entry_yes_price"])

    return _close_position(
        conn,
        position,
        closed_at=utc_now_iso(),
        closed_yes_price=closed_yes_price,
        close_reason="manual_exit",
        note=note or "Manual operator exit.",
    )


def complete_review(conn: sqlite3.Connection, review_id: int, note: str = "") -> dict[str, Any]:
    ensure_decision_core_schema(conn)
    review = _fetchone_dict(
        conn,
        """
        SELECT id, position_id, market_id, status
        FROM reviews
        WHERE id = ?
        LIMIT 1
        """,
        (review_id,),
    )
    if not review:
        raise LookupError(f"REVIEW_NOT_FOUND:{review_id}")

    resolved_at = utc_now_iso()
    conn.execute(
        """
        UPDATE reviews
        SET status = 'done',
            review_note = CASE
                WHEN COALESCE(?, '') = '' THEN review_note
                ELSE ?
            END,
            resolved_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (note, note, resolved_at, resolved_at, review_id),
    )
    conn.commit()
    return {
        "ok": True,
        "review_id": review_id,
        "status": "done",
        "market_id": str(review["market_id"]),
    }


def get_market_workspace(conn: sqlite3.Connection, market_id: str) -> dict[str, Any]:
    latest_signal = _latest_signal_for_market(conn, market_id)
    return {
        "market_id": market_id,
        "signal": latest_signal,
        "open_positions": _position_summary_rows(conn, market_id=market_id, status="open") if _table_exists(conn, "positions") else [],
        "due_reviews": _review_rows(conn, market_id=market_id, status="due") if _table_exists(conn, "reviews") else [],
        "recent_decisions": _recent_decisions(conn, market_id) if _table_exists(conn, "decisions") else [],
        "latest_postmortem": _latest_postmortem(conn, market_id) if _table_exists(conn, "postmortems") else None,
    }


def list_open_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "positions"):
        return []
    return _position_summary_rows(conn, status="open")


def list_due_reviews(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "reviews"):
        return []
    return _review_rows(conn, status="due")


def get_decision_core_counts(conn: sqlite3.Connection) -> dict[str, int]:
    if not _table_exists(conn, "positions") or not _table_exists(conn, "reviews"):
        return {
            "open_shadow_positions": 0,
            "open_operator_positions": 0,
            "due_reviews_count": 0,
        }

    shadow = _fetchone_dict(
        conn,
        "SELECT COUNT(*) AS total FROM positions WHERE mode = 'shadow' AND status = 'open'",
    )
    operator = _fetchone_dict(
        conn,
        "SELECT COUNT(*) AS total FROM positions WHERE mode = 'operator' AND status = 'open'",
    )
    due = _fetchone_dict(
        conn,
        "SELECT COUNT(*) AS total FROM reviews WHERE status = 'due'",
    )
    return {
        "open_shadow_positions": int(shadow["total"] or 0) if shadow else 0,
        "open_operator_positions": int(operator["total"] or 0) if operator else 0,
        "due_reviews_count": int(due["total"] or 0) if due else 0,
    }
