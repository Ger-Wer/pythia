from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

SAFE_ACTION_NAMES = {"forecast", "resolve", "weekly"}


class OperatorCore:
    def __init__(self, db_path: Path | str, log_dir: Path | str):
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir)

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        db_uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
        return sqlite3.connect(db_uri, uri=True)

    def _connect_rw(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

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
    def _prediction_columns(conn: sqlite3.Connection) -> set[str]:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}

    def _has_column(self, conn: sqlite3.Connection, column_name: str) -> bool:
        return column_name in self._prediction_columns(conn)

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
                    "valid_predictions": 0,
                    "latest_run": {
                        "forecast_timestamp": None,
                        "markets_processed": 0,
                    },
                }

            valid_filter = self._valid_where_clause(conn)
            valid_predictions = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE 1=1{valid_filter}"
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

        return {
            "valid_predictions": valid_predictions,
            "latest_run": {
                "forecast_timestamp": latest_ts,
                "markets_processed": markets_processed,
            },
        }

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

        if not row:
            raise LookupError(market_id)

        research = self._parse_research_brief(row[9])
        return {
            "market_id": str(row[0]),
            "question": row[1],
            "market_price": row[2],
            "research_probability": row[3],
            "naked_probability": row[4],
            "confidence": row[5],
            "reasoning": row[6],
            "key_risk": row[7],
            "base_rate": row[8],
            "volume": row[10],
            "end_date": row[11],
            "forecast_timestamp": row[12],
            "research": {
                "summary": str(research.get("summary") or "").strip(),
                "key_facts": self._research_list(research, "key_facts"),
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
                """
                SELECT market_id, question, category, market_price, dexter_p, naked_p, volume, end_date
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
                    "edge": edge,
                    "volume": row[6],
                    "end_date": row[7],
                }
            )

        return {
            "forecast_timestamp": latest_ts,
            "markets_processed": len(items),
            "items": items,
        }

    def list_watchlist(self) -> list[dict[str, Any]]:
        with self._connect_rw() as conn:
            self._ensure_watchlist_table(conn)
            if self._predictions_table_exists(conn):
                valid_filter = self._valid_where_clause(conn, alias="p2")
                rows = conn.execute(
                    """
                    SELECT w.market_id, w.note, w.created_at, w.updated_at,
                           p.question, p.market_price, p.dexter_p, p.naked_p, p.forecast_timestamp
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

    def add_to_watchlist(self, market_id: str, note: str = "") -> None:
        market_id = str(market_id).strip()
        if not market_id:
            raise ValueError("market_id is required")

        now = datetime.now(timezone.utc).isoformat()
        with self._connect_rw() as conn:
            self._ensure_watchlist_table(conn)
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
            conn.commit()

    def remove_from_watchlist(self, market_id: str) -> None:
        with self._connect_rw() as conn:
            self._ensure_watchlist_table(conn)
            conn.execute("DELETE FROM watchlist WHERE market_id = ?", (str(market_id).strip(),))
            conn.commit()

    def run_safe_action(self, action: str) -> dict[str, Any]:
        if action not in SAFE_ACTION_NAMES:
            raise ValueError(action)

        base_dir = Path(os.getenv("PYTHIA_BASE_DIR", str(Path(__file__).resolve().parent.parent)))
        entrypoint = base_dir / "scripts" / "entrypoint.sh"
        command = ["bash", str(entrypoint), action]

        completed = subprocess.run(
            command,
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "action": action,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "status": "success" if completed.returncode == 0 else "failed",
        }
