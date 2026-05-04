"""Weekly digest aggregator (gate #11).

This module composes a structured weekly snapshot of Pythia's research and
operator-decision discipline that the Telegram bot ships every Sunday
20:00 UTC (see ``supercronic.crontab``).

The digest is split into pure, well-tested functions:

* :func:`compose_weekly_digest` reads the production DB and returns a
  ``dict`` of metrics suitable for either Markdown rendering or future web
  surfacing (no formatting concerns inside the aggregator).
* :func:`format_weekly_digest_markdown` renders that dict to a
  Telegram-Markdown string (``parse_mode=Markdown``).

Schema-tolerance is a hard requirement: the function is run against
production DBs that may not have ``decision_core`` tables yet (early
deployments) or may be missing optional columns. Every section degrades
to ``нет данных`` rather than raising. Tests in
``python/test_weekly_digest.py`` exercise empty / predictions-only / full
DB shapes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

WEEK_DAYS = 7
POSTMORTEM_WINDOW_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _valid_filter(conn: sqlite3.Connection, alias: str = "") -> str:
    """Returns ``" AND <alias.>is_valid = 1"`` if predictions has the column.

    Older DB snapshots don't have ``is_valid``; in that case we degrade to
    no filter, matching the existing convention in
    ``python/telegram_bot.py``.
    """

    if not _table_exists(conn, "predictions"):
        return ""
    if "is_valid" not in _columns(conn, "predictions"):
        return ""
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}is_valid = 1"


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def _predictions_section(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not _table_exists(conn, "predictions"):
        return {
            "available": False,
            "new_this_week": 0,
            "resolved_this_week": 0,
            "total": 0,
            "resolved": 0,
            "pending": 0,
        }

    valid = _valid_filter(conn)
    week_ago = (now - timedelta(days=WEEK_DAYS)).isoformat()
    cols = _columns(conn, "predictions")
    has_outcome = "outcome" in cols
    has_resolved_at = "resolved_at" in cols

    new_this_week = (
        _scalar(
            conn,
            f"SELECT COUNT(*) FROM predictions WHERE forecast_timestamp > ?{valid}",
            (week_ago,),
        )
        or 0
    )
    if has_outcome and has_resolved_at:
        resolved_this_week = (
            _scalar(
                conn,
                f"SELECT COUNT(*) FROM predictions WHERE resolved_at > ? AND outcome IN (0,1){valid}",
                (week_ago,),
            )
            or 0
        )
        resolved = (
            _scalar(
                conn,
                f"SELECT COUNT(*) FROM predictions WHERE outcome IS NOT NULL AND outcome IN (0,1){valid}",
            )
            or 0
        )
    else:
        resolved_this_week = 0
        resolved = 0
    total = _scalar(conn, f"SELECT COUNT(*) FROM predictions WHERE 1=1{valid}") or 0
    return {
        "available": True,
        "new_this_week": int(new_this_week),
        "resolved_this_week": int(resolved_this_week),
        "total": int(total),
        "resolved": int(resolved),
        "pending": int(total) - int(resolved),
    }


def _brier_section(conn: sqlite3.Connection) -> dict[str, Any]:
    """Lifetime Brier per model and the Naked-vs-Dexter delta.

    Lower Brier is better. ``delta = brier_naked - brier_dexter``: positive
    means Dexter (Research+R1) beats Naked R1 — the research is adding
    value. Negative means the research is hurting and we should
    investigate.
    """

    if not _table_exists(conn, "predictions"):
        return {"available": False}
    cols = _columns(conn, "predictions")
    required = {"outcome", "brier_dexter", "brier_naked", "brier_market"}
    if not required.issubset(cols):
        return {"available": False}
    valid = _valid_filter(conn)
    row = conn.execute(
        f"""
        SELECT
            AVG(brier_dexter) AS brier_dexter,
            AVG(brier_naked) AS brier_naked,
            AVG(brier_market) AS brier_market,
            COUNT(*) AS resolved_count
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome IN (0,1){valid}
        """
    ).fetchone()
    brier_dexter = row[0] if row and row[0] is not None else None
    brier_naked = row[1] if row and row[1] is not None else None
    brier_market = row[2] if row and row[2] is not None else None
    resolved_count = int(row[3] or 0) if row else 0
    delta_naked_minus_dexter: float | None = None
    if brier_dexter is not None and brier_naked is not None:
        delta_naked_minus_dexter = float(brier_naked) - float(brier_dexter)
    return {
        "available": resolved_count > 0,
        "brier_dexter": float(brier_dexter) if brier_dexter is not None else None,
        "brier_naked": float(brier_naked) if brier_naked is not None else None,
        "brier_market": float(brier_market) if brier_market is not None else None,
        "delta_naked_minus_dexter": delta_naked_minus_dexter,
        "resolved_count": resolved_count,
    }


def _decisions_section(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not _table_exists(conn, "decisions"):
        return {"available": False}
    week_ago = (now - timedelta(days=WEEK_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT actor_type, decision_type, COUNT(*) AS n
        FROM decisions
        WHERE created_at > ?
        GROUP BY actor_type, decision_type
        ORDER BY actor_type, decision_type
        """,
        (week_ago,),
    ).fetchall()
    by_actor_type: dict[str, dict[str, int]] = {}
    total = 0
    for actor_type, decision_type, n in rows:
        actor = str(actor_type or "unknown")
        kind = str(decision_type or "unknown")
        by_actor_type.setdefault(actor, {}).setdefault(kind, 0)
        by_actor_type[actor][kind] += int(n)
        total += int(n)
    return {
        "available": True,
        "total_this_week": total,
        "by_actor_type": by_actor_type,
    }


def _positions_section(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not _table_exists(conn, "positions"):
        return {"available": False}
    week_ago = (now - timedelta(days=WEEK_DAYS)).isoformat()
    opened_this_week_rows = conn.execute(
        """
        SELECT mode, COUNT(*) AS n
        FROM positions
        WHERE opened_at > ?
        GROUP BY mode
        """,
        (week_ago,),
    ).fetchall()
    closed_this_week_rows = conn.execute(
        """
        SELECT mode, COUNT(*) AS n,
               COALESCE(SUM(pnl_usd), 0) AS realized_pnl
        FROM positions
        WHERE closed_at > ? AND status = 'closed'
        GROUP BY mode
        """,
        (week_ago,),
    ).fetchall()
    opened_this_week = {str(r[0] or "unknown"): int(r[1]) for r in opened_this_week_rows}
    closed_this_week_by_mode: dict[str, dict[str, float]] = {}
    realized_pnl_total = 0.0
    for mode, n, realized_pnl in closed_this_week_rows:
        mode_str = str(mode or "unknown")
        closed_this_week_by_mode[mode_str] = {
            "n": int(n),
            "realized_pnl_usd": float(realized_pnl or 0.0),
        }
        realized_pnl_total += float(realized_pnl or 0.0)

    open_rows = conn.execute(
        """
        SELECT mode, COUNT(*) AS n,
               COALESCE(SUM(notional_usd), 0) AS notional
        FROM positions
        WHERE status = 'open'
        GROUP BY mode
        """
    ).fetchall()
    open_by_mode = {
        str(r[0] or "unknown"): {"n": int(r[1]), "notional_usd": float(r[2] or 0.0)}
        for r in open_rows
    }

    win_count_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) AS losses,
            COUNT(*) AS total
        FROM positions
        WHERE closed_at > ? AND status = 'closed' AND pnl_usd IS NOT NULL
        """,
        (week_ago,),
    ).fetchone()
    wins = int(win_count_row[0] or 0) if win_count_row else 0
    losses = int(win_count_row[1] or 0) if win_count_row else 0
    closed_count = int(win_count_row[2] or 0) if win_count_row else 0
    win_rate: float | None = None
    if closed_count > 0:
        win_rate = wins / closed_count

    top_winner = conn.execute(
        """
        SELECT market_id, pnl_usd, pnl_percent
        FROM positions
        WHERE closed_at > ? AND status = 'closed' AND pnl_usd IS NOT NULL
        ORDER BY pnl_usd DESC
        LIMIT 1
        """,
        (week_ago,),
    ).fetchone()
    top_loser = conn.execute(
        """
        SELECT market_id, pnl_usd, pnl_percent
        FROM positions
        WHERE closed_at > ? AND status = 'closed' AND pnl_usd IS NOT NULL
        ORDER BY pnl_usd ASC
        LIMIT 1
        """,
        (week_ago,),
    ).fetchone()

    return {
        "available": True,
        "opened_this_week_by_mode": opened_this_week,
        "closed_this_week_by_mode": closed_this_week_by_mode,
        "open_by_mode": open_by_mode,
        "realized_pnl_week_usd": realized_pnl_total,
        "wins_this_week": wins,
        "losses_this_week": losses,
        "closed_this_week_count": closed_count,
        "win_rate_this_week": win_rate,
        "top_winner": (
            {
                "market_id": str(top_winner[0]),
                "pnl_usd": float(top_winner[1] or 0.0),
                "pnl_percent": float(top_winner[2] or 0.0),
            }
            if top_winner
            else None
        ),
        "top_loser": (
            {
                "market_id": str(top_loser[0]),
                "pnl_usd": float(top_loser[1] or 0.0),
                "pnl_percent": float(top_loser[2] or 0.0),
            }
            if top_loser
            else None
        ),
    }


def _postmortems_section(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Postmortem error breakdown over the last 30 days.

    Counts how often each of the 5 error tags was set on closed positions:
    forecast / entry / timing / management / discipline. The window is 30
    days, not 7, because postmortems are slow to fill (operator-driven)
    and a single week often has 0–1 entries which is too noisy.
    """

    if not _table_exists(conn, "postmortems"):
        return {"available": False}
    window_start = (now - timedelta(days=POSTMORTEM_WINDOW_DAYS)).isoformat()
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            COALESCE(SUM(forecast_error), 0) AS forecast_error,
            COALESCE(SUM(entry_error), 0) AS entry_error,
            COALESCE(SUM(timing_error), 0) AS timing_error,
            COALESCE(SUM(management_error), 0) AS management_error,
            COALESCE(SUM(discipline_error), 0) AS discipline_error
        FROM postmortems
        WHERE created_at > ?
        """,
        (window_start,),
    ).fetchone()
    total = int(row[0] or 0) if row else 0
    return {
        "available": True,
        "window_days": POSTMORTEM_WINDOW_DAYS,
        "total": total,
        "forecast_error": int(row[1] or 0) if row else 0,
        "entry_error": int(row[2] or 0) if row else 0,
        "timing_error": int(row[3] or 0) if row else 0,
        "management_error": int(row[4] or 0) if row else 0,
        "discipline_error": int(row[5] or 0) if row else 0,
    }


def _reviews_section(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not _table_exists(conn, "reviews"):
        return {"available": False}
    week_ago = (now - timedelta(days=WEEK_DAYS)).isoformat()
    due = _scalar(
        conn,
        "SELECT COUNT(*) FROM reviews WHERE status='due'",
    ) or 0
    completed_week = _scalar(
        conn,
        "SELECT COUNT(*) FROM reviews WHERE status='completed' AND resolved_at > ?",
        (week_ago,),
    ) or 0
    return {
        "available": True,
        "due": int(due),
        "completed_this_week": int(completed_week),
    }


def _api_cost_section(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not _table_exists(conn, "predictions"):
        return {"available": False, "month_usd": 0.0}
    cols = _columns(conn, "predictions")
    if "api_cost_usd" not in cols:
        return {"available": False, "month_usd": 0.0}
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    valid = _valid_filter(conn)
    cost = _scalar(
        conn,
        f"SELECT COALESCE(SUM(api_cost_usd), 0) FROM predictions WHERE forecast_timestamp > ?{valid}",
        (month_start,),
    )
    return {"available": True, "month_usd": float(cost or 0.0)}


def compose_weekly_digest(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate every weekly-digest section into a single dict.

    Pure with respect to the DB connection: no writes, no telegram calls,
    no clock side-effects beyond ``now`` (defaults to ``utcnow``). The
    resulting dict is consumed by :func:`format_weekly_digest_markdown`
    and is also suitable for future surfaces (dashboard widget,
    JSON export).
    """

    now = now or _utc_now()
    return {
        "generated_at": now.isoformat(),
        "predictions": _predictions_section(conn, now=now),
        "brier": _brier_section(conn),
        "decisions": _decisions_section(conn, now=now),
        "positions": _positions_section(conn, now=now),
        "postmortems": _postmortems_section(conn, now=now),
        "reviews": _reviews_section(conn, now=now),
        "api_cost": _api_cost_section(conn, now=now),
    }


def _fmt_brier(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "N/A"


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "N/A"


def _fmt_money(value: float) -> str:
    sign = "" if value >= 0 else "−"
    return f"{sign}${abs(value):.2f}"


def _fmt_brier_delta(delta: float | None) -> str:
    if delta is None:
        return "N/A"
    if delta > 0:
        return f"Research+R1 лучше Naked на {delta:.4f}"
    if delta < 0:
        return f"⚠️ Naked лучше Research+R1 на {abs(delta):.4f}"
    return "Нет разницы"


def format_weekly_digest_markdown(digest: dict[str, Any]) -> str:
    """Render a digest dict to a Telegram-Markdown message body."""

    generated_at = digest.get("generated_at", "")
    date_str = generated_at[:10] if generated_at else ""

    parts: list[str] = []
    parts.append("📊 *Еженедельный отчёт Pythia*")
    if date_str:
        parts.append(f"_{date_str}_")
    parts.append("")

    predictions = digest.get("predictions", {})
    if predictions.get("available"):
        parts.append("*За неделю:*")
        parts.append(f"• Новых прогнозов: {predictions.get('new_this_week', 0)}")
        parts.append(f"• Разрешившихся маркетов: {predictions.get('resolved_this_week', 0)}")
        parts.append("")
        parts.append("*С начала наблюдений:*")
        parts.append(f"• Всего прогнозов: {predictions.get('total', 0)}")
        parts.append(f"• Разрешилось: {predictions.get('resolved', 0)}")
        parts.append(f"• В ожидании: {predictions.get('pending', 0)}")
        parts.append("")
    else:
        parts.append("_Таблица predictions ещё не создана — статистика прогнозов недоступна._")
        parts.append("")

    decisions = digest.get("decisions", {})
    if decisions.get("available"):
        parts.append("*Decision discipline (за неделю):*")
        total = decisions.get("total_this_week", 0)
        parts.append(f"• Всего решений: {total}")
        by_actor = decisions.get("by_actor_type", {})
        for actor in sorted(by_actor.keys()):
            kinds = by_actor[actor]
            kinds_str = ", ".join(f"{kind}: {n}" for kind, n in sorted(kinds.items()))
            parts.append(f"• {actor}: {kinds_str}")
        parts.append("")

    positions = digest.get("positions", {})
    if positions.get("available"):
        parts.append("*Активность позиций (за неделю):*")
        opened = positions.get("opened_this_week_by_mode", {})
        if opened:
            opened_str = ", ".join(f"{mode}: {n}" for mode, n in sorted(opened.items()))
            parts.append(f"• Открыто: {opened_str}")
        else:
            parts.append("• Открыто: 0")
        closed_count = positions.get("closed_this_week_count", 0)
        parts.append(f"• Закрыто: {closed_count}")
        if closed_count > 0:
            parts.append(
                f"• Realized P&L: {_fmt_money(positions.get('realized_pnl_week_usd', 0.0))}"
            )
            parts.append(
                f"• Win rate: {_fmt_pct(positions.get('win_rate_this_week'))}"
                f" ({positions.get('wins_this_week', 0)}W / {positions.get('losses_this_week', 0)}L)"
            )
            top_winner = positions.get("top_winner")
            if top_winner:
                parts.append(
                    f"• 🏆 Top winner: `{top_winner['market_id']}` "
                    f"{_fmt_money(top_winner['pnl_usd'])} "
                    f"({top_winner['pnl_percent']:+.1f}%)"
                )
            top_loser = positions.get("top_loser")
            if top_loser and (top_loser.get("pnl_usd") or 0.0) < 0:
                parts.append(
                    f"• 🩸 Top loser: `{top_loser['market_id']}` "
                    f"{_fmt_money(top_loser['pnl_usd'])} "
                    f"({top_loser['pnl_percent']:+.1f}%)"
                )
        open_by_mode = positions.get("open_by_mode", {})
        if open_by_mode:
            open_summary = ", ".join(
                f"{mode}: {info['n']} (notional {_fmt_money(info['notional_usd'])})"
                for mode, info in sorted(open_by_mode.items())
            )
            parts.append(f"• Открытые сейчас: {open_summary}")
        parts.append("")

    postmortems = digest.get("postmortems", {})
    if postmortems.get("available") and postmortems.get("total", 0) > 0:
        parts.append(
            f"*Postmortem-разборы (последние {postmortems.get('window_days', POSTMORTEM_WINDOW_DAYS)} дн.):*"
        )
        parts.append(f"• Всего разборов: {postmortems.get('total', 0)}")
        parts.append(f"• forecast errors: {postmortems.get('forecast_error', 0)}")
        parts.append(f"• entry errors: {postmortems.get('entry_error', 0)}")
        parts.append(f"• timing errors: {postmortems.get('timing_error', 0)}")
        parts.append(f"• management errors: {postmortems.get('management_error', 0)}")
        parts.append(f"• discipline errors: {postmortems.get('discipline_error', 0)}")
        parts.append("")

    reviews = digest.get("reviews", {})
    if reviews.get("available"):
        due = reviews.get("due", 0)
        completed = reviews.get("completed_this_week", 0)
        if due > 0 or completed > 0:
            parts.append("*Reviews:*")
            parts.append(f"• Завершено за неделю: {completed}")
            parts.append(f"• Ожидают review: {due}")
            parts.append("")

    brier = digest.get("brier", {})
    if brier.get("available"):
        parts.append("*Brier score (↓ меньше = лучше):*")
        parts.append(f"• Research+R1: {_fmt_brier(brier.get('brier_dexter'))}")
        parts.append(f"• Naked R1: {_fmt_brier(brier.get('brier_naked'))}")
        parts.append(f"• Рыночная цена: {_fmt_brier(brier.get('brier_market'))}")
        delta = brier.get("delta_naked_minus_dexter")
        parts.append(f"• Δ (Naked − Research+R1): {_fmt_brier_delta(delta)}")
        parts.append(f"• Образец: {brier.get('resolved_count', 0)} разрешившихся прогнозов")
        parts.append("")

    api_cost = digest.get("api_cost", {})
    if api_cost.get("available"):
        parts.append(f"*Расход API в этом месяце:* {_fmt_money(api_cost.get('month_usd', 0.0))}")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
