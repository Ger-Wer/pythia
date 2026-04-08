from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from python.watchlist_store import ensure_watchlist_engine_schema, utc_now_iso

BASE_DIR = Path(__file__).resolve().parent.parent
ATTENTION_ENGINE_SQL_PATH = BASE_DIR / "sql" / "attention_engine.sql"

OPEN_STATUSES = ("new", "in_progress")
PRIORITY_ORDER = {"urgent": 0, "today": 1, "later": 2}


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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_attention_engine_schema(conn: sqlite3.Connection) -> None:
    ensure_watchlist_engine_schema(conn)
    sql = ATTENTION_ENGINE_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def _priority_from_severity(severity: str | None) -> str:
    normalized = str(severity or "").strip().lower()
    if normalized == "critical":
        return "urgent"
    if normalized in {"warning", "review"}:
        return "today"
    return "later"


def _normalize_watch_category(status_label: str | None) -> str:
    normalized = str(status_label or "").strip().lower()
    if "thesis break" in normalized:
        return "thesis_break"
    return "watch_change"


def _create_event(
    conn: sqlite3.Connection,
    attention_item_id: int,
    *,
    action: str,
    from_status: str | None,
    to_status: str | None,
    created_at: str,
    note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO attention_item_events (
            attention_item_id, action, from_status, to_status, created_at, note
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (attention_item_id, action, from_status, to_status, created_at, note),
    )


def _upsert_attention_item(
    conn: sqlite3.Connection,
    *,
    market_id: str,
    source_type: str,
    source_id: int | None,
    category: str,
    priority: str,
    title: str,
    detail: str,
    action_label: str,
    now_iso: str,
) -> dict[str, Any]:
    existing = _fetchone_dict(
        conn,
        f"""
        SELECT id, status, priority, source_id
        FROM attention_items
        WHERE market_id = ?
          AND category = ?
          AND status IN ({", ".join("?" for _ in OPEN_STATUSES)})
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (market_id, category, *OPEN_STATUSES),
    )
    if existing:
        conn.execute(
            """
            UPDATE attention_items
            SET source_type = ?,
                source_id = ?,
                priority = ?,
                title = ?,
                detail = ?,
                action_label = ?,
                updated_at = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                source_type,
                source_id,
                priority,
                title,
                detail,
                action_label,
                now_iso,
                now_iso,
                int(existing["id"]),
            ),
        )
        row = _fetchone_dict(conn, "SELECT * FROM attention_items WHERE id = ?", (int(existing["id"]),))
        return row or {}

    same_source_closed = _fetchone_dict(
        conn,
        """
        SELECT id
        FROM attention_items
        WHERE market_id = ?
          AND source_type = ?
          AND source_id IS ?
          AND category = ?
          AND status IN ('closed', 'cancelled')
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (market_id, source_type, source_id, category),
    )
    if same_source_closed:
        conn.execute(
            "UPDATE attention_items SET last_seen_at = ?, updated_at = ? WHERE id = ?",
            (now_iso, now_iso, int(same_source_closed["id"])),
        )
        row = _fetchone_dict(conn, "SELECT * FROM attention_items WHERE id = ?", (int(same_source_closed["id"]),))
        return row or {}

    cursor = conn.execute(
        """
        INSERT INTO attention_items (
            market_id, source_type, source_id, category, priority, status,
            title, detail, action_label, created_at, updated_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?)
        """,
        (
            market_id,
            source_type,
            source_id,
            category,
            priority,
            title,
            detail,
            action_label,
            now_iso,
            now_iso,
            now_iso,
        ),
    )
    item_id = int(cursor.lastrowid)
    _create_event(
        conn,
        item_id,
        action="create",
        from_status=None,
        to_status="new",
        created_at=now_iso,
    )
    row = _fetchone_dict(conn, "SELECT * FROM attention_items WHERE id = ?", (item_id,))
    return row or {}


def _sync_watch_update_attention(conn: sqlite3.Connection, *, now_iso: str) -> None:
    if not _table_exists(conn, "watch_updates") or not _table_exists(conn, "watch_dossiers"):
        return

    updates = _fetchall_dicts(
        conn,
        """
        SELECT
            u.id,
            d.market_id,
            COALESCE((
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = d.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ), d.market_id) AS question,
            u.severity,
            u.status_label,
            u.reason_summary,
            u.action_summary
        FROM watch_updates u
        JOIN watch_dossiers d ON d.id = u.watch_dossier_id
        WHERE d.status IN ('active', 'active_error')
        ORDER BY u.created_at ASC, u.id ASC
        """,
    )

    for update in updates:
        category = _normalize_watch_category(update.get("status_label"))
        _upsert_attention_item(
            conn,
            market_id=str(update["market_id"]),
            source_type="watch_update",
            source_id=int(update["id"]),
            category=category,
            priority=_priority_from_severity(update.get("severity")),
            title=str(update.get("question") or update["market_id"]),
            detail=str(update.get("reason_summary") or "").strip() or "Есть изменение по watched-рынку.",
            action_label=str(update.get("action_summary") or "Открыть разбор").strip(),
            now_iso=now_iso,
        )


def _sync_due_review_attention(conn: sqlite3.Connection, *, now_iso: str) -> None:
    if not _table_exists(conn, "reviews"):
        return

    rows = _fetchall_dicts(
        conn,
        """
        SELECT
            r.id,
            r.market_id,
            r.review_type,
            r.review_note,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM positions p
                    WHERE p.id = r.position_id
                      AND p.status = 'open'
                ) THEN 1
                ELSE 0
            END AS has_open_position,
            COALESCE((
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = r.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ), r.market_id) AS question
        FROM reviews r
        WHERE r.status = 'due'
        """
    )

    active_markets = set()
    for row in rows:
        active_markets.add(str(row["market_id"]))
        priority = "urgent" if int(row["has_open_position"] or 0) == 1 else "today"
        _upsert_attention_item(
            conn,
            market_id=str(row["market_id"]),
            source_type="review",
            source_id=int(row["id"]),
            category="review_due",
            priority=priority,
            title=f"Review просрочен: {row['question']}",
            detail=str(row.get("review_note") or "Есть review, которое требует ручного подтверждения тезиса."),
            action_label="Открыть review",
            now_iso=now_iso,
        )

    stale_items = _fetchall_dicts(
        conn,
        f"""
        SELECT id, market_id, status
        FROM attention_items
        WHERE category = 'review_due'
          AND status IN ({", ".join("?" for _ in OPEN_STATUSES)})
        """,
        OPEN_STATUSES,
    )
    for item in stale_items:
        if str(item["market_id"]) in active_markets:
            continue
        conn.execute(
            "UPDATE attention_items SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now_iso, int(item["id"])),
        )
        _create_event(
            conn,
            int(item["id"]),
            action="sync_clear",
            from_status=str(item["status"]),
            to_status="cancelled",
            created_at=now_iso,
            note="due review cleared by sync",
        )


def _sync_system_attention(
    conn: sqlite3.Connection,
    *,
    system_status: str,
    research_status: str,
    research_warning: str,
    now_iso: str,
) -> None:
    if not _table_exists(conn, "watch_dossiers"):
        return

    dossiers = _fetchall_dicts(
        conn,
        """
        SELECT
            market_id,
            COALESCE((
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = d.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ), d.market_id) AS question
        FROM watch_dossiers d
        WHERE d.status IN ('active', 'active_error')
        """
    )
    if system_status == "ON" and research_status == "ON":
        stale_items = _fetchall_dicts(
            conn,
            f"""
            SELECT id, status
            FROM attention_items
            WHERE category = 'system_issue'
              AND status IN ({", ".join("?" for _ in OPEN_STATUSES)})
            """,
            OPEN_STATUSES,
        )
        for item in stale_items:
            conn.execute(
                "UPDATE attention_items SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now_iso, int(item["id"])),
            )
            _create_event(
                conn,
                int(item["id"]),
                action="sync_clear",
                from_status=str(item["status"]),
                to_status="cancelled",
                created_at=now_iso,
                note="system issue cleared by sync",
            )
        return

    detail = research_warning or "Research/system слой требует внимания."
    for dossier in dossiers:
        _upsert_attention_item(
            conn,
            market_id=str(dossier["market_id"]),
            source_type="system",
            source_id=None,
            category="system_issue",
            priority="urgent",
            title=f"System issue: {dossier['question']}",
            detail=detail,
            action_label="Проверить статус системы",
            now_iso=now_iso,
        )


def sync_attention_items(
    conn: sqlite3.Connection,
    *,
    system_status: str,
    research_status: str,
    research_warning: str,
    now_iso: str | None = None,
) -> dict[str, int]:
    ensure_attention_engine_schema(conn)
    now_value = now_iso or utc_now_iso()
    _sync_watch_update_attention(conn, now_iso=now_value)
    _sync_due_review_attention(conn, now_iso=now_value)
    _sync_system_attention(
        conn,
        system_status=system_status,
        research_status=research_status,
        research_warning=research_warning,
        now_iso=now_value,
    )
    counts = get_attention_summary(conn)
    return counts


def list_attention_items(
    conn: sqlite3.Connection,
    *,
    priority: str | None = None,
    status: str = "open",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "attention_items"):
        return []
    where = []
    params: list[Any] = []
    if status == "open":
        where.append(f"i.status IN ({', '.join('?' for _ in OPEN_STATUSES)})")
        params.extend(OPEN_STATUSES)
    elif status != "all":
        where.append("i.status = ?")
        params.append(status)
    if priority:
        where.append("i.priority = ?")
        params.append(priority)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = _fetchall_dicts(
        conn,
        f"""
        SELECT
            i.id,
            i.market_id,
            i.source_type,
            i.source_id,
            i.category,
            i.priority,
            i.status,
            i.title,
            i.detail,
            i.action_label,
            i.created_at,
            i.updated_at,
            i.last_seen_at,
            i.last_telegram_sent_at,
            COALESCE((
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = i.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ), i.market_id) AS question
        FROM attention_items i
        JOIN watch_dossiers d
          ON d.market_id = i.market_id
         AND d.status IN ('active', 'active_error')
        {where_sql}
        ORDER BY
            CASE i.priority
              WHEN 'urgent' THEN 0
              WHEN 'today' THEN 1
              ELSE 2
            END ASC,
            CASE i.status
              WHEN 'new' THEN 0
              WHEN 'in_progress' THEN 1
              ELSE 2
            END ASC,
            i.updated_at DESC,
            i.id DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    return rows


def list_active_attention_for_market(conn: sqlite3.Connection, market_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "attention_items") or not _table_exists(conn, "watch_dossiers"):
        return []
    return _fetchall_dicts(
        conn,
        f"""
        SELECT
            i.id,
            i.market_id,
            i.source_type,
            i.source_id,
            i.category,
            i.priority,
            i.status,
            i.title,
            i.detail,
            i.action_label,
            i.created_at,
            i.updated_at,
            i.last_seen_at,
            i.last_telegram_sent_at,
            COALESCE((
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = i.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ), i.market_id) AS question
        FROM attention_items i
        JOIN watch_dossiers d
          ON d.market_id = i.market_id
         AND d.status IN ('active', 'active_error')
        WHERE i.market_id = ?
          AND i.status IN ({", ".join("?" for _ in OPEN_STATUSES)})
        ORDER BY
            CASE i.priority
              WHEN 'urgent' THEN 0
              WHEN 'today' THEN 1
              ELSE 2
            END ASC,
            i.updated_at DESC,
            i.id DESC
        """,
        (market_id, *OPEN_STATUSES),
    )


def get_attention_summary(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"urgent": 0, "today": 0, "later": 0, "open_total": 0}
    if not _table_exists(conn, "attention_items") or not _table_exists(conn, "watch_dossiers"):
        return counts
    rows = _fetchall_dicts(
        conn,
        f"""
        SELECT priority, COUNT(*) AS count
        FROM attention_items i
        JOIN watch_dossiers d
          ON d.market_id = i.market_id
         AND d.status IN ('active', 'active_error')
        WHERE i.status IN ({", ".join("?" for _ in OPEN_STATUSES)})
        GROUP BY priority
        """,
        OPEN_STATUSES,
    )
    for row in rows:
        priority = str(row["priority"])
        if priority in counts:
            counts[priority] = int(row["count"])
            counts["open_total"] += int(row["count"])
    return counts


def enrich_dossiers_with_attention(conn: sqlite3.Connection, dossiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _table_exists(conn, "attention_items"):
        return dossiers
    enriched: list[dict[str, Any]] = []
    for dossier in dossiers:
        items = list_active_attention_for_market(conn, str(dossier["market_id"]))
        highest = items[0] if items else None
        open_operator_positions = 0
        open_shadow_positions = 0
        has_due_review = False
        if _table_exists(conn, "positions"):
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN mode = 'operator' AND status = 'open' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN mode = 'shadow' AND status = 'open' THEN 1 ELSE 0 END)
                FROM positions
                WHERE market_id = ?
                """,
                (str(dossier["market_id"]),),
            ).fetchone()
            if row:
                open_operator_positions = int(row[0] or 0)
                open_shadow_positions = int(row[1] or 0)
        if _table_exists(conn, "reviews"):
            row = conn.execute(
                "SELECT 1 FROM reviews WHERE market_id = ? AND status = 'due' LIMIT 1",
                (str(dossier["market_id"]),),
            ).fetchone()
            has_due_review = row is not None

        enriched.append(
            {
                **dossier,
                "active_attention_item_id": int(highest["id"]) if highest else None,
                "attention_title": highest.get("title") if highest else None,
                "attention_detail": highest.get("detail") if highest else None,
                "attention_action_label": highest.get("action_label") if highest else None,
                "attention_status": highest.get("status") if highest else None,
                "attention_priority": highest.get("priority") if highest else None,
                "has_open_attention": bool(highest),
                "latest_change_at": highest.get("updated_at") if highest else dossier.get("updated_at"),
                "next_action_text": highest.get("action_label") if highest else dossier.get("last_action_summary"),
                "active_attention_count": len(items),
                "open_operator_positions": open_operator_positions,
                "open_shadow_positions": open_shadow_positions,
                "has_due_review": has_due_review,
            }
        )

    enriched.sort(
        key=lambda item: (
            PRIORITY_ORDER.get(str(item.get("attention_priority") or "later"), 2)
            if item.get("has_open_attention")
            else 3,
            0 if item.get("has_due_review") else 1,
            -(int(item.get("open_operator_positions") or 0) + int(item.get("open_shadow_positions") or 0)),
            str(item.get("latest_change_at") or ""),
            str(item.get("market_id") or ""),
        ),
        reverse=False,
    )
    return enriched


def transition_attention_item(
    conn: sqlite3.Connection,
    attention_item_id: int,
    action: str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    ensure_attention_engine_schema(conn)
    row = _fetchone_dict(conn, "SELECT * FROM attention_items WHERE id = ?", (attention_item_id,))
    if row is None:
        raise LookupError(attention_item_id)

    now_value = now_iso or utc_now_iso()
    current_status = str(row["status"])
    next_status = current_status
    next_priority = str(row["priority"])

    if action == "start":
        next_status = "in_progress"
    elif action == "close":
        next_status = "closed"
    elif action == "cancel":
        next_status = "cancelled"
    elif action == "bump_today":
        next_priority = "today"
    elif action == "bump_later":
        next_priority = "later"
    else:
        raise ValueError(action)

    conn.execute(
        """
        UPDATE attention_items
        SET status = ?,
            priority = ?,
            updated_at = ?,
            last_seen_at = ?
        WHERE id = ?
        """,
        (next_status, next_priority, now_value, now_value, attention_item_id),
    )
    _create_event(
        conn,
        attention_item_id,
        action=action,
        from_status=current_status,
        to_status=next_status,
        created_at=now_value,
    )
    updated = _fetchone_dict(conn, "SELECT * FROM attention_items WHERE id = ?", (attention_item_id,))
    return updated or {}


def list_pending_attention_items(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict[str, Any]]:
    if not _table_exists(conn, "attention_items") or not _table_exists(conn, "watch_dossiers"):
        return []
    return _fetchall_dicts(
        conn,
        f"""
        SELECT
            i.id,
            i.market_id,
            i.source_type,
            i.source_id,
            i.category,
            i.priority,
            i.status,
            i.title,
            i.detail,
            i.action_label,
            i.created_at,
            i.updated_at,
            i.last_seen_at,
            i.last_telegram_sent_at,
            COALESCE((
                SELECT p.question
                FROM predictions p
                WHERE p.market_id = i.market_id
                  AND COALESCE(p.is_valid, 1) = 1
                ORDER BY p.forecast_timestamp DESC, p.id DESC
                LIMIT 1
            ), i.market_id) AS question
        FROM attention_items i
        JOIN watch_dossiers d
          ON d.market_id = i.market_id
         AND d.status IN ('active', 'active_error')
        WHERE i.status IN ({", ".join("?" for _ in OPEN_STATUSES)})
          AND i.priority IN ('urgent', 'today')
          AND (i.last_telegram_sent_at IS NULL OR i.last_telegram_sent_at < i.updated_at)
        ORDER BY
            CASE i.priority
              WHEN 'urgent' THEN 0
              ELSE 1
            END ASC,
            i.updated_at DESC,
            i.id DESC
        LIMIT ?
        """,
        (*OPEN_STATUSES, limit),
    )


def mark_attention_items_delivered(
    conn: sqlite3.Connection,
    attention_item_ids: list[int],
    *,
    delivered_at: str | None = None,
) -> int:
    if not _table_exists(conn, "attention_items"):
        return 0
    if not attention_item_ids:
        return 0
    timestamp = delivered_at or utc_now_iso()
    placeholders = ", ".join("?" for _ in attention_item_ids)
    cursor = conn.execute(
        f"""
        UPDATE attention_items
        SET last_telegram_sent_at = ?,
            updated_at = updated_at
        WHERE id IN ({placeholders})
        """,
        (timestamp, *attention_item_ids),
    )
    return int(cursor.rowcount or 0)
