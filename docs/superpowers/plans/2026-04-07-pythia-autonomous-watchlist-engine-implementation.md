# Pythia Autonomous Watchlist Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Построить отдельный `Autonomous Watchlist Engine`, который превращает `Избранное` из пассивного списка в автономный контур сопровождения рынков с `watch dossier`, `watchlist refresh`, короткими operator-facing updates и Telegram-alerts.

**Architecture:** `forecast stack` продолжает искать новые идеи и писать `predictions/signals`, `decision core` продолжает хранить `decisions/positions/reviews/postmortems`, а новый `watchlist engine` получает собственные SQL-таблицы, Bun runtime-job, read-model в Python и тонкий UI-слой в dashboard. Источники и LLM работают в двух режимах: дешёвый `monitoring mode` на каждом refresh и дорогой `deep review mode` только по композитным триггерам.

**Tech Stack:** SQLite, Bun/TypeScript, Python 3.11, FastAPI, React/Vite, supercronic, Telegram Bot API, Exa/Tavily, Groq OpenAI-compatible API

---

## File Structure

### Create
- `src/services/test_database.ts` — общие Bun test fixtures для in-memory SQLite и seed helpers
- `sql/watchlist_engine.sql` — source of truth для схемы `watch_dossiers`, `watch_refresh_runs`, `watch_refresh_snapshots`, `watch_updates`
- `src/services/watchlist_engine.ts` — Bun engine: bootstrap dossier, collect targets, monitoring mode, deep review mode, snapshot/update persistence, refresh orchestration
- `src/services/watchlist_engine.test.ts` — TDD для schema/bootstrap/refresh/deep-review/update logic
- `src/watchlist_refresh.ts` — отдельный runtime entrypoint для `watchlist refresh`
- `python/test_support.py` — общие Python test fixtures: temp-db, seed helpers, test app factory
- `python/watchlist_store.py` — Python read/write layer поверх watchlist engine tables для dashboard и Telegram delivery
- `python/test_watchlist_store.py` — unit-тесты Python store и dossier/update queries
- `dashboard/src/components/WatchDossierCard.tsx` — компактная карточка dossier для новой страницы `Избранное`
- `dashboard/src/components/WatchUpdateFeed.tsx` — лента коротких updates `status / reason / action`
- `dashboard/src/__tests__/WatchDossierCard.test.tsx` — UI-тест карточки dossier
- `dashboard/src/__tests__/WatchUpdateFeed.test.tsx` — UI-тест update-feed

### Modify
- `src/services/db.ts` — подключить `ensureWatchlistEngineSchema()` рядом с `ensureDecisionCoreSchema()`
- `src/main.ts` — не добавлять refresh-логику, только оставить daily forecast независимым и писать данные, которые dossier может reuse
- `src/services/research_runner.ts` — экспортировать компактный `news digest` helper и deep-review helper, не меняя daily forecast semantics
- `scripts/entrypoint.sh` — добавить `watchlist-refresh` и `watch-alerts`
- `supercronic.crontab` — добавить refresh-cycle каждые 6 часов и отдельный alert dispatch
- `python/operator_core.py` — при `add_to_watchlist()` создавать dossier; читать dossier/update history для dashboard
- `python/dashboard_models.py` — новые response-модели dossier/update/history
- `python/dashboard_api.py` — новые endpoints `watchlist/dossiers`, `watchlist/updates`, `watchlist/workspace`
- `python/operator_actions.py` — русские summaries для watchlist refresh/status при ручных командах
- `python/telegram_bot.py` — отправка недоставленных `watch_updates` и отметка `delivered_to_telegram_at`
- `python/test_operator_core.py` — bootstrap dossier на add-to-watchlist и read-model coverage
- `python/test_dashboard_api.py` — read API watch dossier/update history
- `dashboard/src/types/api.ts` — dossier/update types
- `dashboard/src/api/client.ts` — dossier/update endpoints
- `dashboard/src/pages/WatchlistPage.tsx` — превратить в operator board из dossier cards + update feed
- `dashboard/src/pages/MarketDetailPage.tsx` — сверху показать `watch dossier`, `last update`, `action now`; raw context увести во второй слой
- `dashboard/src/styles.css` — стили dossier board и compact update feed
- `dashboard/src/__tests__/WatchlistPage.test.tsx` — адаптировать к dossier board
- `dashboard/src/__tests__/MarketDetailPage.test.tsx` — проверить dossier-first market view
- `README.md` — новый runtime и operator workflow
- `docs/ARCHITECTURE.md` — границы `forecast / watchlist engine / decision core`
- `docs/DEPLOYMENT.md` — новый cron/job и ручные команды
- `docs/AUDIT.md` — обновить состояние прод-системы

## Task 0: Extract Shared Test Fixtures for Bun and Python

**Files:**
- Create: `src/services/test_database.ts`
- Create: `python/test_support.py`
- Modify: `src/services/db.test.ts`
- Modify: `src/services/decision_core.test.ts`
- Modify: `python/test_dashboard_api.py`
- Modify: `python/test_operator_core.py`

- [ ] **Step 1: Add Bun fixture helpers**

```ts
import { Database } from "bun:sqlite";

export function createTestDatabase(): Database {
  const db = new Database(":memory:");
  db.exec("PRAGMA foreign_keys = ON");
  db.exec(`
    CREATE TABLE IF NOT EXISTS predictions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      market_id TEXT NOT NULL,
      question TEXT NOT NULL,
      market_price REAL,
      dexter_p REAL,
      naked_p REAL,
      dexter_confidence TEXT,
      dexter_reasoning TEXT,
      research_brief_json TEXT,
      forecast_timestamp TEXT NOT NULL,
      end_date TEXT
    )
  `);
  return db;
}

export function seedPrediction(
  db: Database,
  input: {
    market_id: string;
    question?: string;
    market_price?: number | null;
    dexter_p?: number | null;
    naked_p?: number | null;
    dexter_confidence?: string | null;
    forecast_timestamp: string;
    end_date?: string | null;
  },
): void {
  db.query(
    `
      INSERT INTO predictions (
        market_id, question, market_price, dexter_p, naked_p,
        dexter_confidence, dexter_reasoning, research_brief_json,
        forecast_timestamp, end_date
      ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, '', '{}', ?7, ?8)
    `,
  ).run(
    input.market_id,
    input.question ?? `Question for ${input.market_id}`,
    input.market_price ?? null,
    input.dexter_p ?? null,
    input.naked_p ?? null,
    input.dexter_confidence ?? null,
    input.forecast_timestamp,
    input.end_date ?? null,
  );
}

export function seedWatchDossier(
  db: Database,
  input: {
    market_id: string;
    thesis_state?: string;
    current_market_price?: number | null;
    current_research_probability?: number | null;
    current_naked_probability?: number | null;
    current_confidence?: string | null;
    current_research_status?: string | null;
    current_thesis_summary?: string | null;
  },
): void {
  db.query(
    `
      INSERT INTO watch_dossiers (
        market_id, status, created_at, updated_at, first_watched_at,
        freshness_policy, initial_snapshot_state, thesis_state,
        current_market_price, current_research_probability, current_naked_probability,
        current_confidence, current_research_status, current_thesis_summary,
        operator_note
      ) VALUES (?1, 'active', ?2, ?2, ?2, 'normal_24h', 'reused', ?3, ?4, ?5, ?6, ?7, ?8, ?9, '')
    `,
  ).run(
    input.market_id,
    "2026-04-07T06:00:00Z",
    input.thesis_state ?? "intact",
    input.current_market_price ?? null,
    input.current_research_probability ?? null,
    input.current_naked_probability ?? null,
    input.current_confidence ?? null,
    input.current_research_status ?? null,
    input.current_thesis_summary ?? null,
  );
}

export function seedOpenOperatorPosition(db: Database, input: { market_id: string }): void {
  db.query(
    `
      INSERT INTO positions (
        book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
        entry_yes_price, entry_cost_per_contract, size_contracts, notional_usd,
        review_due_at, opened_at, thesis_cycle_number
      ) VALUES (1, ?1, 1, 1, 'operator', 'yes', 'open', 42, 0.42, 100, 42, '2026-04-08T06:00:00Z', '2026-04-07T06:00:00Z', 1)
    `,
  ).run(input.market_id);
}
```

- [ ] **Step 2: Add Python fixture helpers**

```python
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from fastapi.testclient import TestClient
from python.dashboard_api import create_app


@contextmanager
def temp_db():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "pythia-test.db"
        yield str(db_path)


def seed_prediction(
    db_path: str,
    *,
    market_id: str,
    question: str = "Test market",
    market_price: float | None = 12.0,
    dexter_p: float | None = 30.0,
    naked_p: float | None = 35.0,
    dexter_confidence: str | None = "medium",
    forecast_timestamp: str,
    end_date: str | None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              market_id TEXT NOT NULL,
              question TEXT NOT NULL,
              market_price REAL,
              dexter_p REAL,
              naked_p REAL,
              dexter_confidence TEXT,
              dexter_reasoning TEXT,
              research_brief_json TEXT,
              forecast_timestamp TEXT NOT NULL,
              end_date TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
              market_id, question, market_price, dexter_p, naked_p,
              dexter_confidence, dexter_reasoning, research_brief_json,
              forecast_timestamp, end_date
            ) VALUES (?, ?, ?, ?, ?, ?, '', '{}', ?, ?)
            """,
            (market_id, question, market_price, dexter_p, naked_p, dexter_confidence, forecast_timestamp, end_date),
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
        conn.row_factory = sqlite3.Row
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
    return create_app(db_path=db_path)
```

- [ ] **Step 3: Switch existing tests to import shared fixtures**

```ts
import { createTestDatabase, seedPrediction } from "./test_database";
```

```python
from python.test_support import temp_db, seed_prediction
```

- [ ] **Step 4: Run fixture smoke tests**

Run:

```bash
bun test src/services/db.test.ts src/services/decision_core.test.ts
python3 -m unittest python.test_operator_core python.test_dashboard_api -v
```

Expected: PASS with unchanged existing behavior.

- [ ] **Step 5: Commit**

```bash
git add src/services/test_database.ts python/test_support.py src/services/db.test.ts src/services/decision_core.test.ts python/test_operator_core.py python/test_dashboard_api.py
git commit -m "test: extract shared watchlist fixture helpers"
```

## Task 1: Add Shared Watchlist Engine Schema

**Files:**
- Create: `sql/watchlist_engine.sql`
- Modify: `src/services/db.ts`
- Test: `src/services/db.test.ts`

- [ ] **Step 1: Write the failing Bun schema test**

```ts
it("creates watchlist engine tables and seeds expected indexes", () => {
  const db = createTestDatabase();
  ensureWatchlistEngineSchema(db);

  const tables = db
    .query<{ name: string }, []>(`
      SELECT name
      FROM sqlite_master
      WHERE type = 'table'
      ORDER BY name
    `)
    .all()
    .map((row) => row.name);

  expect(tables).toContain("watch_dossiers");
  expect(tables).toContain("watch_refresh_runs");
  expect(tables).toContain("watch_refresh_snapshots");
  expect(tables).toContain("watch_updates");

  const uniqueDossier = db
    .query<{ sql: string }, []>(`
      SELECT sql
      FROM sqlite_master
      WHERE type = 'index' AND name = 'idx_watch_dossiers_market_id'
    `)
    .get();

  expect(uniqueDossier?.sql ?? "").toContain("market_id");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
bun test src/services/db.test.ts --grep "watchlist engine tables"
```

Expected: FAIL with `ensureWatchlistEngineSchema is not defined` or missing tables.

- [ ] **Step 3: Add the SQL schema source of truth**

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS watch_dossiers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active',
  created_from_prediction_id INTEGER,
  created_from_signal_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  first_watched_at TEXT NOT NULL,
  last_refresh_at TEXT,
  last_deep_review_at TEXT,
  next_review_due_at TEXT,
  freshness_policy TEXT NOT NULL,
  initial_snapshot_state TEXT NOT NULL DEFAULT 'reused',
  thesis_state TEXT NOT NULL DEFAULT 'review_required',
  current_market_price REAL,
  current_research_probability REAL,
  current_naked_probability REAL,
  current_confidence TEXT,
  current_research_status TEXT,
  current_thesis_summary TEXT,
  current_primary_driver TEXT,
  current_invalidation_signal TEXT,
  last_status_label TEXT,
  last_reason_summary TEXT,
  last_action_summary TEXT,
  operator_note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (created_from_prediction_id) REFERENCES predictions(id) ON DELETE SET NULL,
  FOREIGN KEY (created_from_signal_id) REFERENCES signals(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS watch_refresh_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  markets_total INTEGER NOT NULL DEFAULT 0,
  markets_checked INTEGER NOT NULL DEFAULT 0,
  markets_changed INTEGER NOT NULL DEFAULT 0,
  deep_reviews_triggered INTEGER NOT NULL DEFAULT 0,
  alerts_created INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watch_refresh_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  watch_dossier_id INTEGER NOT NULL,
  refresh_run_id INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  market_price REAL,
  research_probability REAL,
  naked_probability REAL,
  edge_vs_market REAL,
  price_delta_since_last REAL,
  thesis_state TEXT NOT NULL,
  research_status TEXT,
  monitoring_status TEXT NOT NULL,
  trigger_flags_json TEXT NOT NULL DEFAULT '{}',
  news_digest_json TEXT NOT NULL DEFAULT '{}',
  monitoring_output_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (watch_dossier_id) REFERENCES watch_dossiers(id) ON DELETE CASCADE,
  FOREIGN KEY (refresh_run_id) REFERENCES watch_refresh_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS watch_updates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  watch_dossier_id INTEGER NOT NULL,
  refresh_run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  severity TEXT NOT NULL,
  status_label TEXT NOT NULL,
  reason_summary TEXT NOT NULL,
  action_summary TEXT NOT NULL,
  market_price_before REAL,
  market_price_after REAL,
  research_probability_before REAL,
  research_probability_after REAL,
  change_summary_json TEXT NOT NULL DEFAULT '{}',
  delivered_to_dashboard_at TEXT,
  delivered_to_telegram_at TEXT,
  FOREIGN KEY (watch_dossier_id) REFERENCES watch_dossiers(id) ON DELETE CASCADE,
  FOREIGN KEY (refresh_run_id) REFERENCES watch_refresh_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_watch_dossiers_market_id ON watch_dossiers(market_id);
CREATE INDEX IF NOT EXISTS idx_watch_dossiers_status_updated_at ON watch_dossiers(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_refresh_runs_status_started_at ON watch_refresh_runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_refresh_snapshots_dossier_captured_at ON watch_refresh_snapshots(watch_dossier_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_updates_dossier_created_at ON watch_updates(watch_dossier_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_updates_delivery ON watch_updates(delivered_to_telegram_at, created_at ASC);
```

- [ ] **Step 4: Wire schema loader into Bun DB bootstrap**

```ts
const WATCHLIST_ENGINE_SQL_PATH = path.resolve(import.meta.dir, "../../sql/watchlist_engine.sql");

export function ensureWatchlistEngineSchema(db: Database): void {
  const sql = readFileSync(WATCHLIST_ENGINE_SQL_PATH, "utf8");
  runSqlScript(db, sql);
}

export function initializeDatabase(): Database {
  const db = new Database(DB_PATH);
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  return db;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
bun test src/services/db.test.ts --grep "watchlist engine tables"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add sql/watchlist_engine.sql src/services/db.ts src/services/db.test.ts
git commit -m "feat: add watchlist engine schema"
```

## Task 2: Bootstrap Watch Dossiers When Markets Enter Watchlist

**Files:**
- Create: `python/watchlist_store.py`
- Modify: `python/operator_core.py`
- Modify: `python/test_operator_core.py`
- Test: `python/test_watchlist_store.py`

- [ ] **Step 1: Write the failing Python bootstrap test**

```python
def test_add_to_watchlist_creates_dossier_from_fresh_prediction(self):
    with temp_db() as db_path:
        seed_prediction(
            db_path,
            market_id="watch-1",
            question="Will Arsenal finish in 2nd place?",
            market_price=12.0,
            dexter_p=30.0,
            naked_p=35.0,
            forecast_timestamp="2026-04-07T06:00:00Z",
            end_date="2026-05-01T12:00:00Z",
        )
        core = OperatorCore(db_path=db_path)

        core.add_to_watchlist("watch-1", "Следить внимательно")

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT market_id, freshness_policy, thesis_state, current_market_price,
                       current_research_probability, current_thesis_summary
                FROM watch_dossiers
                WHERE market_id = 'watch-1'
                """
            ).fetchone()

        assert row[0] == "watch-1"
        assert row[1] == "normal_24h"
        assert row[2] in {"intact", "review_required"}
        assert row[3] == 12.0
        assert row[4] == 30.0
        assert row[5]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest python.test_operator_core.OperatorCoreTests.test_add_to_watchlist_creates_dossier_from_fresh_prediction -v
```

Expected: FAIL because `watch_dossiers` bootstrap does not exist.

- [ ] **Step 3: Add Python store helpers for dossier bootstrap**

```python
WATCHLIST_ENGINE_SQL_PATH = BASE_DIR / "sql" / "watchlist_engine.sql"


def ensure_watchlist_engine_schema(conn: sqlite3.Connection) -> None:
    sql = WATCHLIST_ENGINE_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def freshness_policy_for_end_date(end_date: str | None, now: datetime) -> tuple[str, timedelta]:
    if not end_date:
        return ("normal_24h", timedelta(hours=24))
    horizon = datetime.fromisoformat(end_date.replace("Z", "+00:00")) - now
    if horizon <= timedelta(days=14):
        return ("short_12h", timedelta(hours=12))
    if horizon >= timedelta(days=90):
        return ("long_48h", timedelta(hours=48))
    return ("normal_24h", timedelta(hours=24))


def latest_prediction_for_market(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT id, market_id, market_price, dexter_p, naked_p, dexter_confidence,
               dexter_reasoning, research_brief_json, forecast_timestamp, end_date
        FROM predictions
        WHERE market_id = ?
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_signal_for_market(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT id, recommended_action, signal_strength
        FROM signals
        WHERE market_id = ?
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    return dict(row) if row else None


def thesis_summary_from_prediction(prediction: dict[str, Any] | None) -> str:
    if not prediction:
        return "Initial deep review pending."
    return str(prediction.get("dexter_reasoning") or "Краткий тезис будет обновлён после deep review.")


def primary_driver_from_prediction(prediction: dict[str, Any] | None) -> str:
    if not prediction:
        return "Нет данных."
    try:
        brief = json.loads(prediction.get("research_brief_json") or "{}")
    except json.JSONDecodeError:
        brief = {}
    return str(brief.get("primary_driver") or "Нет данных.")


def invalidation_signal_from_prediction(prediction: dict[str, Any] | None) -> str:
    if not prediction:
        return "Нет данных."
    try:
        brief = json.loads(prediction.get("research_brief_json") or "{}")
    except json.JSONDecodeError:
        brief = {}
    return str(brief.get("disconfirming_signal") or "Нет данных.")


def initial_review_due_at(now: datetime, end_date: str | None, signal: dict[str, Any] | None) -> str:
    if end_date:
        deadline = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    else:
        deadline = now + timedelta(days=365)

    horizon = deadline - now
    hours = 48
    if signal and signal.get("signal_strength") and float(signal["signal_strength"]) >= 15:
        hours = 24
    elif horizon >= timedelta(days=90):
        hours = 72
    due = min(now + timedelta(hours=hours), deadline)
    return due.isoformat()


def bootstrap_watch_dossier(conn: sqlite3.Connection, market_id: str, note: str, now: datetime) -> None:
    ensure_watchlist_engine_schema(conn)
    prediction = latest_prediction_for_market(conn, market_id)
    signal = latest_signal_for_market(conn, market_id)
    policy_name, freshness_window = freshness_policy_for_end_date(prediction["end_date"] if prediction else None, now)
    snapshot_is_fresh = bool(
        prediction
        and now - dateutil.parser.isoparse(prediction["forecast_timestamp"]) <= freshness_window
    )
    review_due = initial_review_due_at(now, prediction["end_date"] if prediction else None, signal)

    conn.execute(
        """
        INSERT INTO watch_dossiers (
            market_id, status, created_from_prediction_id, created_from_signal_id,
            created_at, updated_at, first_watched_at, next_review_due_at,
            freshness_policy, initial_snapshot_state, thesis_state,
            current_market_price, current_research_probability, current_naked_probability,
            current_confidence, current_research_status, current_thesis_summary,
            current_primary_driver, current_invalidation_signal, operator_note
        ) VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            status = 'active',
            updated_at = excluded.updated_at,
            next_review_due_at = excluded.next_review_due_at,
            operator_note = excluded.operator_note
        """,
        (
            market_id,
            prediction["id"] if prediction else None,
            signal["id"] if signal else None,
            now.isoformat(),
            now.isoformat(),
            now.isoformat(),
            review_due,
            policy_name,
            "reused" if snapshot_is_fresh else "pending_deep_review",
            "intact" if snapshot_is_fresh else "review_required",
            prediction["market_price"] if prediction else None,
            prediction["dexter_p"] if prediction else None,
            prediction["naked_p"] if prediction else None,
            prediction["dexter_confidence"] if prediction else None,
            "ON" if prediction and prediction["dexter_p"] is not None else "DEGRADED",
            thesis_summary_from_prediction(prediction),
            primary_driver_from_prediction(prediction),
            invalidation_signal_from_prediction(prediction),
            note.strip(),
        ),
    )
```

- [ ] **Step 4: Call bootstrap from `add_to_watchlist()` and keep delete simple**

```python
def add_to_watchlist(self, market_id: str, note: str = "") -> None:
    market_id = str(market_id).strip()
    if not market_id:
        raise ValueError("market_id is required")

    now = datetime.now(timezone.utc)
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
            (market_id, str(note or "").strip(), now.isoformat(), now.isoformat()),
        )
        bootstrap_watch_dossier(conn, market_id, str(note or ""), now)
        conn.commit()
```

- [ ] **Step 5: Add stale-snapshot coverage**

```python
def test_add_to_watchlist_marks_pending_deep_review_for_stale_snapshot(self):
    with temp_db() as db_path:
        seed_prediction(
            db_path,
            market_id="watch-2",
            forecast_timestamp="2026-04-05T00:00:00Z",
            end_date="2026-04-20T12:00:00Z",
        )
        core = OperatorCore(db_path=db_path)
        core.add_to_watchlist("watch-2", "Нужен автопересмотр")

        with sqlite3.connect(db_path) as conn:
            state = conn.execute(
                "SELECT initial_snapshot_state, thesis_state FROM watch_dossiers WHERE market_id = 'watch-2'"
            ).fetchone()

        assert state == ("pending_deep_review", "review_required")
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
python3 -m unittest \
  python.test_watchlist_store \
  python.test_operator_core.OperatorCoreTests.test_add_to_watchlist_creates_dossier_from_fresh_prediction \
  python.test_operator_core.OperatorCoreTests.test_add_to_watchlist_marks_pending_deep_review_for_stale_snapshot \
  -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add python/watchlist_store.py python/operator_core.py python/test_watchlist_store.py python/test_operator_core.py
git commit -m "feat: bootstrap watch dossiers from watchlist"
```

## Task 3: Build the Watchlist Refresh Runtime Core

**Files:**
- Create: `src/services/watchlist_engine.ts`
- Create: `src/watchlist_refresh.ts`
- Test: `src/services/watchlist_engine.test.ts`
- Modify: `src/services/research_runner.ts`

- [ ] **Step 1: Write the failing refresh orchestration test**

```ts
it("creates a refresh run, snapshots dossiers, and leaves no operator update when nothing changed", async () => {
  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedWatchDossier(db, {
    market_id: "watch-arsenal",
    current_market_price: 12,
    current_research_probability: 30,
    current_naked_probability: 35,
    current_confidence: "medium",
    current_research_status: "ON",
    current_thesis_summary: "Арсенал стабильно держится в топ-2.",
  });

  const result = await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async () => ({ marketPrice: 12, researchProbability: 30, nakedProbability: 35 }),
    fetchNewsDigest: async () => ({ hits: [], summary: "Новых значимых фактов нет." }),
    monitorThesis: async () => ({
      monitoringStatus: "no_change",
      thesisState: "intact",
      statusLabel: "без изменений",
      reasonSummary: "Цена и факты почти не изменились.",
      actionSummary: "Ничего не делать.",
      shouldDeepReview: false,
      triggerFlags: {},
    }),
    deepReview: async () => {
      throw new Error("should not run");
    },
  });

  expect(result.marketsChecked).toBe(1);
  expect(result.marketsChanged).toBe(0);
  expect(result.alertsCreated).toBe(0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
bun test src/services/watchlist_engine.test.ts --grep "creates a refresh run"
```

Expected: FAIL because `runWatchlistRefresh` does not exist.

- [ ] **Step 3: Implement target selection, monitoring mode, and snapshot persistence**

```ts
export interface RefreshDependencies {
  fetchMarketSnapshot: (marketId: string) => Promise<{
    marketPrice: number | null;
    researchProbability: number | null;
    nakedProbability: number | null;
  }>;
  fetchNewsDigest: (input: { marketId: string; question: string; thesisSummary: string | null }) => Promise<{
    hits: NewsResult[];
    summary: string;
  }>;
  monitorThesis: (input: MonitoringInput) => Promise<MonitoringResult>;
  deepReview: (input: DeepReviewInput) => Promise<DeepReviewResult>;
}

export async function runWatchlistRefresh(db: Database, deps: RefreshDependencies): Promise<RefreshRunSummary> {
  ensureWatchlistEngineSchema(db);
  const startedAt = new Date().toISOString();
  const runId = db.run(
    `INSERT INTO watch_refresh_runs (started_at, status, markets_total) VALUES (?1, 'running', ?2)`,
    [startedAt, countRefreshTargets(db)],
  ).lastInsertRowid as number;

  const dossiers = collectRefreshTargets(db);
  let marketsChecked = 0;
  let marketsChanged = 0;
  let deepReviewsTriggered = 0;
  let alertsCreated = 0;

  for (const dossier of dossiers) {
    try {
      const current = await deps.fetchMarketSnapshot(dossier.market_id);
      const newsDigest = await deps.fetchNewsDigest({
        marketId: dossier.market_id,
        question: dossier.question,
        thesisSummary: dossier.current_thesis_summary,
      });
      const monitoring = await deps.monitorThesis({
        dossier,
        current,
        newsDigest,
      });

      persistTechnicalSnapshot(db, {
        runId,
        dossierId: dossier.id,
        current,
        monitoring,
        newsDigest,
      });
      updateDossierAfterMonitoring(db, dossier.id, current, monitoring);

      marketsChecked += 1;

      if (monitoring.shouldDeepReview) {
        deepReviewsTriggered += 1;
      }
      if (monitoring.operatorFacing) {
        marketsChanged += 1;
        createWatchUpdate(db, dossier.id, runId, monitoring.operatorFacing);
        alertsCreated += 1;
      }
    } catch (error) {
      appendRunError(db, runId, dossier.market_id, error);
    }
  }

  db.run(
    `
      UPDATE watch_refresh_runs
      SET finished_at = ?2,
          status = 'success',
          markets_checked = ?3,
          markets_changed = ?4,
          deep_reviews_triggered = ?5,
          alerts_created = ?6
      WHERE id = ?1
    `,
    [runId, new Date().toISOString(), marketsChecked, marketsChanged, deepReviewsTriggered, alertsCreated],
  );

  return { runId, marketsChecked, marketsChanged, deepReviewsTriggered, alertsCreated };
}
```

- [ ] **Step 4: Reuse research helpers instead of building a second search stack**

```ts
export async function buildNewsDigest(question: string, category?: string): Promise<{
  hits: NewsResult[];
  summary: string;
}> {
  const queries = buildSearchQueries(question, category);
  const hits = deduplicateNewsResults(await multiSearchQueries(queries));
  return {
    hits,
    summary: hits.length
      ? hits.slice(0, 3).map((item) => `${item.title}: ${item.snippet}`).join("\n")
      : "Новых значимых публикаций не найдено.",
  };
}
```

- [ ] **Step 5: Add coverage for priority order**

```ts
it("refreshes open positions before regular watchlist markets", async () => {
  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedOpenOperatorPosition(db, { market_id: "position-first" });
  seedWatchDossier(db, { market_id: "watch-second" });

  const visited: string[] = [];

  await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async (marketId) => {
      visited.push(marketId);
      return { marketPrice: 50, researchProbability: 55, nakedProbability: 52 };
    },
    fetchNewsDigest: async () => ({ hits: [], summary: "Нет изменений" }),
    monitorThesis: async () => ({
      monitoringStatus: "no_change",
      thesisState: "intact",
      statusLabel: "без изменений",
      reasonSummary: "Нет изменений",
      actionSummary: "Ничего не делать",
      shouldDeepReview: false,
      triggerFlags: {},
    }),
    deepReview: async () => { throw new Error("unexpected"); },
  });

  expect(visited[0]).toBe("position-first");
  expect(visited[1]).toBe("watch-second");
});
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
bun test src/services/watchlist_engine.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/watchlist_engine.ts src/watchlist_refresh.ts src/services/watchlist_engine.test.ts src/services/research_runner.ts
git commit -m "feat: add watchlist refresh runtime core"
```

## Task 4: Add Deep Review Triggers and Operator-Facing Updates

**Files:**
- Modify: `src/services/watchlist_engine.ts`
- Modify: `src/services/watchlist_engine.test.ts`
- Modify: `src/services/research_runner.ts`

- [ ] **Step 1: Write the failing deep-review trigger test**

```ts
it("creates an operator-facing update when monitoring detects thesis break", async () => {
  const db = createTestDatabase();
  ensureDecisionCoreSchema(db);
  ensureWatchlistEngineSchema(db);
  seedWatchDossier(db, {
    market_id: "watch-break",
    current_market_price: 42,
    current_research_probability: 65,
    current_naked_probability: 60,
    current_confidence: "high",
    current_research_status: "ON",
    current_thesis_summary: "Burnley рискованно держится у зоны вылета.",
  });

  await runWatchlistRefresh(db, {
    fetchMarketSnapshot: async () => ({ marketPrice: 55, researchProbability: 41, nakedProbability: 47 }),
    fetchNewsDigest: async () => ({ hits: [], summary: "Команда потеряла ключевого защитника." }),
    monitorThesis: async () => ({
      monitoringStatus: "thesis_break",
      thesisState: "broken",
      statusLabel: "thesis break",
      reasonSummary: "Новый риск разрушает исходный тезис.",
      actionSummary: "Открыть review и пересмотреть позицию.",
      shouldDeepReview: true,
      triggerFlags: { thesis_break: true },
      operatorFacing: {
        severity: "critical",
        statusLabel: "thesis break",
        reasonSummary: "Новый риск разрушает исходный тезис.",
        actionSummary: "Открыть review и пересмотреть позицию.",
      },
    }),
    deepReview: async () => ({
      researchProbability: 41,
      nakedProbability: 47,
      confidence: "medium",
      researchStatus: "ON",
      thesisSummary: "После новой травмы тезис нужно пересобрать.",
      primaryDriver: "Травма ключевого игрока",
      invalidationSignal: "Подтверждение восстановления состава",
      nextReviewDueAt: "2026-04-08T12:00:00Z",
    }),
  });

  const update = db.query(`SELECT status_label, reason_summary, action_summary FROM watch_updates`).get() as any;
  expect(update.status_label).toBe("thesis break");
  expect(update.reason_summary).toContain("Новый риск");
  expect(update.action_summary).toContain("review");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
bun test src/services/watchlist_engine.test.ts --grep "thesis break"
```

Expected: FAIL because no deep-review/update path exists.

- [ ] **Step 3: Implement composite trigger evaluation**

```ts
function shouldTriggerDeepReview(input: {
  priceDelta: number | null;
  monitoringStatus: MonitoringStatus;
  hasDisconfirmingSignal: boolean;
  reviewDue: boolean;
  hasOpenPosition: boolean;
  thesisWeakened: boolean;
}): boolean {
  return Boolean(
    (input.priceDelta !== null && Math.abs(input.priceDelta) >= 10)
      || input.monitoringStatus === "review_required"
      || input.monitoringStatus === "thesis_break"
      || input.hasDisconfirmingSignal
      || input.reviewDue
      || (input.hasOpenPosition && input.thesisWeakened)
  );
}
```

- [ ] **Step 4: Persist deep-review output back into dossier and updates**

```ts
function applyDeepReview(
  db: Database,
  dossierId: number,
  runId: number,
  before: DossierRow,
  deepReview: DeepReviewResult,
): void {
  db.run(
    `
      UPDATE watch_dossiers
      SET updated_at = ?2,
          last_deep_review_at = ?2,
          next_review_due_at = ?3,
          thesis_state = ?4,
          current_market_price = ?5,
          current_research_probability = ?6,
          current_naked_probability = ?7,
          current_confidence = ?8,
          current_research_status = ?9,
          current_thesis_summary = ?10,
          current_primary_driver = ?11,
          current_invalidation_signal = ?12,
          last_status_label = ?13,
          last_reason_summary = ?14,
          last_action_summary = ?15
      WHERE id = ?1
    `,
    [
      dossierId,
      new Date().toISOString(),
      deepReview.nextReviewDueAt,
      deepReview.thesisState,
      deepReview.marketPrice,
      deepReview.researchProbability,
      deepReview.nakedProbability,
      deepReview.confidence,
      deepReview.researchStatus,
      deepReview.thesisSummary,
      deepReview.primaryDriver,
      deepReview.invalidationSignal,
      deepReview.operatorFacing.statusLabel,
      deepReview.operatorFacing.reasonSummary,
      deepReview.operatorFacing.actionSummary,
    ],
  );

  createWatchUpdate(db, dossierId, runId, {
    severity: deepReview.operatorFacing.severity,
    statusLabel: deepReview.operatorFacing.statusLabel,
    reasonSummary: deepReview.operatorFacing.reasonSummary,
    actionSummary: deepReview.operatorFacing.actionSummary,
    marketPriceBefore: before.current_market_price,
    marketPriceAfter: deepReview.marketPrice,
    researchProbabilityBefore: before.current_research_probability,
    researchProbabilityAfter: deepReview.researchProbability,
  });
}
```

- [ ] **Step 5: Wire dossier reviews when update requires operator action**

```ts
function ensureOperatorReview(db: Database, dossier: DossierRow, actionSummary: string, dueAt: string): void {
  const relatedOpenPosition = db.query(
    `SELECT id FROM positions WHERE market_id = ?1 AND status = 'open' ORDER BY opened_at DESC LIMIT 1`
  ).get(dossier.market_id) as { id: number } | null;

  if (!relatedOpenPosition) {
    return;
  }

  db.run(
    `
      INSERT INTO reviews (position_id, market_id, review_type, due_at, status, review_note, created_at, updated_at)
      VALUES (?1, ?2, 'watchlist_refresh', ?3, 'due', ?4, ?5, ?5)
    `,
    [relatedOpenPosition.id, dossier.market_id, dueAt, actionSummary, new Date().toISOString()],
  );
}
```

- [ ] **Step 6: Run test suite to verify it passes**

Run:

```bash
bun test src/services/watchlist_engine.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/watchlist_engine.ts src/services/watchlist_engine.test.ts src/services/research_runner.ts
git commit -m "feat: add deep review triggers for watchlist engine"
```

## Task 5: Schedule the Refresh Job and Telegram Delivery

**Files:**
- Modify: `scripts/entrypoint.sh`
- Modify: `supercronic.crontab`
- Modify: `python/telegram_bot.py`
- Modify: `python/watchlist_store.py`
- Modify: `python/test_watchlist_store.py`
- Modify: `python/test_telegram_operator.py`

- [ ] **Step 1: Write the failing delivery test**

```python
def test_send_pending_watch_updates_marks_telegram_delivery(self):
    with temp_db() as db_path:
        seed_watch_update(
            db_path,
            market_id="watch-alert",
            status_label="тезис ослаб",
            reason_summary="Рынок ушёл против нас на 11 п.п.",
            action_summary="Открыть review",
        )

        sent: list[str] = []

        with mock.patch("python.telegram_bot.send_message", side_effect=lambda text: sent.append(text)):
            telegram_bot.send_pending_watch_updates(db_path)

        assert len(sent) == 1
        assert "тезис ослаб" in sent[0]
        assert "Открыть review" in sent[0]
        with sqlite3.connect(db_path) as conn:
            delivered = conn.execute(
                "SELECT delivered_to_telegram_at FROM watch_updates WHERE id = 1"
            ).fetchone()[0]
        assert delivered is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest python.test_watchlist_store.WatchlistStoreTests.test_send_pending_watch_updates_marks_telegram_delivery -v
```

Expected: FAIL because pending update delivery is not implemented.

- [ ] **Step 3: Add runtime actions for refresh and alert delivery**

```bash
watchlist-refresh)
  cd /app
  bun run src/watchlist_refresh.ts 2>&1 | tee -a "$LOG_DIR/watchlist_refresh_${TODAY}.log"
  ;;

watch-alerts)
  cd /app
  /app/python/venv/bin/python python/telegram_bot.py --watch-updates 2>&1 | tee -a "$LOG_DIR/watchlist_alerts_${TODAY}.log"
  ;;
```

- [ ] **Step 4: Schedule the autonomous cycle**

```cron
30 */6 * * * /app/scripts/entrypoint.sh watchlist-refresh
40 */6 * * * /app/scripts/entrypoint.sh watch-alerts
```

- [ ] **Step 5: Add delivery helpers in Python**

```python
def list_pending_watch_updates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_watchlist_engine_schema(conn)
    rows = conn.execute(
        """
        SELECT wu.id, wd.market_id, wu.status_label, wu.reason_summary, wu.action_summary
        FROM watch_updates wu
        JOIN watch_dossiers wd ON wd.id = wu.watch_dossier_id
        WHERE wu.delivered_to_telegram_at IS NULL
        ORDER BY wu.created_at ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def mark_watch_update_delivered(conn: sqlite3.Connection, update_id: int, channel: str) -> None:
    column = "delivered_to_telegram_at" if channel == "telegram" else "delivered_to_dashboard_at"
    conn.execute(f"UPDATE watch_updates SET {column} = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), update_id))


def send_pending_watch_updates(db_path: str = DB_PATH) -> int:
    sent = 0
    with sqlite3.connect(db_path) as conn:
      conn.row_factory = sqlite3.Row
      for update in list_pending_watch_updates(conn):
          send_message(
              "\n".join(
                  [
                      f"📌 {update['market_id']}",
                      f"Статус: {update['status_label']}",
                      f"Почему: {update['reason_summary']}",
                      f"Действие: {update['action_summary']}",
                  ]
              )
          )
          mark_watch_update_delivered(conn, int(update["id"]), "telegram")
          sent += 1
      conn.commit()
    return sent
```

- [ ] **Step 6: Expose the new CLI path in `telegram_bot.py`**

```python
parser.add_argument("--watch-updates", action="store_true")

if args.watch_updates:
    delivered = send_pending_watch_updates(DB_PATH)
    print(f"Delivered watch updates: {delivered}")
```

- [ ] **Step 7: Run tests to verify they pass**

Run:

```bash
python3 -m unittest python.test_watchlist_store python.test_telegram_operator -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add scripts/entrypoint.sh supercronic.crontab python/telegram_bot.py python/watchlist_store.py python/test_watchlist_store.py python/test_telegram_operator.py
git commit -m "feat: schedule autonomous watchlist refresh and alerts"
```

## Task 6: Add Python Read Models and Dashboard APIs

**Files:**
- Modify: `python/watchlist_store.py`
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `python/dashboard_api.py`
- Modify: `python/test_dashboard_api.py`
- Modify: `python/test_operator_core.py`

- [ ] **Step 1: Write the failing dashboard API test**

```python
def test_watchlist_dossier_endpoints_return_operator_updates(self):
    with temp_db() as db_path:
        seed_watch_dossier(db_path, market_id="watch-api")
        seed_watch_update(
            db_path,
            market_id="watch-api",
            status_label="нужен review",
            reason_summary="Рынок ушёл против тезиса на 9 п.п.",
            action_summary="Открыть разбор и проверить позицию.",
        )

        app = create_test_app(db_path=db_path)
        client = TestClient(app)

        dossiers = client.get("/api/watchlist/dossiers")
        assert dossiers.status_code == 200
        assert dossiers.json()["items"][0]["market_id"] == "watch-api"

        updates = client.get("/api/watchlist/updates", params={"market_id": "watch-api"})
        assert updates.status_code == 200
        assert updates.json()["items"][0]["status_label"] == "нужен review"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest python.test_dashboard_api.DashboardApiTests.test_watchlist_dossier_endpoints_return_operator_updates -v
```

Expected: FAIL because endpoints/models do not exist.

- [ ] **Step 3: Add dossier/update read models**

```python
def list_watch_dossiers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_watchlist_engine_schema(conn)
    rows = conn.execute(
        """
        SELECT market_id, status, thesis_state, current_market_price,
               current_research_probability, current_confidence,
               current_research_status, current_thesis_summary,
               current_primary_driver, current_invalidation_signal,
               next_review_due_at, last_refresh_at, last_status_label,
               last_reason_summary, last_action_summary
        FROM watch_dossiers
        WHERE status = 'active'
        ORDER BY
          CASE thesis_state
            WHEN 'broken' THEN 1
            WHEN 'review_required' THEN 2
            WHEN 'weakened' THEN 3
            ELSE 4
          END,
          updated_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_watch_dossier(conn: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    ensure_watchlist_engine_schema(conn)
    row = conn.execute(
        "SELECT * FROM watch_dossiers WHERE market_id = ?",
        (market_id,),
    ).fetchone()
    return dict(row) if row else None


def list_watch_updates(conn: sqlite3.Connection, market_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    ensure_watchlist_engine_schema(conn)
    where = "WHERE wd.market_id = ?" if market_id else ""
    params = (market_id, limit) if market_id else (limit,)
    rows = conn.execute(
        f"""
        SELECT wu.id, wd.market_id, wu.created_at, wu.severity, wu.status_label,
               wu.reason_summary, wu.action_summary
        FROM watch_updates wu
        JOIN watch_dossiers wd ON wd.id = wu.watch_dossier_id
        {where}
        ORDER BY wu.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 4: Expose dossier/update APIs and enrich market workspace**

```python
@app.get("/api/watchlist/dossiers", response_model=WatchDossierListResponse)
def watchlist_dossiers(request: Request) -> WatchDossierListResponse:
    require_auth(request)
    return WatchDossierListResponse(items=core.list_watch_dossiers())


@app.get("/api/watchlist/updates", response_model=WatchUpdateListResponse)
def watchlist_updates(request: Request, market_id: str | None = None, limit: int = 20) -> WatchUpdateListResponse:
    require_auth(request)
    return WatchUpdateListResponse(items=core.list_watch_updates(market_id=market_id, limit=limit))


workspace = load_market_workspace(conn, market_id)
dossier = get_watch_dossier(conn, market_id)
updates = list_watch_updates(conn, market_id=market_id, limit=10)
return {
    **workspace,
    "watch_dossier": dossier,
    "watch_updates": updates,
}
```

- [ ] **Step 5: Add response models**

```python
class WatchDossierResponse(BaseModel):
    market_id: str
    status: str
    thesis_state: str
    current_market_price: float | None = None
    current_research_probability: float | None = None
    current_confidence: str | None = None
    current_research_status: str | None = None
    current_thesis_summary: str | None = None
    current_primary_driver: str | None = None
    current_invalidation_signal: str | None = None
    next_review_due_at: str | None = None
    last_refresh_at: str | None = None
    last_status_label: str | None = None
    last_reason_summary: str | None = None
    last_action_summary: str | None = None


class WatchUpdateResponse(BaseModel):
    id: int
    market_id: str
    created_at: str
    severity: str
    status_label: str
    reason_summary: str
    action_summary: str
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
python3 -m unittest python.test_dashboard_api python.test_operator_core -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add python/watchlist_store.py python/operator_core.py python/dashboard_models.py python/dashboard_api.py python/test_dashboard_api.py python/test_operator_core.py
git commit -m "feat: expose watchlist engine data through dashboard api"
```

## Task 7: Redesign `Избранное` as a Dossier Board and Simplify Market Detail

**Files:**
- Create: `dashboard/src/components/WatchDossierCard.tsx`
- Create: `dashboard/src/components/WatchUpdateFeed.tsx`
- Modify: `dashboard/src/pages/WatchlistPage.tsx`
- Modify: `dashboard/src/pages/MarketDetailPage.tsx`
- Modify: `dashboard/src/types/api.ts`
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/styles.css`
- Test: `dashboard/src/__tests__/WatchDossierCard.test.tsx`
- Test: `dashboard/src/__tests__/WatchUpdateFeed.test.tsx`
- Test: `dashboard/src/__tests__/WatchlistPage.test.tsx`
- Test: `dashboard/src/__tests__/MarketDetailPage.test.tsx`

- [ ] **Step 1: Write the failing `Избранное` board test**

```tsx
it("renders dossier cards with status reason action and review due", async () => {
  watchlistDossiers.mockResolvedValue({
    items: [
      {
        market_id: "watch-ui",
        thesis_state: "weakened",
        current_thesis_summary: "Тезис ослаб после движения цены.",
        last_status_label: "тезис ослаб",
        last_reason_summary: "Рынок ушёл против нас на 8 п.п.",
        last_action_summary: "Проверить рынок вечером.",
        next_review_due_at: "2026-04-08T12:00:00Z",
      },
    ],
  });
  watchlistUpdates.mockResolvedValue({ items: [] });

  renderWithRouter(<WatchlistPage />);

  expect(await screen.findByText("тезис ослаб")).toBeInTheDocument();
  expect(screen.getByText("Рынок ушёл против нас на 8 п.п.")).toBeInTheDocument();
  expect(screen.getByText("Проверить рынок вечером.")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd dashboard && npm test -- src/__tests__/WatchlistPage.test.tsx --runInBand
```

Expected: FAIL because dossier API/components do not exist.

- [ ] **Step 3: Add dossier/update client contracts**

```ts
export interface WatchDossier {
  market_id: string;
  status: string;
  thesis_state: string;
  current_market_price?: number | null;
  current_research_probability?: number | null;
  current_confidence?: string | null;
  current_research_status?: StatusLevel | null;
  current_thesis_summary?: string | null;
  current_primary_driver?: string | null;
  current_invalidation_signal?: string | null;
  next_review_due_at?: string | null;
  last_refresh_at?: string | null;
  last_status_label?: string | null;
  last_reason_summary?: string | null;
  last_action_summary?: string | null;
}

export interface WatchUpdate {
  id: number;
  market_id: string;
  created_at: string;
  severity: "info" | "review" | "warning" | "critical";
  status_label: string;
  reason_summary: string;
  action_summary: string;
}

watchlistDossiers: () => request<WatchDossierListResponse>("/api/watchlist/dossiers"),
watchlistUpdates: (marketId?: string) =>
  request<WatchUpdateListResponse>(marketId ? `/api/watchlist/updates?market_id=${marketId}` : "/api/watchlist/updates"),
```

- [ ] **Step 4: Render compact dossier cards instead of raw watchlist rows**

```tsx
export function WatchDossierCard({ dossier }: { dossier: WatchDossier }) {
  return (
    <article className="watch-dossier-card">
      <header className="watch-dossier-header">
        <h3>{dossier.market_id}</h3>
        <span className={`badge badge-${dossier.thesis_state}`}>{dossier.last_status_label ?? dossier.thesis_state}</span>
      </header>
      <p className="detail-lead">{dossier.current_thesis_summary ?? "Краткий тезис пока не записан."}</p>
      <ul className="detail-list">
        <li>Почему: {dossier.last_reason_summary ?? "Пока без новых изменений."}</li>
        <li>Что делать: {dossier.last_action_summary ?? "Наблюдать."}</li>
        <li>Review до: {formatTimestamp(dossier.next_review_due_at)}</li>
      </ul>
      <Link className="button button-secondary" to={`/markets/${dossier.market_id}`}>
        Открыть рынок
      </Link>
    </article>
  );
}
```

- [ ] **Step 5: Move market detail to dossier-first layout**

```tsx
{workspace.watch_dossier ? (
  <section className="panel">
    <SectionHeader
      title="Текущее сопровождение"
      subtitle="Сначала статус тезиса, причина изменения и действие. Полные факты — только вторым слоем."
    />
    <p className="detail-lead">{workspace.watch_dossier.current_thesis_summary}</p>
    <ul className="detail-list">
      <li>Статус: {workspace.watch_dossier.last_status_label ?? workspace.watch_dossier.thesis_state}</li>
      <li>Почему: {workspace.watch_dossier.last_reason_summary ?? "Нет новых изменений."}</li>
      <li>Что делать: {workspace.watch_dossier.last_action_summary ?? "Наблюдать."}</li>
      <li>Review до: {formatTimestamp(workspace.watch_dossier.next_review_due_at)}</li>
    </ul>
  </section>
) : null}

<WatchUpdateFeed items={workspace.watch_updates ?? []} />

<details className="secondary-detail">
  <summary>Почему Pythia так думает</summary>
  {/* existing transparency + raw research blocks */}
</details>
```

- [ ] **Step 6: Add focused CSS**

```css
.watch-dossier-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.watch-dossier-card,
.watch-update-feed article {
  border: 1px solid var(--line-soft);
  border-radius: 18px;
  padding: 18px;
  background: rgba(255, 255, 255, 0.72);
}

.watch-update-feed {
  display: grid;
  gap: 12px;
}

.secondary-detail summary {
  cursor: pointer;
  font-weight: 700;
}
```

- [ ] **Step 7: Run frontend tests and build**

Run:

```bash
cd dashboard && npm test -- src/__tests__/WatchDossierCard.test.tsx src/__tests__/WatchUpdateFeed.test.tsx src/__tests__/WatchlistPage.test.tsx src/__tests__/MarketDetailPage.test.tsx --runInBand
cd dashboard && npm run build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/components/WatchDossierCard.tsx dashboard/src/components/WatchUpdateFeed.tsx dashboard/src/pages/WatchlistPage.tsx dashboard/src/pages/MarketDetailPage.tsx dashboard/src/types/api.ts dashboard/src/api/client.ts dashboard/src/styles.css dashboard/src/__tests__/WatchDossierCard.test.tsx dashboard/src/__tests__/WatchUpdateFeed.test.tsx dashboard/src/__tests__/WatchlistPage.test.tsx dashboard/src/__tests__/MarketDetailPage.test.tsx
git commit -m "feat: redesign watchlist around autonomous dossiers"
```

## Task 8: Update Docs, Verify End-to-End, and Roll Out

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/AUDIT.md`

- [ ] **Step 1: Document the new runtime commands**

```md
### Автономное сопровождение watchlist

- `bash scripts/entrypoint.sh watchlist-refresh` — один цикл refresh engine
- `bash scripts/entrypoint.sh watch-alerts` — отправка недоставленных watch-updates в Telegram

По умолчанию:
- refresh: каждые 6 часов
- alerts: через 10 минут после refresh
```

- [ ] **Step 2: Add architecture section for the new contour**

```md
## Watchlist Engine

`Autonomous Watchlist Engine` — отдельный runtime-контур между `forecast stack` и `dashboard`.

Он:
- создаёт и поддерживает `watch dossier`;
- пишет технические refresh snapshots;
- создаёт короткие operator-facing updates;
- не заменяет `decision core`, а дополняет его.
```

- [ ] **Step 3: Run the full verification matrix**

Run:

```bash
bun test src/services/db.test.ts src/services/watchlist_engine.test.ts src/services/decision_core.test.ts
python3 -m unittest python.test_watchlist_store python.test_operator_core python.test_dashboard_api python.test_resolution_checker -v
cd dashboard && npm test && npm run build
```

Expected:
- Bun tests PASS
- Python tests PASS
- React tests PASS
- Vite build PASS

- [ ] **Step 4: Dry-run the jobs locally against a copy of production data**

Run:

```bash
cp data/pythia.db /tmp/pythia-watchlist-smoke.db
DB_PATH=/tmp/pythia-watchlist-smoke.db bun run src/watchlist_refresh.ts
DB_PATH=/tmp/pythia-watchlist-smoke.db python3 python/telegram_bot.py --watch-updates
sqlite3 /tmp/pythia-watchlist-smoke.db "SELECT COUNT(*) FROM watch_refresh_runs; SELECT COUNT(*) FROM watch_refresh_snapshots; SELECT COUNT(*) FROM watch_updates;"
```

Expected:
- refresh run count >= 1
- snapshots count >= number of active dossiers
- updates count >= 0

- [ ] **Step 5: Deploy to `amper` and verify**

Run:

```bash
git push origin main
ssh amper 'cd /home/pythia/pythia && git pull && docker compose up -d --build'
ssh amper 'cd /home/pythia/pythia && docker compose ps'
ssh amper 'cd /home/pythia/pythia && docker compose exec -T pythia-scheduler bash /app/scripts/entrypoint.sh watchlist-refresh'
ssh amper 'cd /home/pythia/pythia && docker compose exec -T pythia-telegram python /app/python/telegram_bot.py --watch-updates'
```

Expected:
- containers healthy
- refresh job completes
- Telegram alert delivers only concise `status / reason / action`

- [ ] **Step 6: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md docs/AUDIT.md
git commit -m "docs: describe autonomous watchlist engine"
```

## Spec Coverage Check

- `watch dossier`, `initial thesis snapshot`, freshness-policy and automatic review deadline: Task 2
- separate `watchlist refresh` job every `4-6` hours: Task 5
- `monitoring mode` and cheap change detection: Task 3
- `deep review mode` only on composite triggers: Task 4
- two-layer persistence (`technical snapshot` + `operator-facing update`): Tasks 3-4
- dashboard + Telegram dual delivery: Tasks 5-7
- `Избранное` becomes operator board, raw facts move to second layer: Task 7

## Notes for Executors

- Не смешивайте `watchlist engine` в `decision_core.ts`. Decision core и watch dossier — разные ответственности.
- Не показывайте raw sources по умолчанию на `WatchlistPage`. Только `status / reason / action`.
- Не добавляйте real trading, Polymarket order placement или wallet handling в этом workstream.
- Если какой-либо API/provider недоступен, refresh run должен завершаться с частичным успехом и писать `error_summary`, а не падать целиком.
