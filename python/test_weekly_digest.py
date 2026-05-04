"""Tests for python.weekly_digest (gate #11).

Schema-tolerance is the central guarantee: production DBs vary across
deployments — early ones lack ``decision_core`` tables, others lack
``api_cost_usd`` columns. Each section must degrade independently
without raising.
"""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from python.test_support import seed_prediction, temp_db
from python.weekly_digest import (
    compose_weekly_digest,
    format_weekly_digest_markdown,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DECISION_CORE_SQL = (BASE_DIR / "sql" / "decision_core.sql").read_text(encoding="utf-8")


FIXED_NOW = datetime(2026, 5, 4, 20, 0, 0, tzinfo=timezone.utc)


def _ensure_decision_core(conn: sqlite3.Connection) -> None:
    conn.executescript(DECISION_CORE_SQL)
    conn.commit()


def _book_id(conn: sqlite3.Connection, name: str) -> int:
    """Resolve the book id by name. ``decision_core.sql`` auto-inserts
    ``shadow_default`` and ``operator_default`` via ``INSERT OR IGNORE``,
    so tests never need to seed books themselves."""

    row = conn.execute("SELECT id FROM books WHERE name=?", (name,)).fetchone()
    assert row is not None, f"book {name!r} should exist after decision_core schema"
    return int(row[0])


def _seed_signal(conn: sqlite3.Connection, *, market_id: str = "test-mkt", prediction_id: int = 1) -> int:
    conn.execute(
        """
        INSERT INTO signals (
            prediction_id, market_id, question, forecast_timestamp, market_price,
            research_probability, naked_probability, best_probability, signal_edge,
            signal_strength, recommended_action, research_status, status,
            created_at, updated_at
        ) VALUES (?, ?, 'Q', ?, 50, 65, 60, 65, 15, 'strong', 'open', 'fresh', 'new', ?, ?)
        """,
        (prediction_id, market_id, FIXED_NOW.isoformat(), FIXED_NOW.isoformat(), FIXED_NOW.isoformat()),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    book_id: int,
    market_id: str,
    actor_type: str,
    decision_type: str,
    created_at: datetime,
) -> int:
    conn.execute(
        """
        INSERT INTO decisions (
            signal_id, book_id, market_id, actor_type, decision_type, decision_reason,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'auto-test', 'active', ?)
        """,
        (signal_id, book_id, market_id, actor_type, decision_type, created_at.isoformat()),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _seed_position(
    conn: sqlite3.Connection,
    *,
    book_id: int,
    market_id: str,
    signal_id: int,
    decision_id: int,
    mode: str,
    side: str = "yes",
    status: str = "open",
    opened_at: datetime,
    closed_at: datetime | None = None,
    pnl_usd: float | None = None,
    pnl_percent: float | None = None,
    notional_usd: float = 100.0,
    size_contracts: float = 200.0,
) -> int:
    conn.execute(
        """
        INSERT INTO positions (
            book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
            entry_yes_price, entry_cost_per_contract, size_contracts, notional_usd,
            opened_at, closed_at, pnl_usd, pnl_percent
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.5, 0.5, ?, ?, ?, ?, ?, ?)
        """,
        (
            book_id,
            market_id,
            signal_id,
            decision_id,
            mode,
            side,
            status,
            size_contracts,
            notional_usd,
            opened_at.isoformat(),
            closed_at.isoformat() if closed_at else None,
            pnl_usd,
            pnl_percent,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _seed_postmortem(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    market_id: str,
    forecast_error: int = 0,
    entry_error: int = 0,
    timing_error: int = 0,
    management_error: int = 0,
    discipline_error: int = 0,
    created_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO postmortems (
            position_id, market_id, forecast_error, entry_error, timing_error,
            management_error, discipline_error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id,
            market_id,
            forecast_error,
            entry_error,
            timing_error,
            management_error,
            discipline_error,
            created_at.isoformat(),
            created_at.isoformat(),
        ),
    )


def _add_outcome_columns(conn: sqlite3.Connection) -> None:
    """Predictions seeded by test_support.seed_prediction lack outcome /
    resolved_at / brier_* / api_cost_usd columns. The aggregator handles
    missing columns gracefully but tests that want to exercise resolved
    rows need to add them."""

    cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    for stmt in [
        ("outcome", "ALTER TABLE predictions ADD COLUMN outcome INTEGER"),
        ("resolved_at", "ALTER TABLE predictions ADD COLUMN resolved_at TEXT"),
        ("brier_dexter", "ALTER TABLE predictions ADD COLUMN brier_dexter REAL"),
        ("brier_naked", "ALTER TABLE predictions ADD COLUMN brier_naked REAL"),
        ("brier_market", "ALTER TABLE predictions ADD COLUMN brier_market REAL"),
    ]:
        col, sql = stmt
        if col not in cols:
            conn.execute(sql)
    conn.commit()


class WeeklyDigestComposeTests(unittest.TestCase):
    def test_empty_db_degrades_to_unavailable(self):
        with temp_db() as db_path:
            with sqlite3.connect(db_path) as conn:
                digest = compose_weekly_digest(conn, now=FIXED_NOW)

        self.assertFalse(digest["predictions"]["available"])
        self.assertFalse(digest["brier"]["available"])
        self.assertFalse(digest["decisions"]["available"])
        self.assertFalse(digest["positions"]["available"])
        self.assertFalse(digest["postmortems"]["available"])
        self.assertFalse(digest["reviews"]["available"])
        self.assertFalse(digest["api_cost"]["available"])
        self.assertEqual(digest["generated_at"], FIXED_NOW.isoformat())

    def test_predictions_only_db_populates_predictions_section_only(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="poly-A",
                forecast_timestamp=(FIXED_NOW - timedelta(days=2)).isoformat(),
                end_date=(FIXED_NOW + timedelta(days=10)).isoformat(),
                api_cost_usd=0.50,
            )
            seed_prediction(
                db_path,
                market_id="poly-B",
                forecast_timestamp=(FIXED_NOW - timedelta(days=10)).isoformat(),
                end_date=(FIXED_NOW + timedelta(days=10)).isoformat(),
                api_cost_usd=0.30,
            )
            with sqlite3.connect(db_path) as conn:
                _add_outcome_columns(conn)
                conn.execute(
                    "UPDATE predictions SET outcome=1, resolved_at=?, brier_dexter=0.04, brier_naked=0.09, brier_market=0.16 WHERE market_id=?",
                    ((FIXED_NOW - timedelta(days=1)).isoformat(), "poly-B"),
                )
                conn.commit()
                digest = compose_weekly_digest(conn, now=FIXED_NOW)

        predictions = digest["predictions"]
        self.assertTrue(predictions["available"])
        self.assertEqual(predictions["new_this_week"], 1)  # poly-A within 7d
        self.assertEqual(predictions["resolved_this_week"], 1)  # poly-B
        self.assertEqual(predictions["total"], 2)
        self.assertEqual(predictions["resolved"], 1)
        self.assertEqual(predictions["pending"], 1)

        brier = digest["brier"]
        self.assertTrue(brier["available"])
        self.assertAlmostEqual(brier["brier_dexter"], 0.04)
        self.assertAlmostEqual(brier["brier_naked"], 0.09)
        self.assertAlmostEqual(brier["brier_market"], 0.16)
        # delta = naked - dexter = 0.05 → research helps by 0.05
        self.assertAlmostEqual(brier["delta_naked_minus_dexter"], 0.05)
        self.assertEqual(brier["resolved_count"], 1)

        api_cost = digest["api_cost"]
        self.assertTrue(api_cost["available"])
        # FIXED_NOW = 2026-05-04 → month_start = 2026-05-01.
        # poly-A is 2d ago (2026-05-02, $0.50, included), poly-B is 10d
        # ago (2026-04-24, $0.30, NOT included).
        self.assertAlmostEqual(api_cost["month_usd"], 0.50)

        # decision_core sections all unavailable
        self.assertFalse(digest["decisions"]["available"])
        self.assertFalse(digest["positions"]["available"])
        self.assertFalse(digest["postmortems"]["available"])

    def test_full_db_populates_decision_core_sections(self):
        with temp_db() as db_path:
            seed_prediction(
                db_path,
                market_id="poly-X",
                forecast_timestamp=(FIXED_NOW - timedelta(days=3)).isoformat(),
                end_date=(FIXED_NOW + timedelta(days=10)).isoformat(),
                api_cost_usd=0.0,
            )
            with sqlite3.connect(db_path) as conn:
                _ensure_decision_core(conn)
                shadow_book = _book_id(conn, "shadow_default")
                operator_book = _book_id(conn, "operator_default")
                signal_id = _seed_signal(conn, market_id="poly-X", prediction_id=1)

                # 3 decisions this week:
                #   operator/open
                #   operator/skip
                #   system/snooze
                op_open_id = _seed_decision(
                    conn,
                    signal_id=signal_id,
                    book_id=operator_book,
                    market_id="poly-X",
                    actor_type="operator",
                    decision_type="open",
                    created_at=FIXED_NOW - timedelta(days=2),
                )
                _seed_decision(
                    conn,
                    signal_id=signal_id,
                    book_id=operator_book,
                    market_id="poly-X",
                    actor_type="operator",
                    decision_type="skip",
                    created_at=FIXED_NOW - timedelta(days=1),
                )
                _seed_decision(
                    conn,
                    signal_id=signal_id,
                    book_id=shadow_book,
                    market_id="poly-X",
                    actor_type="system",
                    decision_type="snooze",
                    created_at=FIXED_NOW - timedelta(days=1, hours=12),
                )
                # one decision OUTSIDE the window — must NOT count
                _seed_decision(
                    conn,
                    signal_id=signal_id,
                    book_id=operator_book,
                    market_id="poly-X",
                    actor_type="operator",
                    decision_type="open",
                    created_at=FIXED_NOW - timedelta(days=10),
                )

                # 2 positions opened this week, 1 closed (winner +$15.50, +15.5%)
                p1 = _seed_position(
                    conn,
                    book_id=operator_book,
                    market_id="poly-X",
                    signal_id=signal_id,
                    decision_id=op_open_id,
                    mode="operator",
                    opened_at=FIXED_NOW - timedelta(days=4),
                    closed_at=FIXED_NOW - timedelta(days=1),
                    status="closed",
                    pnl_usd=15.50,
                    pnl_percent=15.5,
                    notional_usd=100.0,
                )
                _seed_position(
                    conn,
                    book_id=shadow_book,
                    market_id="poly-X",
                    signal_id=signal_id,
                    decision_id=op_open_id,
                    mode="shadow",
                    opened_at=FIXED_NOW - timedelta(days=2),
                    status="open",
                    notional_usd=80.0,
                )
                # one losing position closed this week
                p3 = _seed_position(
                    conn,
                    book_id=operator_book,
                    market_id="poly-X",
                    signal_id=signal_id,
                    decision_id=op_open_id,
                    mode="operator",
                    opened_at=FIXED_NOW - timedelta(days=5),
                    closed_at=FIXED_NOW - timedelta(days=2),
                    status="closed",
                    pnl_usd=-7.25,
                    pnl_percent=-7.25,
                    notional_usd=100.0,
                )

                _seed_postmortem(
                    conn,
                    position_id=p1,
                    market_id="poly-X",
                    forecast_error=1,
                    timing_error=1,
                    created_at=FIXED_NOW - timedelta(days=1),
                )
                _seed_postmortem(
                    conn,
                    position_id=p3,
                    market_id="poly-X",
                    discipline_error=1,
                    created_at=FIXED_NOW - timedelta(days=2),
                )
                # postmortem outside the 30d window — must NOT count
                _seed_postmortem(
                    conn,
                    position_id=p3 + 1000,  # not a real fk but no fk enforcement here
                    market_id="poly-X",
                    forecast_error=1,
                    created_at=FIXED_NOW - timedelta(days=45),
                )

                conn.commit()
                digest = compose_weekly_digest(conn, now=FIXED_NOW)

        decisions = digest["decisions"]
        self.assertTrue(decisions["available"])
        self.assertEqual(decisions["total_this_week"], 3)
        self.assertEqual(
            decisions["by_actor_type"]["operator"],
            {"open": 1, "skip": 1},
        )
        self.assertEqual(decisions["by_actor_type"]["system"], {"snooze": 1})

        positions = digest["positions"]
        self.assertTrue(positions["available"])
        # 3 positions opened in the window: p1 (operator, -4d), shadow
        # (-2d), p3 (operator, -5d)
        self.assertEqual(positions["opened_this_week_by_mode"], {"operator": 2, "shadow": 1})
        self.assertEqual(positions["closed_this_week_count"], 2)
        self.assertEqual(positions["wins_this_week"], 1)
        self.assertEqual(positions["losses_this_week"], 1)
        self.assertAlmostEqual(positions["realized_pnl_week_usd"], 15.50 - 7.25)
        self.assertEqual(positions["win_rate_this_week"], 0.5)
        self.assertEqual(positions["top_winner"]["pnl_usd"], 15.50)
        self.assertEqual(positions["top_loser"]["pnl_usd"], -7.25)
        self.assertEqual(positions["open_by_mode"]["shadow"]["n"], 1)

        postmortems = digest["postmortems"]
        self.assertTrue(postmortems["available"])
        self.assertEqual(postmortems["total"], 2)  # third is outside 30d window
        self.assertEqual(postmortems["forecast_error"], 1)
        self.assertEqual(postmortems["timing_error"], 1)
        self.assertEqual(postmortems["discipline_error"], 1)
        self.assertEqual(postmortems["entry_error"], 0)
        self.assertEqual(postmortems["management_error"], 0)


class WeeklyDigestFormatTests(unittest.TestCase):
    def test_empty_digest_renders_predictions_unavailable_notice(self):
        digest = {
            "generated_at": "2026-05-04T20:00:00+00:00",
            "predictions": {"available": False},
            "brier": {"available": False},
            "decisions": {"available": False},
            "positions": {"available": False},
            "postmortems": {"available": False},
            "reviews": {"available": False},
            "api_cost": {"available": False},
        }
        msg = format_weekly_digest_markdown(digest)
        self.assertIn("📊 *Еженедельный отчёт Pythia*", msg)
        self.assertIn("_2026-05-04_", msg)
        self.assertIn("Таблица predictions ещё не создана", msg)

    def test_full_digest_includes_every_section_header(self):
        digest = {
            "generated_at": "2026-05-04T20:00:00+00:00",
            "predictions": {
                "available": True,
                "new_this_week": 5,
                "resolved_this_week": 2,
                "total": 100,
                "resolved": 60,
                "pending": 40,
            },
            "brier": {
                "available": True,
                "brier_dexter": 0.21,
                "brier_naked": 0.24,
                "brier_market": 0.30,
                "delta_naked_minus_dexter": 0.03,
                "resolved_count": 60,
            },
            "decisions": {
                "available": True,
                "total_this_week": 7,
                "by_actor_type": {
                    "operator": {"open": 3, "skip": 2},
                    "system": {"snooze": 2},
                },
            },
            "positions": {
                "available": True,
                "opened_this_week_by_mode": {"shadow": 2, "operator": 1},
                "closed_this_week_count": 2,
                "wins_this_week": 1,
                "losses_this_week": 1,
                "realized_pnl_week_usd": 8.25,
                "win_rate_this_week": 0.5,
                "top_winner": {"market_id": "poly-W", "pnl_usd": 15.50, "pnl_percent": 15.5},
                "top_loser": {"market_id": "poly-L", "pnl_usd": -7.25, "pnl_percent": -7.25},
                "open_by_mode": {"shadow": {"n": 3, "notional_usd": 240.0}},
            },
            "postmortems": {
                "available": True,
                "window_days": 30,
                "total": 2,
                "forecast_error": 1,
                "entry_error": 0,
                "timing_error": 1,
                "management_error": 0,
                "discipline_error": 1,
            },
            "reviews": {
                "available": True,
                "due": 3,
                "completed_this_week": 5,
            },
            "api_cost": {"available": True, "month_usd": 12.34},
        }
        msg = format_weekly_digest_markdown(digest)
        for header in (
            "*За неделю:*",
            "*С начала наблюдений:*",
            "*Decision discipline (за неделю):*",
            "*Активность позиций (за неделю):*",
            "*Postmortem-разборы (последние 30 дн.):*",
            "*Reviews:*",
            "*Brier score (↓ меньше = лучше):*",
            "*Расход API в этом месяце:*",
        ):
            self.assertIn(header, msg, f"missing section: {header}")
        self.assertIn("Top winner", msg)
        self.assertIn("Top loser", msg)
        self.assertIn("Research+R1 лучше Naked", msg)
        self.assertIn("$12.34", msg)
        self.assertIn("Realized P&L: $8.25", msg)

    def test_negative_brier_delta_emits_warning(self):
        digest = {
            "generated_at": "2026-05-04T20:00:00+00:00",
            "predictions": {"available": False},
            "brier": {
                "available": True,
                "brier_dexter": 0.30,
                "brier_naked": 0.22,
                "brier_market": 0.28,
                "delta_naked_minus_dexter": -0.08,
                "resolved_count": 40,
            },
            "decisions": {"available": False},
            "positions": {"available": False},
            "postmortems": {"available": False},
            "reviews": {"available": False},
            "api_cost": {"available": False},
        }
        msg = format_weekly_digest_markdown(digest)
        self.assertIn("⚠️", msg)
        self.assertIn("Naked лучше Research+R1", msg)


if __name__ == "__main__":
    unittest.main()
