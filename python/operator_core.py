from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote


class OperatorCore:
    def __init__(self, db_path: Path | str, log_dir: Path | str):
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir)

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        db_uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
        return sqlite3.connect(db_uri, uri=True)

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
