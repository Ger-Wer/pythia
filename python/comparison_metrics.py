"""Operator vs Shadow comparison metrics."""

from __future__ import annotations

import sqlite3
from typing import Any


def _fetchone_dict(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Execute query and return single row as dict, or None."""
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return dict(row)


def _fetchall_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute query and return all rows as list of dicts."""
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_agreement_rate(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """
    Calculate agreement rate between operator and shadow decisions.
    
    Returns:
        {
            "total_signals": int,
            "operator_decisions": int,
            "shadow_decisions": int,
            "agreement_rate": float | None
        }
    """
    params: list[Any] = []
    date_filter = ""
    
    if from_date:
        date_filter += " AND s.created_at >= ?"
        params.append(from_date)
    if to_date:
        date_filter += " AND s.created_at <= ?"
        params.append(to_date)
    
    sql = f"""
        WITH signal_decisions AS (
            SELECT 
                s.id as signal_id,
                s.market_id,
                s.created_at,
                MAX(CASE WHEN b.mode = 'operator' THEN d.decision_type END) as operator_decision,
                MAX(CASE WHEN b.mode = 'shadow' THEN d.decision_type END) as shadow_decision
            FROM signals s
            LEFT JOIN decisions d ON s.id = d.signal_id
            LEFT JOIN books b ON d.book_id = b.id
            WHERE 1=1 {date_filter}
            GROUP BY s.id
        )
        SELECT 
            COUNT(*) as total_signals,
            COUNT(CASE WHEN operator_decision IS NOT NULL THEN 1 END) as operator_decisions,
            COUNT(CASE WHEN shadow_decision IS NOT NULL THEN 1 END) as shadow_decisions,
            COUNT(CASE 
                WHEN operator_decision IS NOT NULL 
                AND shadow_decision IS NOT NULL 
                AND operator_decision = shadow_decision 
                THEN 1 
            END) * 100.0 / NULLIF(COUNT(CASE 
                WHEN operator_decision IS NOT NULL 
                AND shadow_decision IS NOT NULL 
                THEN 1 
            END), 0) as agreement_rate
        FROM signal_decisions
    """
    
    result = _fetchone_dict(conn, sql, tuple(params))
    return result or {
        "total_signals": 0,
        "operator_decisions": 0,
        "shadow_decisions": 0,
        "agreement_rate": None,
    }


def get_mode_performance(
    conn: sqlite3.Connection,
    *,
    mode: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """
    Get performance metrics for a specific mode (operator or shadow).
    
    Returns:
        {
            "total_positions": int,
            "closed_positions": int,
            "win_rate": float | None,
            "avg_pnl_usd": float | None,
            "total_pnl_usd": float | None
        }
    """
    params: list[Any] = [mode]
    date_filter = ""
    
    if from_date:
        date_filter += " AND opened_at >= ?"
        params.append(from_date)
    if to_date:
        date_filter += " AND opened_at <= ?"
        params.append(to_date)
    
    sql = f"""
        SELECT 
            COUNT(*) as total_positions,
            COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_positions,
            CAST(COUNT(CASE WHEN status = 'closed' AND pnl_usd > 0 THEN 1 END) AS REAL) * 100.0 / 
                NULLIF(COUNT(CASE WHEN status = 'closed' THEN 1 END), 0) as win_rate,
            AVG(CASE WHEN status = 'closed' THEN pnl_usd END) as avg_pnl_usd,
            SUM(CASE WHEN status = 'closed' THEN pnl_usd END) as total_pnl_usd
        FROM positions
        WHERE mode = ? {date_filter}
    """
    
    result = _fetchone_dict(conn, sql, tuple(params))
    return result or {
        "total_positions": 0,
        "closed_positions": 0,
        "win_rate": None,
        "avg_pnl_usd": None,
        "total_pnl_usd": None,
    }


def get_divergence_cases(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Find signals where operator and shadow made different decisions.
    
    Returns list of divergence cases with outcomes.
    """
    params: list[Any] = []
    date_filter = ""
    
    if from_date:
        date_filter += " AND s.created_at >= ?"
        params.append(from_date)
    if to_date:
        date_filter += " AND s.created_at <= ?"
        params.append(to_date)
    
    params.append(limit)
    
    sql = f"""
        WITH signal_decisions AS (
            SELECT 
                s.id as signal_id,
                s.market_id,
                s.question,
                s.created_at as signal_created_at,
                MAX(CASE WHEN b.mode = 'operator' THEN d.decision_type END) as operator_decision,
                MAX(CASE WHEN b.mode = 'shadow' THEN d.decision_type END) as shadow_decision,
                MAX(CASE WHEN b.mode = 'operator' THEN d.id END) as operator_decision_id,
                MAX(CASE WHEN b.mode = 'shadow' THEN d.id END) as shadow_decision_id
            FROM signals s
            LEFT JOIN decisions d ON s.id = d.signal_id
            LEFT JOIN books b ON d.book_id = b.id
            WHERE 1=1 {date_filter}
            GROUP BY s.id
        )
        SELECT 
            sd.signal_id,
            sd.market_id,
            sd.question,
            sd.signal_created_at,
            sd.operator_decision,
            sd.shadow_decision,
            op.id as operator_position_id,
            op.pnl_usd as operator_pnl_usd,
            op.status as operator_status,
            sp.id as shadow_position_id,
            sp.pnl_usd as shadow_pnl_usd,
            sp.status as shadow_status,
            CASE
                WHEN op.status = 'closed' AND sp.status = 'closed' THEN
                    CASE 
                        WHEN op.pnl_usd > sp.pnl_usd THEN 'operator_better'
                        WHEN sp.pnl_usd > op.pnl_usd THEN 'shadow_better'
                        ELSE 'equal'
                    END
                WHEN op.status = 'closed' OR sp.status = 'closed' THEN 'partial_closed'
                ELSE 'pending'
            END as outcome
        FROM signal_decisions sd
        LEFT JOIN positions op ON sd.operator_decision_id = op.opened_by_decision_id
        LEFT JOIN positions sp ON sd.shadow_decision_id = sp.opened_by_decision_id
        WHERE sd.operator_decision != sd.shadow_decision
            OR (sd.operator_decision IS NOT NULL AND sd.shadow_decision IS NULL)
            OR (sd.operator_decision IS NULL AND sd.shadow_decision IS NOT NULL)
        ORDER BY sd.signal_created_at DESC
        LIMIT ?
    """
    
    return _fetchall_dicts(conn, sql, tuple(params))


def get_cumulative_pnl_chart(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Get cumulative P&L over time for both operator and shadow.
    
    Returns list of points with date, operator_cumulative_pnl, shadow_cumulative_pnl.
    """
    params_operator: list[Any] = ["operator"]
    params_shadow: list[Any] = ["shadow"]
    date_filter = ""
    
    if from_date:
        date_filter += " AND closed_at >= ?"
        params_operator.append(from_date)
        params_shadow.append(from_date)
    if to_date:
        date_filter += " AND closed_at <= ?"
        params_operator.append(to_date)
        params_shadow.append(to_date)
    
    # Get operator cumulative P&L
    sql_operator = f"""
        SELECT 
            DATE(closed_at) as date,
            SUM(SUM(pnl_usd)) OVER (ORDER BY DATE(closed_at)) as cumulative_pnl
        FROM positions
        WHERE mode = ? 
            AND status = 'closed' 
            AND pnl_usd IS NOT NULL
            {date_filter}
        GROUP BY DATE(closed_at)
        ORDER BY date
    """
    
    operator_data = _fetchall_dicts(conn, sql_operator, tuple(params_operator))
    
    # Get shadow cumulative P&L
    sql_shadow = f"""
        SELECT 
            DATE(closed_at) as date,
            SUM(SUM(pnl_usd)) OVER (ORDER BY DATE(closed_at)) as cumulative_pnl
        FROM positions
        WHERE mode = ? 
            AND status = 'closed' 
            AND pnl_usd IS NOT NULL
            {date_filter}
        GROUP BY DATE(closed_at)
        ORDER BY date
    """
    
    shadow_data = _fetchall_dicts(conn, sql_shadow, tuple(params_shadow))
    
    # Merge data by date
    operator_dict = {row["date"]: row["cumulative_pnl"] for row in operator_data}
    shadow_dict = {row["date"]: row["cumulative_pnl"] for row in shadow_data}
    
    all_dates = sorted(set(operator_dict.keys()) | set(shadow_dict.keys()))
    
    result = []
    operator_last = 0.0
    shadow_last = 0.0
    
    for date in all_dates:
        if date in operator_dict:
            operator_last = operator_dict[date]
        if date in shadow_dict:
            shadow_last = shadow_dict[date]
        
        result.append({
            "date": date,
            "operator_cumulative_pnl": operator_last,
            "shadow_cumulative_pnl": shadow_last,
        })
    
    return result


def get_operator_vs_shadow_comparison(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """
    Get complete operator vs shadow comparison data.
    
    Returns full comparison payload with summary, divergence_cases, and comparison_chart.
    """
    # Get agreement rate
    agreement = get_agreement_rate(conn, from_date=from_date, to_date=to_date)
    
    # Get operator performance
    operator_perf = get_mode_performance(conn, mode="operator", from_date=from_date, to_date=to_date)
    
    # Get shadow performance
    shadow_perf = get_mode_performance(conn, mode="shadow", from_date=from_date, to_date=to_date)
    
    # Calculate performance delta
    performance_delta = {
        "win_rate_delta": (
            (operator_perf["win_rate"] or 0) - (shadow_perf["win_rate"] or 0)
            if operator_perf["win_rate"] is not None and shadow_perf["win_rate"] is not None
            else None
        ),
        "avg_pnl_delta": (
            (operator_perf["avg_pnl_usd"] or 0) - (shadow_perf["avg_pnl_usd"] or 0)
            if operator_perf["avg_pnl_usd"] is not None and shadow_perf["avg_pnl_usd"] is not None
            else None
        ),
        "total_pnl_delta": (
            (operator_perf["total_pnl_usd"] or 0) - (shadow_perf["total_pnl_usd"] or 0)
            if operator_perf["total_pnl_usd"] is not None and shadow_perf["total_pnl_usd"] is not None
            else None
        ),
    }
    
    # Get divergence cases
    divergence_cases = get_divergence_cases(conn, from_date=from_date, to_date=to_date, limit=50)
    
    # Get comparison chart
    comparison_chart = get_cumulative_pnl_chart(conn, from_date=from_date, to_date=to_date)
    
    return {
        "summary": {
            **agreement,
            "operator_performance": operator_perf,
            "shadow_performance": shadow_perf,
            "performance_delta": performance_delta,
        },
        "divergence_cases": divergence_cases,
        "comparison_chart": comparison_chart,
    }
