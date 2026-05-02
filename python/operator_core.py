from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ContextManager
from urllib.parse import quote

from python.decision_store import (
    close_position_manually,
    complete_review,
    create_manual_decision,
    ensure_decision_core_schema,
    get_decision_core_counts,
    get_market_workspace as load_market_workspace,
    list_due_reviews as load_due_reviews,
    list_open_positions as load_open_positions,
)
from python.attention_store import (
    ensure_attention_engine_schema,
    enrich_dossiers_with_attention,
    get_attention_summary,
    list_active_attention_for_market,
    list_attention_items as load_attention_items,
    sync_attention_items,
    transition_attention_item as apply_attention_transition,
)
from python.quality_metrics import load_quality_snapshot
from python.watchlist_store import (
    bootstrap_watch_dossier,
    deactivate_watch_dossier,
    get_watch_dossier as load_watch_dossier,
    list_watch_updates_for_market as load_watch_updates_for_market,
    ensure_watchlist_engine_schema,
    list_watch_dossiers as load_watch_dossiers,
    list_watch_updates as load_watch_updates,
)

SAFE_ACTION_NAMES = {"forecast", "resolve", "weekly"}
LLM_PROVIDER_ENV_KEYS = ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")
DECISION_CORE_TABLES = ("signals", "books", "decisions", "positions", "position_events", "reviews", "postmortems")
WATCHLIST_ENGINE_TABLES = ("watch_dossiers", "watch_refresh_runs", "watch_refresh_snapshots", "watch_updates")
ATTENTION_ENGINE_TABLES = ("attention_items", "attention_item_events")


class OperatorCore:
    def __init__(self, db_path: Path | str, log_dir: Path | str):
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir)

    def _connect(self) -> ContextManager[sqlite3.Connection]:
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        db_uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.execute("PRAGMA foreign_keys = ON")
        return closing(conn)

    def _connect_rw(self) -> ContextManager[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return closing(conn)

    def _ensure_decision_core_schema_rw(self) -> None:
        if self.db_path.exists():
            with self._connect() as conn:
                if all(self._table_exists(conn, table_name) for table_name in DECISION_CORE_TABLES):
                    return

        with self._connect_rw() as conn:
            ensure_decision_core_schema(conn)

    def _watchlist_engine_exists(self, conn: sqlite3.Connection) -> bool:
        return all(self._table_exists(conn, table_name) for table_name in WATCHLIST_ENGINE_TABLES)

    def _attention_engine_exists(self, conn: sqlite3.Connection) -> bool:
        return all(self._table_exists(conn, table_name) for table_name in ATTENTION_ENGINE_TABLES)

    def _sync_attention_engine(self, *, health: dict[str, Any] | None = None) -> dict[str, int]:
        current_health = health or self.get_health()
        try:
            with self._connect_rw() as conn:
                ensure_attention_engine_schema(conn)
                summary = sync_attention_items(
                    conn,
                    system_status=str(current_health["system_status"]),
                    research_status=str(current_health["research_status"]),
                    research_warning=str(current_health["research_warning"]),
                )
                conn.commit()
                return summary
        except sqlite3.Error:
            return {"urgent": 0, "today": 0, "later": 0, "open_total": 0}

    def predictions_table_exists(self) -> bool:
        with self._connect() as conn:
            return self._predictions_table_exists(conn)

    @staticmethod
    def _predictions_table_exists(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _prediction_columns(conn: sqlite3.Connection) -> set[str]:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}

    def _has_column(self, conn: sqlite3.Connection, column_name: str) -> bool:
        return column_name in self._prediction_columns(conn)

    def _select_expression(
        self,
        conn: sqlite3.Connection,
        column_name: str,
        *,
        table_alias: str | None = None,
        cast_text: bool = False,
    ) -> str:
        if self._has_column(conn, column_name):
            if table_alias:
                return f"{table_alias}.{column_name}"
            return column_name
        if cast_text:
            return f"NULL AS {column_name}"
        return f"NULL AS {column_name}"

    def _valid_where_clause(self, conn: sqlite3.Connection, alias: str = "") -> str:
        if not self._has_column(conn, "is_valid"):
            return ""
        prefix = f"{alias}." if alias else ""
        return f" AND {prefix}is_valid = 1"

    def _ensure_watchlist_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                market_id TEXT PRIMARY KEY,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _status_file_path(self, *, ensure_parent: bool = False) -> Path:
        if ensure_parent:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / "operator_action_status.json"

    @staticmethod
    def _env_value(key: str) -> str:
        return os.getenv(key, "").strip()

    @classmethod
    def _has_any_env(cls, keys: tuple[str, ...]) -> bool:
        return any(cls._env_value(key) for key in keys)

    @staticmethod
    def _component_check(component: str, status: str, detail: str) -> dict[str, str]:
        return {
            "component": component,
            "status": status,
            "detail": detail,
        }

    def _has_research_predictions(self, conn: sqlite3.Connection) -> bool:
        if not self._predictions_table_exists(conn):
            return False
        if not self._has_column(conn, "dexter_p"):
            return False
        valid_filter = self._valid_where_clause(conn)
        row = conn.execute(
            f"""
            SELECT 1
            FROM predictions
            WHERE dexter_p IS NOT NULL
            {valid_filter}
            LIMIT 1
            """
        ).fetchone()
        return row is not None

    @staticmethod
    def _attention_item(
        title: str,
        detail: str,
        *,
        market_id: str | None = None,
        action_label: str | None = None,
    ) -> dict[str, Any]:
        return {
            "title": title,
            "detail": detail,
            "market_id": market_id,
            "action_label": action_label,
        }

    @staticmethod
    def _parse_research_brief(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            parsed = json.loads(str(payload))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _research_field(payload: dict[str, Any], key: str, default: str = "Нет данных.") -> str:
        value = payload.get(key)
        if value is None:
            return default
        normalized = str(value).strip()
        return normalized or default

    @staticmethod
    def _research_list(payload: dict[str, Any], key: str) -> list[str]:
        value = payload.get(key)
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _signal_edge(signal: dict[str, Any]) -> float | None:
        edge = signal.get("edge")
        if edge is None:
            market_price = signal.get("market_price")
            if market_price is None:
                return None
            if signal.get("research_probability") is not None:
                edge = float(signal["research_probability"]) - float(market_price)
            elif signal.get("naked_probability") is not None:
                edge = float(signal["naked_probability"]) - float(market_price)
            else:
                return None
        try:
            return float(edge)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _overview_signal_sort_key(signal: dict[str, Any]) -> tuple[int, float, str]:
        edge = OperatorCore._signal_edge(signal)
        market_id = str(signal.get("market_id", ""))
        if edge is None:
            return (1, 0.0, market_id)
        return (0, -abs(edge), market_id)

    @classmethod
    def _build_daily_digest(
        cls,
        *,
        ranked_signals: list[dict[str, Any]],
        research_status: str,
        watchlist_count: int,
        markets_processed: int,
    ) -> dict[str, Any]:
        if not ranked_signals:
            return {
                "headline": "Сегодня Pythia не видит заметных расхождений в последнем прогоне.",
                "summary": "Сначала стоит запустить новый прогон, чтобы появились рынки для ручной проверки.",
                "items": [],
            }

        strong_count = 0
        for signal in ranked_signals:
            edge = cls._signal_edge(signal)
            if edge is not None and abs(edge) >= 15:
                strong_count += 1

        if strong_count == 1:
            headline = "Сегодня Pythia выделяет 1 самый сильный сигнал в последнем прогоне."
        elif strong_count:
            headline = f"Сегодня Pythia выделяет {strong_count} самых сильных сигнала в последнем прогоне."
        else:
            headline = "Сегодня Pythia не видит по-настоящему сильных сигналов в последнем прогоне."

        if research_status == "ON":
            summary = "Сначала смотрю на самые заметные расхождения, затем сверяю их с рынками из списка наблюдения."
        elif watchlist_count:
            summary = f"Сначала смотрю на самые заметные расхождения и сверяю их с {watchlist_count} рынками из списка наблюдения."
        else:
            summary = "Сначала смотрю на самые заметные расхождения, а дополнительный контекст пока ограничен."

        items: list[dict[str, Any]] = []
        for signal in ranked_signals[:3]:
            edge = cls._signal_edge(signal)
            if edge is None:
                continue
            if edge >= 0:
                detail = f"Pythia оценивает этот рынок выше текущей цены примерно на {abs(edge):.1f} пункта."
            else:
                detail = f"Pythia оценивает этот рынок ниже текущей цены примерно на {abs(edge):.1f} пункта."
            if watchlist_count:
                reason_why_it_matters = (
                    f"Почему это важно: в свежем прогоне уже разобрано {markets_processed} рынков, "
                    "и этот сигнал удобно сверить с рынками из списка наблюдения."
                )
            else:
                reason_why_it_matters = (
                    f"Почему это важно: в свежем прогоне уже разобрано {markets_processed} рынков, "
                    "и этот сигнал стоит открыть первым."
                )
            items.append(
                {
                    "market_id": str(signal["market_id"]),
                    "title": str(signal["question"]),
                    "detail": detail,
                    "reason_why_it_matters": reason_why_it_matters,
                }
            )

        return {
            "headline": headline,
            "summary": summary,
            "items": items,
        }

    def resolve_market_reference(self, reference: str) -> str | None:
        reference = reference.strip()
        if not reference:
            return None

        with self._connect() as conn:
            if not self._predictions_table_exists(conn):
                return None

            valid_filter = self._valid_where_clause(conn)
            row = conn.execute(
                """
                SELECT market_id
                FROM predictions
                WHERE market_id = ?
                """
                + valid_filter
                + """
                ORDER BY forecast_timestamp DESC, id DESC
                LIMIT 1
                """,
                (reference,),
            ).fetchone()
            if row:
                return str(row[0])

            if reference.isdigit():
                offset = max(int(reference) - 1, 0)
                row = conn.execute(
                    """
                    SELECT market_id
                    FROM predictions
                    WHERE 1=1
                    """
                    + valid_filter
                    + """
                    ORDER BY forecast_timestamp DESC, id DESC
                    LIMIT 1 OFFSET ?
                    """,
                    (offset,),
                ).fetchone()
                if row:
                    return str(row[0])

        return None

    def get_overview(self) -> dict[str, Any]:
        with self._connect() as conn:
            if not self._predictions_table_exists(conn):
                return {
                    "statuses": {
                        "system": "DEGRADED",
                        "research": "ON",
                    },
                    "valid_predictions": 0,
                    "resolved_predictions": 0,
                    "watchlist_count": 0,
                    "open_shadow_positions": 0,
                    "open_operator_positions": 0,
                    "due_reviews_count": 0,
                    "latest_run": {
                        "forecast_timestamp": None,
                        "markets_processed": 0,
                    },
                    "top_signals": [],
                    "attention_items": [],
                    "attention_summary": {"urgent": 0, "today": 0, "later": 0, "open_total": 0},
                    "urgent_items": [],
                    "today_items": [],
                    "later_items": [],
                    "watchlist_changed_count": 0,
                    "active_positions": [],
                    "recent_watch_updates": [],
                    "daily_digest": {
                        "headline": "Сегодня Pythia пока не видит сильных сигналов в последнем прогоне.",
                        "summary": "Сначала стоит запустить новый прогон, чтобы появились рынки для ручной проверки.",
                        "items": [],
                    },
                    "next_step": "Сначала запустите прогнозный прогон и дождитесь первых сигналов.",
                    "last_action": self.get_last_action_summary(),
                    "research_warning": "",
                }

            valid_filter = self._valid_where_clause(conn)
            valid_predictions = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE 1=1{valid_filter}"
            ).fetchone()[0]
            resolved_predictions = 0
            if self._has_column(conn, "outcome"):
                resolved_predictions = conn.execute(
                    f"SELECT COUNT(*) FROM predictions WHERE outcome IN (0, 1){valid_filter}"
                ).fetchone()[0]
            latest_ts = conn.execute(
                f"SELECT MAX(forecast_timestamp) FROM predictions WHERE 1=1{valid_filter}"
            ).fetchone()[0]
            markets_processed = 0
            if latest_ts:
                markets_processed = conn.execute(
                    f"SELECT COUNT(*) FROM predictions WHERE 1=1{valid_filter} AND forecast_timestamp = ?",
                    (latest_ts,),
                ).fetchone()[0]

        latest_run = self.get_latest_run()
        health = self.get_health()
        watchlist_count = self.get_watchlist_count()
        with self._connect() as conn:
            decision_counts = get_decision_core_counts(conn)
        ranked_signals = sorted(latest_run["items"], key=self._overview_signal_sort_key)
        top_signals = ranked_signals[:5]
        overview_research_status = health["research_status"]
        if any(item.get("research_probability") is not None for item in latest_run["items"]):
            overview_research_status = "ON"
        self._sync_attention_engine(health=health)
        with self._connect() as conn:
            attention_summary = get_attention_summary(conn) if self._attention_engine_exists(conn) else {"urgent": 0, "today": 0, "later": 0, "open_total": 0}
            urgent_items = load_attention_items(conn, priority="urgent", status="open", limit=5) if self._attention_engine_exists(conn) else []
            today_items = load_attention_items(conn, priority="today", status="open", limit=5) if self._attention_engine_exists(conn) else []
            later_items = load_attention_items(conn, priority="later", status="open", limit=5) if self._attention_engine_exists(conn) else []
            recent_watch_updates = load_watch_updates(conn, limit=5) if self._watchlist_engine_exists(conn) else []
            active_positions = load_open_positions(conn)[:5] if all(self._table_exists(conn, name) for name in ("positions", "books")) else []

        attention_items = (urgent_items + today_items + later_items)[:3]
        if not attention_items and top_signals:
            top_signal = top_signals[0]
            top_signal_edge = top_signal.get("edge")
            if top_signal_edge is None:
                edge_detail = "без рассчитанного расхождения"
            else:
                edge_detail = f"расхождение {float(top_signal_edge):+.1f} п.п."
            attention_items.append(
                self._attention_item(
                    "Следующий рынок для разбора",
                    f"{top_signal['question']} · {edge_detail}",
                    market_id=top_signal["market_id"],
                    action_label="Открыть разбор",
                )
            )

        next_step = "Проверьте здоровье системы и запустите первый прогноз."
        if urgent_items:
            next_step = "Сначала разберите срочные изменения по watched-рынкам и открытым позициям."
        elif today_items:
            next_step = "Закройте задачи на сегодня: review и заметные изменения по избранному."
        elif top_signals:
            next_step = "Откройте главный сигнал и решите, добавлять ли его в избранное."
        elif latest_ts:
            next_step = "Откройте раздел «Сигналы» и проверьте, появились ли рынки для разбора."
        elif health["research_warning"]:
            next_step = "Сначала устраните ограничения research-слоя, затем перезапустите прогноз."

        return {
            "statuses": {
                "system": health["system_status"],
                "research": overview_research_status,
            },
            "valid_predictions": valid_predictions,
            "resolved_predictions": resolved_predictions,
            "watchlist_count": watchlist_count,
            "open_shadow_positions": decision_counts["open_shadow_positions"],
            "open_operator_positions": decision_counts["open_operator_positions"],
            "due_reviews_count": decision_counts["due_reviews_count"],
            "attention_summary": attention_summary,
            "urgent_items": urgent_items,
            "today_items": today_items,
            "later_items": later_items,
            "watchlist_changed_count": attention_summary["urgent"] + attention_summary["today"] + attention_summary["later"],
            "active_positions": active_positions,
            "recent_watch_updates": recent_watch_updates,
            "latest_run": {
                "forecast_timestamp": latest_ts,
                "markets_processed": markets_processed,
            },
            "top_signals": top_signals,
            "attention_items": attention_items[:3],
            "daily_digest": self._build_daily_digest(
                ranked_signals=ranked_signals,
                research_status=overview_research_status,
                watchlist_count=watchlist_count,
                markets_processed=markets_processed,
            ),
            "next_step": next_step,
            "last_action": self.get_last_action_summary(),
            "research_warning": health["research_warning"],
        }

    def get_quality(self) -> dict[str, Any]:
        return load_quality_snapshot(self.db_path, self.log_dir)

    def get_market_workspace(self, market_id: str) -> dict[str, Any]:
        market = self.get_market_details(market_id)
        self._ensure_decision_core_schema_rw()
        health = self.get_health()
        self._sync_attention_engine(health=health)
        with self._connect() as conn:
            workspace = load_market_workspace(conn, market_id)
            watch_dossier = load_watch_dossier(conn, market_id)
            watch_updates = load_watch_updates_for_market(conn, market_id, limit=5)
            active_attention_items = list_active_attention_for_market(conn, market_id) if self._attention_engine_exists(conn) else []
        return {
            "market": market,
            "signal": workspace["signal"],
            "open_positions": workspace["open_positions"],
            "due_reviews": workspace["due_reviews"],
            "recent_decisions": workspace["recent_decisions"],
            "latest_postmortem": workspace["latest_postmortem"],
            "watch_dossier": watch_dossier,
            "watch_updates": watch_updates,
            "active_attention_items": active_attention_items,
        }

    def list_open_positions(self) -> list[dict[str, Any]]:
        self._ensure_decision_core_schema_rw()
        with self._connect() as conn:
            return load_open_positions(conn)

    def list_due_reviews(self) -> list[dict[str, Any]]:
        self._ensure_decision_core_schema_rw()
        with self._connect() as conn:
            return load_due_reviews(conn)

    def create_manual_decision(
        self,
        *,
        signal_id: int,
        decision_type: str,
        decision_reason: str,
        operator_confidence: str | None,
        confirmation_criteria: str | None,
        invalidation_criteria: str | None,
        open_position: bool,
        review_due_at: str | None,
    ) -> dict[str, Any]:
        with self._connect_rw() as conn:
            return create_manual_decision(
                conn,
                signal_id=signal_id,
                decision_type=decision_type,
                decision_reason=decision_reason,
                operator_confidence=operator_confidence,
                confirmation_criteria=confirmation_criteria,
                invalidation_criteria=invalidation_criteria,
                open_position=open_position,
                review_due_at=review_due_at,
            )

    def close_position_manually(self, position_id: int, note: str = "") -> dict[str, Any]:
        with self._connect_rw() as conn:
            return close_position_manually(conn, position_id, note)

    def complete_review(self, review_id: int, note: str = "") -> dict[str, Any]:
        with self._connect_rw() as conn:
            result = complete_review(conn, review_id, note)
            sync_attention_items(
                conn,
                system_status=str(self.get_health()["system_status"]),
                research_status=str(self.get_health()["research_status"]),
                research_warning=str(self.get_health()["research_warning"]),
            )
            conn.commit()
            return result

    def get_market_details(self, market_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            if not self._predictions_table_exists(conn):
                raise LookupError(market_id)

            valid_filter = self._valid_where_clause(conn)
            row = conn.execute(
                """
                SELECT market_id, question, market_price, dexter_p, naked_p,
                       dexter_confidence, dexter_reasoning, dexter_key_risk,
                       dexter_base_rate, research_brief_json, volume, end_date,
                       forecast_timestamp
                FROM predictions
                WHERE market_id = ?
                """
                + valid_filter
                + """
                ORDER BY forecast_timestamp DESC, id DESC
                LIMIT 1
                """,
                (market_id,),
            ).fetchone()
            history_rows = conn.execute(
                """
                SELECT forecast_timestamp, market_price, dexter_p, naked_p
                FROM predictions
                WHERE market_id = ?
                """
                + valid_filter
                + """
                ORDER BY forecast_timestamp ASC, id ASC
                """,
                (market_id,),
            ).fetchall()
            is_watched = False
            if self._table_exists(conn, "watchlist"):
                is_watched = (
                    conn.execute(
                        "SELECT 1 FROM watchlist WHERE market_id = ? LIMIT 1",
                        (market_id,),
                    ).fetchone()
                    is not None
                )

        if not row:
            raise LookupError(market_id)

        research = self._parse_research_brief(row[9])
        verdict_summary = self._build_verdict_summary(
            market_price=row[2],
            research_probability=row[3],
            confidence=row[5],
        )
        research_summary = str(research.get("summary") or "").strip()
        research_key_facts = self._research_list(research, "key_facts")
        transparency_facts: list[str] = []
        for item in [*research_key_facts, research_summary, self._research_field(research, "primary_driver")]:
            normalized = str(item).strip()
            if normalized and normalized not in transparency_facts:
                transparency_facts.append(normalized)
        falsifier = self._research_field(research, "disconfirming_signal")
        if falsifier == "Нет данных." and row[7]:
            falsifier = str(row[7]).strip()
        return {
            "market_id": str(row[0]),
            "question": row[1],
            "market_price": row[2],
            "research_probability": row[3],
            "naked_probability": row[4],
            "confidence": row[5],
            "verdict_summary": verdict_summary,
            "is_watched": is_watched,
            "reasoning": row[6],
            "key_risk": row[7],
            "base_rate": row[8],
            "volume": row[10],
            "end_date": row[11],
            "forecast_timestamp": row[12],
            "last_updated": row[12],
            "history": [
                {
                    "forecast_timestamp": history_row[0],
                    "market_price": history_row[1],
                    "research_probability": history_row[2],
                    "naked_probability": history_row[3],
                }
                for history_row in history_rows
            ],
            "transparency": {
                "facts": transparency_facts,
                "sources": self._research_list(research, "sources"),
                "falsifier": falsifier,
                "updated_at": row[12],
            },
            "research": {
                "summary": research_summary,
                "key_facts": research_key_facts,
                "catalyst_type": str(research.get("catalyst_type") or "other").strip() or "other",
                "primary_driver": self._research_field(research, "primary_driver"),
                "second_order_effects": self._research_list(research, "second_order_effects"),
                "disconfirming_signal": self._research_field(research, "disconfirming_signal"),
                "sources": self._research_list(research, "sources"),
            },
        }

    def get_latest_run(self) -> dict[str, Any]:
        with self._connect() as conn:
            if not self._predictions_table_exists(conn):
                return {
                    "forecast_timestamp": None,
                    "markets_processed": 0,
                    "items": [],
                }

            valid_filter = self._valid_where_clause(conn)
            latest_ts_row = conn.execute(
                f"SELECT MAX(forecast_timestamp) FROM predictions WHERE 1=1{valid_filter}"
            ).fetchone()
            latest_ts = str(latest_ts_row[0]) if latest_ts_row and latest_ts_row[0] else None
            if not latest_ts:
                return {
                    "forecast_timestamp": None,
                    "markets_processed": 0,
                    "items": [],
                }

            rows = conn.execute(
                f"""
                SELECT
                    market_id,
                    question,
                    {self._select_expression(conn, "category", cast_text=True)},
                    {self._select_expression(conn, "market_price")},
                    {self._select_expression(conn, "dexter_p")},
                    {self._select_expression(conn, "naked_p")},
                    {self._select_expression(conn, "dexter_confidence", cast_text=True)},
                    {self._select_expression(conn, "volume")},
                    {self._select_expression(conn, "end_date", cast_text=True)}
                FROM predictions
                WHERE forecast_timestamp = ?
                """
                + valid_filter
                + """
                ORDER BY id ASC
                """,
                (latest_ts,),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            market_price = row[3]
            research_probability = row[4]
            naked_probability = row[5]
            best_probability = research_probability if research_probability is not None else naked_probability
            edge = None
            if best_probability is not None and market_price is not None:
                edge = float(best_probability) - float(market_price)
            items.append(
                {
                    "market_id": str(row[0]),
                    "question": row[1],
                    "category": row[2],
                    "market_price": market_price,
                    "research_probability": research_probability,
                    "naked_probability": naked_probability,
                    "confidence": row[6],
                    "research_status": "ON" if research_probability is not None else "DEGRADED",
                    "edge": edge,
                    "volume": row[7],
                    "end_date": row[8],
                }
            )

        return {
            "forecast_timestamp": latest_ts,
            "markets_processed": len(items),
            "items": items,
        }

    def get_watchlist_count(self) -> int:
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "watchlist"):
                    return 0
                row = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()
                return int(row[0]) if row else 0
        except (FileNotFoundError, sqlite3.Error):
            return 0

    def get_health(self) -> dict[str, Any]:
        system_status = "ON"
        checks: list[dict[str, str]] = []
        llm_configured = self._has_any_env(LLM_PROVIDER_ENV_KEYS)
        exa_configured = bool(self._env_value("EXA_API_KEY"))
        tavily_configured = bool(self._env_value("TAVILY_API_KEY"))
        telegram_configured = bool(self._env_value("TELEGRAM_BOT_TOKEN") and self._env_value("TELEGRAM_CHAT_ID"))
        scheduler_visible = self.log_dir.exists() or self._status_file_path().exists()
        research_status = "ON"
        research_warning = ""
        has_historical_research = False

        try:
            with self._connect() as conn:
                if self._predictions_table_exists(conn):
                    count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
                    has_historical_research = self._has_research_predictions(conn)
                    checks.append(self._component_check("sqlite", "ON", f"{count} predictions"))
                else:
                    system_status = "DEGRADED"
                    checks.append(self._component_check("sqlite", "DEGRADED", "predictions table missing"))
        except FileNotFoundError:
            system_status = "DEGRADED"
            checks.append(self._component_check("sqlite", "DEGRADED", "database file missing"))
        except sqlite3.Error as exc:
            system_status = "DEGRADED"
            checks.append(self._component_check("sqlite", "DEGRADED", str(exc)))

        llm_model = self._env_value("MODEL_SYNTHESIS") or self._env_value("MODEL_RESEARCH") or "model not set"
        if llm_configured:
            checks.append(self._component_check("llm", "ON", f"configured ({llm_model})"))
        else:
            checks.append(self._component_check("llm", "OFF", "API key missing for LLM backend"))

        checks.append(
            self._component_check(
                "gamma",
                "ON",
                "Polymarket Gamma REST API is public and expected by the pipeline",
            )
        )
        checks.append(
            self._component_check(
                "exa",
                "ON" if exa_configured else "OFF",
                "news search key configured" if exa_configured else "EXA_API_KEY missing",
            )
        )
        checks.append(
            self._component_check(
                "tavily",
                "ON" if tavily_configured else "OFF",
                "fallback search key configured" if tavily_configured else "TAVILY_API_KEY missing",
            )
        )
        checks.append(
            self._component_check(
                "scheduler",
                "ON" if scheduler_visible else "DEGRADED",
                "log directory/status files visible" if scheduler_visible else "scheduler artifacts not visible",
            )
        )
        checks.append(
            self._component_check(
                "telegram",
                "ON" if telegram_configured else "OFF",
                "bot credentials configured" if telegram_configured else "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing",
            )
        )

        if llm_configured and (exa_configured or tavily_configured):
            research_status = "ON"
        elif llm_configured:
            research_status = "DEGRADED"
            research_warning = "LLM доступен, но news-search выключен: research будет беднее без Exa/Tavily."
        elif has_historical_research:
            research_status = "DEGRADED"
            research_warning = "В базе есть результаты Research+R1, но текущая runtime-конфигурация research неполная."
        else:
            research_status = "OFF"
            research_warning = "Research-контур не сконфигурирован: не найден ключ LLM backend."

        return {
            "system_status": system_status,
            "research_status": research_status,
            "research_warning": research_warning,
            "checks": checks,
        }

    @staticmethod
    def _build_verdict_summary(
        *,
        market_price: float | None,
        research_probability: float | None,
        confidence: str | None,
    ) -> str:
        if market_price is None or research_probability is None:
            return "Pythia пока не собрала достаточно данных, чтобы сформулировать итоговый вердикт по рынку."

        edge = float(research_probability) - float(market_price)
        direction = "выше" if edge >= 0 else "ниже"
        strength = "умеренно" if abs(edge) < 10 else "заметно"
        confidence_text = f" при уверенности {confidence}" if confidence else ""
        return (
            f"Pythia {strength} оценивает рынок {direction} текущей рыночной цены:"
            f" {research_probability:.1f}% против {market_price:.1f}%{confidence_text}."
        )

    def get_last_action_summary(self) -> dict[str, Any]:
        status_path = self._status_file_path()
        if not status_path.exists():
            return {
                "action": None,
                "status": "idle",
                "summary": "Без недавних операторских действий.",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "technical_log": "",
                "returncode": None,
            }

        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {
                "action": "unknown",
                "status": "failed",
                "summary": "Не удалось прочитать статус последнего действия.",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "technical_log": "",
                "returncode": None,
            }

        if not isinstance(payload, dict):
            return {
                "action": "unknown",
                "status": "failed",
                "summary": "Некорректный формат статуса последнего действия.",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "technical_log": "",
                "returncode": None,
            }

        if "status" not in payload or "summary" not in payload:
            return {
                "action": payload.get("action"),
                "status": "failed",
                "summary": "Не удалось прочитать полный статус последнего действия.",
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "duration_seconds": payload.get("duration_seconds"),
                "technical_log": str(payload.get("technical_log") or ""),
                "returncode": payload.get("returncode"),
            }

        payload.setdefault("action", None)
        payload.setdefault("started_at", None)
        payload.setdefault("finished_at", None)
        payload.setdefault("duration_seconds", None)
        payload.setdefault("technical_log", "")
        payload.setdefault("returncode", None)
        return payload

    def get_logs_summary(self) -> dict[str, Any]:
        log_files = sorted(self.log_dir.glob("*.log"))
        if not log_files:
            return {
                "latest_file": None,
                "excerpt": "",
            }

        latest_log = log_files[-1]
        excerpt = latest_log.read_text(encoding="utf-8", errors="ignore")[-2000:]
        return {
            "latest_file": latest_log.name,
            "excerpt": excerpt,
        }

    def list_watchlist(self) -> list[dict[str, Any]]:
        with self._connect_rw() as conn:
            self._ensure_watchlist_table(conn)
            if self._predictions_table_exists(conn):
                valid_filter = self._valid_where_clause(conn, alias="p2")
                rows = conn.execute(
                    f"""
                    SELECT
                           w.market_id,
                           w.note,
                           w.created_at,
                           w.updated_at,
                           {self._select_expression(conn, "question", table_alias="p", cast_text=True)},
                           {self._select_expression(conn, "market_price", table_alias="p")},
                           {self._select_expression(conn, "dexter_p", table_alias="p")},
                           {self._select_expression(conn, "naked_p", table_alias="p")},
                           {self._select_expression(conn, "forecast_timestamp", table_alias="p", cast_text=True)}
                    FROM watchlist w
                    LEFT JOIN predictions p
                      ON p.market_id = w.market_id
                     AND p.id = (
                          SELECT id FROM predictions p2
                          WHERE p2.market_id = w.market_id
                    """
                    + valid_filter
                    + """
                          ORDER BY p2.forecast_timestamp DESC, p2.id DESC
                          LIMIT 1
                     )
                    ORDER BY w.updated_at DESC, w.market_id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT w.market_id, w.note, w.created_at, w.updated_at,
                           NULL AS question, NULL AS market_price,
                           NULL AS dexter_p, NULL AS naked_p, NULL AS forecast_timestamp
                    FROM watchlist w
                    ORDER BY w.updated_at DESC, w.market_id ASC
                    """
                ).fetchall()
        return [
            {
                "market_id": str(row[0]),
                "note": str(row[1] or ""),
                "created_at": str(row[2]),
                "updated_at": str(row[3]),
                "question": row[4],
                "market_price": row[5],
                "research_probability": row[6],
                "naked_probability": row[7],
                "forecast_timestamp": row[8],
            }
            for row in rows
        ]

    def add_to_watchlist(
        self,
        market_id: str,
        note: str = "",
        *,
        now_iso: str | None = None,
    ) -> None:
        market_id = str(market_id).strip()
        if not market_id:
            raise ValueError("market_id is required")

        now = now_iso or datetime.now(timezone.utc).isoformat()
        with self._connect_rw() as conn:
            self._ensure_watchlist_table(conn)
            ensure_watchlist_engine_schema(conn)
            conn.execute(
                """
                INSERT INTO watchlist (market_id, note, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (market_id, str(note or "").strip(), now, now),
            )
            bootstrap_watch_dossier(conn, market_id, str(note or "").strip(), now_iso=now)
            conn.commit()

    def remove_from_watchlist(self, market_id: str) -> None:
        with self._connect_rw() as conn:
            self._ensure_watchlist_table(conn)
            ensure_watchlist_engine_schema(conn)
            normalized_market_id = str(market_id).strip()
            conn.execute("DELETE FROM watchlist WHERE market_id = ?", (normalized_market_id,))
            deactivate_watch_dossier(conn, normalized_market_id)
            conn.commit()

    def list_watch_dossiers(self) -> list[dict[str, Any]]:
        self._sync_attention_engine()
        with self._connect() as conn:
            if not self._watchlist_engine_exists(conn):
                return []
            return enrich_dossiers_with_attention(conn, load_watch_dossiers(conn))

    def list_watch_updates(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if not self._watchlist_engine_exists(conn):
                return []
            return load_watch_updates(conn, limit=limit)

    def list_attention_items(self, *, priority: str | None = None, status: str = "open") -> list[dict[str, Any]]:
        self._sync_attention_engine()
        with self._connect() as conn:
            if not self._attention_engine_exists(conn):
                return []
            return load_attention_items(conn, priority=priority, status=status, limit=50)

    def transition_attention_item(self, item_id: int, action: str) -> dict[str, Any]:
        with self._connect_rw() as conn:
            ensure_attention_engine_schema(conn)
            result = apply_attention_transition(conn, item_id, action)
            conn.commit()
            return {
                "id": int(result["id"]),
                "status": str(result["status"]),
                "priority": str(result["priority"]),
                "market_id": str(result["market_id"]),
            }

    def run_safe_action(self, action: str) -> dict[str, Any]:
        if action not in SAFE_ACTION_NAMES:
            raise ValueError(action)

        base_dir = Path(os.getenv("PYTHIA_BASE_DIR", str(Path(__file__).resolve().parent.parent)))
        entrypoint = base_dir / "scripts" / "entrypoint.sh"
        command = ["bash", str(entrypoint), action]
        started_at = datetime.now(timezone.utc)

        completed = subprocess.run(
            command,
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        finished_at = datetime.now(timezone.utc)
        technical_chunks = [chunk.strip() for chunk in [completed.stdout, completed.stderr] if chunk and chunk.strip()]
        technical_log = "\n".join(technical_chunks)[-4000:]
        summary = self._summarize_action_output(
            action=action,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        payload = {
            "action": action,
            "returncode": completed.returncode,
            "status": "success" if completed.returncode == 0 else "failed",
            "summary": summary,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
            "technical_log": technical_log,
        }
        self._status_file_path(ensure_parent=True).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    @staticmethod
    def _summarize_action_output(action: str, returncode: int, stdout: str, stderr: str) -> str:
        if returncode == 0:
            output = stdout.strip()
            if output:
                first_line = output.splitlines()[0].strip()
                if first_line:
                    return first_line
            return f"Команда {action} завершилась успешно."

        error_output = (stderr or stdout).strip()
        if error_output:
            first_line = error_output.splitlines()[0].strip()
            if first_line:
                return first_line
        return f"Команда {action} завершилась с ошибкой."
