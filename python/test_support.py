from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from python.dashboard_api import create_app
from python.watchlist_store import bootstrap_watch_dossier, ensure_watchlist_engine_schema


@contextmanager
def temp_db() -> Iterator[str]:
    with TemporaryDirectory() as temp_dir:
      yield str(Path(temp_dir) / "pythia-test.db")


def seed_prediction(
    db_path: str,
    *,
    market_id: str,
    question: str = "Test market",
    category: str = "tests",
    market_price: float | None = 12.0,
    dexter_p: float | None = 30.0,
    naked_p: float | None = 35.0,
    dexter_confidence: str | None = "medium",
    dexter_reasoning: str = "",
    dexter_key_risk: str = "",
    dexter_base_rate: str = "",
    research_brief_json: str = "{}",
    naked_reasoning: str = "",
    volume: float | None = None,
    created_at: str | None = None,
    forecast_timestamp: str,
    end_date: str | None,
    api_cost_usd: float | None = 0,
    is_valid: int = 1,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                question TEXT NOT NULL,
                category TEXT,
                market_price REAL,
                dexter_p REAL,
                naked_p REAL,
                dexter_confidence TEXT,
                dexter_reasoning TEXT,
                dexter_key_risk TEXT,
                dexter_base_rate TEXT,
                research_brief_json TEXT,
                naked_reasoning TEXT,
                volume REAL,
                created_at TEXT,
                end_date TEXT,
                forecast_timestamp TEXT NOT NULL,
                api_cost_usd REAL DEFAULT 0,
                is_valid INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
                market_id, question, category, market_price, dexter_p, naked_p,
                dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                research_brief_json, naked_reasoning, volume, created_at, end_date, forecast_timestamp, api_cost_usd, is_valid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id,
                question,
                category,
                market_price,
                dexter_p,
                naked_p,
                dexter_confidence,
                dexter_reasoning,
                dexter_key_risk,
                dexter_base_rate,
                research_brief_json,
                naked_reasoning,
                volume,
                created_at or forecast_timestamp,
                end_date,
                forecast_timestamp,
                api_cost_usd,
                is_valid,
            ),
        )
        conn.commit()


def seed_watch_update(
    db_path: str,
    *,
    market_id: str,
    status_label: str,
    reason_summary: str,
    action_summary: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_watchlist_engine_schema(conn)
        bootstrap_watch_dossier(conn, market_id, now_iso="2026-04-07T06:00:00Z")
        conn.execute(
            """
            INSERT INTO watch_refresh_runs (
                started_at, finished_at, status, markets_total, markets_checked, markets_changed,
                deep_reviews_triggered, alerts_created, error_summary
            ) VALUES ('2026-04-07T12:00:00Z', '2026-04-07T12:05:00Z', 'success', 1, 1, 1, 0, 1, '')
            """
        )
        conn.execute(
            """
            INSERT INTO watch_updates (
                watch_dossier_id, refresh_run_id, created_at, severity, status_label,
                reason_summary, action_summary, change_summary_json
            ) VALUES (1, 1, '2026-04-07T12:10:00Z', 'review', ?, ?, ?, '{}')
            """,
            (status_label, reason_summary, action_summary),
        )
        conn.commit()


def create_test_app(db_path: str):
    db_path_obj = Path(db_path)
    return create_app(
        db_path=db_path_obj,
        log_dir=db_path_obj.parent,
        session_secret="test-secret",
        dashboard_username="operator",
        dashboard_password_hash="plain:secret-pass",
    )
