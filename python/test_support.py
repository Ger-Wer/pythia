from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from python.dashboard_api import create_app


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
    volume: float | None = None,
    created_at: str | None = None,
    forecast_timestamp: str,
    end_date: str | None,
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
                volume REAL,
                created_at TEXT,
                end_date TEXT,
                forecast_timestamp TEXT NOT NULL,
                is_valid INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
                market_id, question, category, market_price, dexter_p, naked_p,
                dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
                research_brief_json, volume, created_at, end_date, forecast_timestamp, is_valid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                volume,
                created_at or forecast_timestamp,
                end_date,
                forecast_timestamp,
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_dossiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                first_watched_at TEXT NOT NULL,
                freshness_policy TEXT NOT NULL,
                initial_snapshot_state TEXT NOT NULL,
                thesis_state TEXT NOT NULL,
                operator_note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_refresh_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_dossier_id INTEGER NOT NULL,
                refresh_run_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                severity TEXT NOT NULL,
                status_label TEXT NOT NULL,
                reason_summary TEXT NOT NULL,
                action_summary TEXT NOT NULL,
                change_summary_json TEXT NOT NULL DEFAULT '{}',
                delivered_to_dashboard_at TEXT,
                delivered_to_telegram_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO watch_dossiers (
                market_id, status, created_at, updated_at, first_watched_at,
                freshness_policy, initial_snapshot_state, thesis_state, operator_note
            ) VALUES (?, 'active', '2026-04-07T06:00:00Z', '2026-04-07T06:00:00Z', '2026-04-07T06:00:00Z', 'normal_24h', 'reused', 'review_required', '')
            """,
            (market_id,),
        )
        conn.execute(
            "INSERT INTO watch_refresh_runs (started_at, status) VALUES ('2026-04-07T12:00:00Z', 'success')"
        )
        conn.execute(
            """
            INSERT INTO watch_updates (
                watch_dossier_id, refresh_run_id, created_at, severity, status_label,
                reason_summary, action_summary
            ) VALUES (1, 1, '2026-04-07T12:10:00Z', 'review', ?, ?, ?)
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
