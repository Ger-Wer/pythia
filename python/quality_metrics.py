"""Quality metrics and shadow performance analytics."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent


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
    return [{columns[index]: row[index] for index in range(len(columns))} for row in rows]


def get_shadow_performance_summary(
    conn: sqlite3.Connection,
    *,
    mode: str = "shadow",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Get summary metrics for shadow/operator performance."""
    summary = _fetchone_dict(
        conn,
        """
        SELECT 
          COUNT(*) as total_positions,
          COUNT(CASE WHEN status = 'open' THEN 1 END) as open_positions,
          COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_positions,
          CAST(COUNT(CASE WHEN status = 'closed' AND pnl_usd > 0 THEN 1 END) AS REAL) * 100.0 / 
            NULLIF(COUNT(CASE WHEN status = 'closed' THEN 1 END), 0) as win_rate,
          AVG(CASE WHEN status = 'closed' THEN pnl_usd END) as avg_pnl_usd,
          SUM(CASE WHEN status = 'closed' THEN pnl_usd END) as total_pnl_usd
        FROM positions
        WHERE mode = ?
          AND (? IS NULL OR opened_at >= ?)
          AND (? IS NULL OR opened_at <= ?)
        """,
        (mode, from_date, from_date, to_date, to_date),
    )
    
    if summary is None:
        return {
            "total_positions": 0,
            "open_positions": 0,
            "closed_positions": 0,
            "win_rate": 0.0,
            "avg_pnl_usd": 0.0,
            "total_pnl_usd": 0.0,
        }
    
    return {
        "total_positions": int(summary.get("total_positions") or 0),
        "open_positions": int(summary.get("open_positions") or 0),
        "closed_positions": int(summary.get("closed_positions") or 0),
        "win_rate": float(summary.get("win_rate") or 0.0),
        "avg_pnl_usd": float(summary.get("avg_pnl_usd") or 0.0),
        "total_pnl_usd": float(summary.get("total_pnl_usd") or 0.0),
    }


def get_best_position(
    conn: sqlite3.Connection,
    *,
    mode: str = "shadow",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any] | None:
    """Get best performing position."""
    result = _fetchone_dict(
        conn,
        """
        SELECT market_id, pnl_usd, pnl_percent
        FROM positions
        WHERE mode = ? 
          AND status = 'closed'
          AND pnl_usd IS NOT NULL
          AND (? IS NULL OR opened_at >= ?)
          AND (? IS NULL OR opened_at <= ?)
        ORDER BY pnl_usd DESC
        LIMIT 1
        """,
        (mode, from_date, from_date, to_date, to_date),
    )
    
    if result is None:
        return None
    
    return {
        "market_id": str(result.get("market_id") or ""),
        "pnl_usd": float(result.get("pnl_usd") or 0.0),
        "pnl_percent": float(result.get("pnl_percent") or 0.0),
    }


def get_worst_position(
    conn: sqlite3.Connection,
    *,
    mode: str = "shadow",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any] | None:
    """Get worst performing position."""
    result = _fetchone_dict(
        conn,
        """
        SELECT market_id, pnl_usd, pnl_percent
        FROM positions
        WHERE mode = ? 
          AND status = 'closed'
          AND pnl_usd IS NOT NULL
          AND (? IS NULL OR opened_at >= ?)
          AND (? IS NULL OR opened_at <= ?)
        ORDER BY pnl_usd ASC
        LIMIT 1
        """,
        (mode, from_date, from_date, to_date, to_date),
    )
    
    if result is None:
        return None
    
    return {
        "market_id": str(result.get("market_id") or ""),
        "pnl_usd": float(result.get("pnl_usd") or 0.0),
        "pnl_percent": float(result.get("pnl_percent") or 0.0),
    }


def get_equity_curve(
    conn: sqlite3.Connection,
    *,
    mode: str = "shadow",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Get equity curve data (cumulative P&L over time)."""
    return _fetchall_dicts(
        conn,
        """
        SELECT 
          DATE(closed_at) as date,
          SUM(pnl_usd) OVER (ORDER BY closed_at) as cumulative_pnl
        FROM positions
        WHERE mode = ?
          AND status = 'closed'
          AND closed_at IS NOT NULL
          AND pnl_usd IS NOT NULL
          AND (? IS NULL OR closed_at >= ?)
          AND (? IS NULL OR closed_at <= ?)
        ORDER BY closed_at
        """,
        (mode, from_date, from_date, to_date, to_date),
    )


def get_positions_list(
    conn: sqlite3.Connection,
    *,
    mode: str = "shadow",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get list of positions with P&L data."""
    return _fetchall_dicts(
        conn,
        """
        SELECT 
          id,
          market_id,
          mode,
          status,
          pnl_usd,
          pnl_percent,
          opened_at,
          closed_at
        FROM positions
        WHERE mode = ?
          AND (? IS NULL OR opened_at >= ?)
          AND (? IS NULL OR opened_at <= ?)
        ORDER BY opened_at DESC
        LIMIT ?
        """,
        (mode, from_date, from_date, to_date, to_date, limit),
    )


def get_shadow_performance(
    conn: sqlite3.Connection,
    *,
    mode: str = "shadow",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Get complete shadow performance data."""
    summary = get_shadow_performance_summary(conn, mode=mode, from_date=from_date, to_date=to_date)
    best_position = get_best_position(conn, mode=mode, from_date=from_date, to_date=to_date)
    worst_position = get_worst_position(conn, mode=mode, from_date=from_date, to_date=to_date)
    equity_curve = get_equity_curve(conn, mode=mode, from_date=from_date, to_date=to_date)
    positions = get_positions_list(conn, mode=mode, from_date=from_date, to_date=to_date)
    
    return {
        "summary": {
            **summary,
            "best_position": best_position,
            "worst_position": worst_position,
        },
        "equity_curve": equity_curve,
        "positions": positions,
    }
