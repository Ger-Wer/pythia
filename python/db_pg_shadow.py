"""Minimal Postgres shadow-write helper for the Python side of Pythia.

Phase 1 — Track D (Dual-Loop). Full Python-side shadow mirroring of
``decision_store`` / ``watchlist_store`` / ``attention_store`` is **deferred**
to a follow-up PR (see ``docs/dual_loop/07-risks.md`` R13 for the mitigation
notes). The TS side is the primary write path; Python stores currently write
only to SQLite and are compared via ``bun run db:parity`` at the
``predictions`` level.

This module exists so Python code can opportunistically shadow-write simple
rows (today only ``predictions`` row-level upserts, used by the resolution
checker) without depending on the TS adapter. It is:

- Optional: does nothing unless ``DUAL_LOOP_SHADOW_PG`` is truthy AND a
  psycopg/psycopg2 driver is available.
- Fire-and-forget: errors are logged to ``logs/shadow_pg_errors.log`` and
  swallowed. Primary SQLite writes are never blocked.
- Pool-of-one: keeps a single module-level connection; reconnects lazily.

If you need richer shadow-write support for a store, extend this module
(not the callers) so all the env-gating lives in one place.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

_LOGGER = logging.getLogger(__name__)

_SHADOW_FLAG_ENV = "DUAL_LOOP_SHADOW_PG"
_DEFAULT_URL = "postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev"
_LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
_LOG_PATH = _LOG_DIR / "shadow_pg_errors.log"

_lock = threading.Lock()
_conn: Any | None = None
_driver: str | None = None  # 'psycopg' or 'psycopg2'
_warned = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_shadow_enabled() -> bool:
    return _truthy(os.getenv(_SHADOW_FLAG_ENV))


def resolve_url() -> str:
    return os.getenv("DUAL_LOOP_PG_URL") or os.getenv("DATABASE_URL") or _DEFAULT_URL


def _log_shadow_error(operation: str, payload: Mapping[str, Any], err: Exception) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "operation": operation,
        "error": str(err),
        "payload": dict(payload),
    }
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # pragma: no cover - logging must never raise
        pass


def _connect() -> Any | None:
    """Try psycopg (v3) first, fall back to psycopg2; return None if neither available."""
    global _driver
    try:
        import psycopg  # type: ignore[import-not-found]

        _driver = "psycopg"
        return psycopg.connect(resolve_url(), autocommit=True)
    except ImportError:
        pass
    except Exception as err:
        _log_shadow_error("connect:psycopg", {"url": resolve_url()}, err)
        return None
    try:
        import psycopg2  # type: ignore[import-not-found]

        _driver = "psycopg2"
        conn = psycopg2.connect(resolve_url())
        conn.autocommit = True
        return conn
    except ImportError:
        return None
    except Exception as err:
        _log_shadow_error("connect:psycopg2", {"url": resolve_url()}, err)
        return None


def _get_conn() -> Any | None:
    global _conn, _warned
    if not is_shadow_enabled():
        return None
    with _lock:
        if _conn is not None:
            return _conn
        _conn = _connect()
        if _conn is None and not _warned:
            _warned = True
            _LOGGER.warning(
                "DUAL_LOOP_SHADOW_PG=true but no psycopg/psycopg2 available or connection failed; Python-side shadow writes disabled.",
            )
        return _conn


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def _execute(sql: str, params: Iterable[Any], *, operation: str) -> None:
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
    except Exception as err:
        _log_shadow_error(operation, {"sql": sql}, err)
        # Reset the cached connection so the next call reconnects cleanly.
        close()


# ---------------------------------------------------------------------------
# Public helpers — keep this list minimal; extend as Python stores migrate.
# ---------------------------------------------------------------------------

def pg_shadow_update_outcome(market_id: str, outcome: int, resolved_at: str) -> None:
    """Mirror the SQLite outcome-update used by the resolution checker."""
    if not is_shadow_enabled():
        return
    _execute(
        """
        UPDATE predictions
        SET
          outcome = %s,
          resolved_at = %s,
          brier_dexter = CASE
            WHEN dexter_p IS NOT NULL
            THEN ((dexter_p / 100.0) - %s) * ((dexter_p / 100.0) - %s)
            ELSE NULL
          END,
          brier_naked = CASE
            WHEN naked_p IS NOT NULL
            THEN ((naked_p / 100.0) - %s) * ((naked_p / 100.0) - %s)
            ELSE NULL
          END,
          brier_market = ((market_price / 100.0) - %s) * ((market_price / 100.0) - %s)
        WHERE market_id = %s AND is_valid = 1
        """,
        (
            outcome,
            resolved_at,
            outcome,
            outcome,
            outcome,
            outcome,
            outcome,
            outcome,
            market_id,
        ),
        operation="update_outcome",
    )


__all__ = [
    "close",
    "is_shadow_enabled",
    "pg_shadow_update_outcome",
    "resolve_url",
]
