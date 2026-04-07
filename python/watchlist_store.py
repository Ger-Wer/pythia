from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from python.decision_store import ensure_decision_core_schema

BASE_DIR = Path(__file__).resolve().parent.parent
WATCHLIST_ENGINE_SQL_PATH = BASE_DIR / "sql" / "watchlist_engine.sql"

SHORT_HORIZON_WINDOW = timedelta(hours=12)
NORMAL_HORIZON_WINDOW = timedelta(hours=24)
LONG_HORIZON_WINDOW = timedelta(hours=48)
REVIEW_24H = timedelta(hours=24)
REVIEW_48H = timedelta(hours=48)
REVIEW_72H = timedelta(hours=72)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _prediction_columns(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "predictions"):
        return set()
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}


def _select_prediction_column(columns: set[str], column_name: str) -> str:
    if column_name in columns:
        return column_name
    return f"NULL AS {column_name}"


def ensure_watchlist_engine_schema(conn: sqlite3.Connection) -> None:
    ensure_decision_core_schema(conn)
    sql = WATCHLIST_ENGINE_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def _latest_prediction(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    columns = _prediction_columns(conn)
    if not columns:
        return None
    return _fetchone_dict(
        conn,
        f"""
        SELECT
            id,
            market_id,
            question,
            {_select_prediction_column(columns, "market_price")},
            {_select_prediction_column(columns, "dexter_p")},
            {_select_prediction_column(columns, "naked_p")},
            {_select_prediction_column(columns, "dexter_confidence")},
            {_select_prediction_column(columns, "dexter_reasoning")},
            {_select_prediction_column(columns, "dexter_key_risk")},
            {_select_prediction_column(columns, "research_brief_json")},
            {_select_prediction_column(columns, "end_date")},
            forecast_timestamp
        FROM predictions
        WHERE market_id = ?
          AND COALESCE(is_valid, 1) = 1
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    )


def _latest_signal(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "signals"):
        return None
    return _fetchone_dict(
        conn,
        """
        SELECT
            id,
            market_id,
            recommended_action,
            thesis_summary,
            primary_driver,
            disconfirming_signal,
            confidence,
            research_status
        FROM signals
        WHERE market_id = ?
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    )


def _freshness_policy(end_date: str | None, now_dt: datetime) -> tuple[str, timedelta]:
    end_dt = _parse_iso(end_date)
    if end_dt is None:
        return ("normal_24h", NORMAL_HORIZON_WINDOW)

    remaining = end_dt - now_dt
    if remaining <= timedelta(days=14):
        return ("short_12h", SHORT_HORIZON_WINDOW)
    if remaining >= timedelta(days=90):
        return ("long_48h", LONG_HORIZON_WINDOW)
    return ("normal_24h", NORMAL_HORIZON_WINDOW)


def _is_snapshot_fresh(forecast_timestamp: str | None, freshness_window: timedelta, now_dt: datetime) -> bool:
    forecast_dt = _parse_iso(forecast_timestamp)
    if forecast_dt is None:
        return False
    return now_dt - forecast_dt <= freshness_window


def _initial_thesis_summary(prediction: dict[str, Any] | None, signal: dict[str, Any] | None) -> str:
    if signal and signal.get("thesis_summary"):
        return str(signal["thesis_summary"]).strip()
    if prediction and prediction.get("dexter_reasoning"):
        return str(prediction["dexter_reasoning"]).strip()
    if prediction and prediction.get("question"):
        return str(prediction["question"]).strip()
    return "Deep review ожидает первого тезиса."


def _next_review_due_at(
    *,
    now_dt: datetime,
    end_date: str | None,
    recommended_action: str | None,
    freshness_policy: str,
    has_open_position: bool = False,
) -> str:
    if has_open_position or recommended_action in {"enter_yes", "enter_no"}:
        due_dt = now_dt + REVIEW_24H
    elif freshness_policy == "long_48h":
        due_dt = now_dt + REVIEW_72H
    else:
        due_dt = now_dt + REVIEW_48H

    end_dt = _parse_iso(end_date)
    if end_dt is not None:
        due_dt = min(due_dt, end_dt)
    return due_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_open_position(conn: sqlite3.Connection, market_id: str) -> bool:
    if not _table_exists(conn, "positions"):
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM positions
        WHERE market_id = ?
          AND status = 'open'
        LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    return row is not None


def _parse_research_brief(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        parsed = json.loads(str(payload))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bootstrap_watch_dossier(
    conn: sqlite3.Connection,
    market_id: str,
    note: str = "",
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    ensure_watchlist_engine_schema(conn)

    cleaned_market_id = str(market_id).strip()
    if not cleaned_market_id:
        raise ValueError("market_id is required")

    now_dt = _parse_iso(now_iso) or utc_now()
    now_value = now_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    prediction = _latest_prediction(conn, cleaned_market_id)
    signal = _latest_signal(conn, cleaned_market_id)
    research_brief = _parse_research_brief(prediction.get("research_brief_json") if prediction else None)

    freshness_policy, freshness_window = _freshness_policy(
        prediction.get("end_date") if prediction else None,
        now_dt,
    )
    is_fresh = _is_snapshot_fresh(
        prediction.get("forecast_timestamp") if prediction else None,
        freshness_window,
        now_dt,
    )
    initial_snapshot_state = "reused" if prediction and is_fresh else "pending_deep_review"
    thesis_state = "intact" if initial_snapshot_state == "reused" else "review_required"

    current_market_price = float(prediction["market_price"]) if prediction and prediction.get("market_price") is not None else None
    current_research_probability = (
        float(prediction["dexter_p"])
        if prediction and prediction.get("dexter_p") is not None
        else None
    )
    current_naked_probability = (
        float(prediction["naked_p"])
        if prediction and prediction.get("naked_p") is not None
        else None
    )
    current_confidence = (
        str((signal.get("confidence") if signal else None) or (prediction.get("dexter_confidence") if prediction else None) or "").strip()
        or None
        if prediction or signal
        else None
    )
    current_research_status = (
        str(signal.get("research_status")).strip()
        if signal and signal.get("research_status")
        else ("ON" if current_research_probability is not None else "DEGRADED")
    )
    current_thesis_summary = _initial_thesis_summary(prediction, signal)
    current_primary_driver = None
    if signal and signal.get("primary_driver"):
        current_primary_driver = str(signal["primary_driver"]).strip()
    elif research_brief.get("primary_driver"):
        current_primary_driver = str(research_brief["primary_driver"]).strip()
    elif prediction and prediction.get("dexter_reasoning"):
        current_primary_driver = str(prediction["dexter_reasoning"]).strip()

    current_invalidation_signal = None
    if signal and signal.get("disconfirming_signal"):
        current_invalidation_signal = str(signal["disconfirming_signal"]).strip()
    elif research_brief.get("disconfirming_signal"):
        current_invalidation_signal = str(research_brief["disconfirming_signal"]).strip()
    elif prediction and prediction.get("dexter_key_risk"):
        current_invalidation_signal = str(prediction["dexter_key_risk"]).strip()

    last_status_label = "без изменений" if initial_snapshot_state == "reused" else "нужен deep review"
    last_reason_summary = (
        "Используем свежий snapshot последнего прогноза."
        if initial_snapshot_state == "reused"
        else "Последний snapshot устарел или отсутствует."
    )
    last_action_summary = (
        "Продолжаем автоматическое сопровождение."
        if initial_snapshot_state == "reused"
        else "Поставить рынок в очередь на deep review."
    )
    recommended_action = str(signal.get("recommended_action") if signal else "").strip() or None
    next_review_due_at = _next_review_due_at(
        now_dt=now_dt,
        end_date=prediction.get("end_date") if prediction else None,
        recommended_action=recommended_action,
        freshness_policy=freshness_policy,
        has_open_position=_has_open_position(conn, cleaned_market_id),
    )

    conn.execute(
        """
        INSERT INTO watch_dossiers (
            market_id,
            status,
            created_from_prediction_id,
            created_from_signal_id,
            created_at,
            updated_at,
            first_watched_at,
            last_refresh_at,
            last_deep_review_at,
            next_review_due_at,
            freshness_policy,
            initial_snapshot_state,
            thesis_state,
            current_market_price,
            current_research_probability,
            current_naked_probability,
            current_confidence,
            current_research_status,
            current_thesis_summary,
            current_primary_driver,
            current_invalidation_signal,
            last_status_label,
            last_reason_summary,
            last_action_summary,
            operator_note
        ) VALUES (?, 'active', ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            status = 'active',
            created_from_prediction_id = excluded.created_from_prediction_id,
            created_from_signal_id = excluded.created_from_signal_id,
            updated_at = excluded.updated_at,
            next_review_due_at = excluded.next_review_due_at,
            freshness_policy = excluded.freshness_policy,
            initial_snapshot_state = excluded.initial_snapshot_state,
            thesis_state = excluded.thesis_state,
            current_market_price = excluded.current_market_price,
            current_research_probability = excluded.current_research_probability,
            current_naked_probability = excluded.current_naked_probability,
            current_confidence = excluded.current_confidence,
            current_research_status = excluded.current_research_status,
            current_thesis_summary = excluded.current_thesis_summary,
            current_primary_driver = excluded.current_primary_driver,
            current_invalidation_signal = excluded.current_invalidation_signal,
            last_status_label = excluded.last_status_label,
            last_reason_summary = excluded.last_reason_summary,
            last_action_summary = excluded.last_action_summary,
            operator_note = excluded.operator_note
        """,
        (
            cleaned_market_id,
            prediction.get("id") if prediction else None,
            signal.get("id") if signal else None,
            now_value,
            now_value,
            now_value,
            next_review_due_at,
            freshness_policy,
            initial_snapshot_state,
            thesis_state,
            current_market_price,
            current_research_probability,
            current_naked_probability,
            current_confidence,
            current_research_status,
            current_thesis_summary,
            current_primary_driver,
            current_invalidation_signal,
            last_status_label,
            last_reason_summary,
            last_action_summary,
            str(note or "").strip(),
        ),
    )
    return {
        "market_id": cleaned_market_id,
        "freshness_policy": freshness_policy,
        "initial_snapshot_state": initial_snapshot_state,
        "thesis_state": thesis_state,
        "next_review_due_at": next_review_due_at,
        "current_market_price": current_market_price,
        "current_research_probability": current_research_probability,
        "current_naked_probability": current_naked_probability,
        "current_thesis_summary": current_thesis_summary,
    }


def list_watch_dossiers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "watch_dossiers"):
        return []
    return [
        {
            **row,
            "question": row.get("question"),
        }
        for row in _fetchall_dicts(
            conn,
            """
            SELECT
                d.id,
                d.market_id,
                d.status,
                d.updated_at,
                d.next_review_due_at,
                d.freshness_policy,
                d.initial_snapshot_state,
                d.thesis_state,
                d.current_market_price,
                d.current_research_probability,
                d.current_naked_probability,
                d.current_confidence,
                d.current_research_status,
                d.current_thesis_summary,
                d.current_primary_driver,
                d.current_invalidation_signal,
                d.last_status_label,
                d.last_reason_summary,
                d.last_action_summary,
                d.operator_note,
                (
                    SELECT p.question
                    FROM predictions p
                    WHERE p.market_id = d.market_id
                      AND COALESCE(p.is_valid, 1) = 1
                    ORDER BY p.forecast_timestamp DESC, p.id DESC
                    LIMIT 1
                ) AS question
            FROM watch_dossiers d
            ORDER BY d.updated_at DESC, d.market_id ASC
            """,
        )
    ]


def list_watch_updates(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    if not _table_exists(conn, "watch_updates") or not _table_exists(conn, "watch_dossiers"):
        return []
    return _fetchall_dicts(
        conn,
        """
        SELECT
            u.id,
            d.market_id,
            u.created_at,
            u.severity,
            u.status_label,
            u.reason_summary,
            u.action_summary,
            u.market_price_before,
            u.market_price_after,
            u.research_probability_before,
            u.research_probability_after,
            u.delivered_to_dashboard_at,
            u.delivered_to_telegram_at,
            (
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = d.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ) AS question
        FROM watch_updates u
        JOIN watch_dossiers d ON d.id = u.watch_dossier_id
        ORDER BY u.created_at DESC, u.id DESC
        LIMIT ?
        """,
        (limit,),
    )


def list_pending_watch_updates(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict[str, Any]]:
    if not _table_exists(conn, "watch_updates") or not _table_exists(conn, "watch_dossiers"):
        return []
    return _fetchall_dicts(
        conn,
        """
        SELECT
            u.id,
            d.market_id,
            u.created_at,
            u.severity,
            u.status_label,
            u.reason_summary,
            u.action_summary,
            (
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = d.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ) AS question
        FROM watch_updates u
        JOIN watch_dossiers d ON d.id = u.watch_dossier_id
        WHERE u.delivered_to_telegram_at IS NULL
        ORDER BY u.created_at ASC, u.id ASC
        LIMIT ?
        """,
        (limit,),
    )


def mark_watch_updates_delivered(
    conn: sqlite3.Connection,
    update_ids: list[int],
    *,
    delivered_at: str | None = None,
) -> int:
    if not update_ids:
        return 0

    timestamp = delivered_at or utc_now_iso()
    placeholders = ",".join("?" for _ in update_ids)
    cursor = conn.execute(
        f"""
        UPDATE watch_updates
        SET delivered_to_telegram_at = ?
        WHERE id IN ({placeholders})
        """,
        (timestamp, *update_ids),
    )
    conn.commit()
    return int(cursor.rowcount or 0)
