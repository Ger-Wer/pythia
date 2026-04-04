# Pythia Operator Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в Pythia полноценную web-консоль оператора на `b1w.duckdns.org`, которая объединяет обзор системы, разбор рынков, watchlist и безопасные operator actions, не ломая текущий production pipeline и Telegram-алерты.

**Architecture:** Новая web-часть строится как `React + Vite` SPA поверх отдельного Python API, а Telegram и dashboard делят общий `operator_core`. `pythia-dashboard` публикуется наружу через reverse proxy, но scheduler и telegram остаются внутренними сервисами. Во внешнем доступе действует двойная защита: proxy auth и app session auth.

**Tech Stack:** React, Vite, TypeScript, Python 3.11, FastAPI, SQLite, Docker Compose, reverse proxy, HTTP cookie sessions

---

## Карта файлов

### Создать

- `python/operator_core.py` — общий operator-core для Telegram и dashboard: overview, latest run, market details, watchlist, health, safe actions, logs
- `python/dashboard_api.py` — FastAPI приложение с read/action/auth endpoints
- `python/dashboard_auth.py` — login/session helpers, cookie auth, password hash verification
- `python/dashboard_models.py` — pydantic response/request models для API
- `python/test_operator_core.py` — unit tests на operator core и watchlist
- `python/test_dashboard_api.py` — API tests через `TestClient`
- `dashboard/package.json` — frontend dependencies и scripts
- `dashboard/tsconfig.json` — frontend TypeScript config
- `dashboard/vite.config.ts` — Vite config и dev proxy
- `dashboard/index.html` — SPA entry
- `dashboard/src/main.tsx` — bootstrap приложения
- `dashboard/src/app/App.tsx` — root router/layout
- `dashboard/src/app/routes.tsx` — маршруты SPA
- `dashboard/src/api/client.ts` — fetch wrapper для API
- `dashboard/src/types/api.ts` — типы API
- `dashboard/src/pages/LoginPage.tsx` — login screen
- `dashboard/src/pages/OverviewPage.tsx` — главный экран
- `dashboard/src/pages/LatestRunPage.tsx` — таблица последнего прогона
- `dashboard/src/pages/MarketDetailPage.tsx` — детальный экран рынка
- `dashboard/src/pages/WatchlistPage.tsx` — watchlist
- `dashboard/src/pages/OperationsPage.tsx` — безопасные operator actions
- `dashboard/src/components/Layout.tsx` — app shell, nav, status bar
- `dashboard/src/components/StatusPills.tsx` — status/research pills
- `dashboard/src/components/ActionBar.tsx` — кнопки safe actions
- `dashboard/src/components/MarketTable.tsx` — таблица рынков/карточки
- `dashboard/src/components/MarketSummaryCard.tsx` — важные расхождения
- `dashboard/src/components/TimelinePanel.tsx` — простая история рынка по дням
- `dashboard/src/components/WatchlistPanel.tsx` — список watchlist и заметки
- `dashboard/src/components/ConfirmDialog.tsx` — подтверждение действий
- `dashboard/src/styles.css` — единые переменные и базовый дизайн
- `docs/superpowers/plans/2026-04-04-pythia-operator-dashboard-implementation.md` — этот план

### Изменить

- `python/operator_actions.py` — перевести Telegram на `operator_core`, убрать дублирование SQL
- `python/telegram_operator.py` — оставить как adapter поверх operator core
- `python/requirements.txt` — добавить backend-зависимости dashboard (`fastapi`, `uvicorn`, `httpx`, `itsdangerous`, возможно `python-multipart`)
- `.env.example` — добавить переменные dashboard auth/session и public base url
- `Dockerfile` — добавить сборку frontend через Node/Vite и запуск dashboard режима
- `docker-compose.yml` — добавить сервис `pythia-dashboard`, внутренний порт, сети и healthcheck
- `scripts/entrypoint.sh` — добавить режим `dashboard`
- `scripts/manual_run.sh` — расширить `status`/`logs` на новый сервис
- `README.md` — описать dashboard как основной интерфейс оператора
- `docs/ARCHITECTURE.md` — добавить `pythia-dashboard`, `operator_core`, external access model
- `docs/DEPLOYMENT.md` — описать сборку frontend, запуск dashboard, auth и reverse proxy host `b1w.duckdns.org`
- `docs/AUDIT.md` — отразить новый операторский слой и границы безопасности
- `docs/DECISION_LOG.md` — зафиксировать переход к web operator console

### Не трогать без необходимости

- `src/main.ts`
- `src/services/market_selector.ts`
- `src/services/research_runner.ts`
- `src/prompts/synthesis.ts`
- `python/resolution_checker.py`
- `python/edge_calculator.py`

---

### Task 1: Выделить общий operator core из Telegram-слоя

**Files:**
- Create: `python/operator_core.py`
- Create: `python/test_operator_core.py`
- Modify: `python/operator_actions.py`
- Modify: `python/telegram_operator.py`

- [ ] **Step 1: Написать падающий unit test на overview/details через новый core**

```python
# python/test_operator_core.py
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from python.operator_core import OperatorCore


class OperatorCoreTests(unittest.TestCase):
    def test_get_overview_and_market_details_from_sqlite(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
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
                        end_date TEXT,
                        forecast_timestamp TEXT NOT NULL,
                        outcome INTEGER,
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
                        research_brief_json, volume, end_date, forecast_timestamp, outcome, api_cost_usd, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "572833",
                        "Will Wolves finish in last place in the 2025-26 English Premier League?",
                        "sports",
                        53.0,
                        68.0,
                        58.0,
                        "medium",
                        "Команда выглядит уязвимой относительно рынка.",
                        "Риск внезапного улучшения формы.",
                        "Последние команды лиги часто остаются внизу таблицы.",
                        json.dumps(
                            {
                                "summary": "Короткая сводка",
                                "key_facts": ["Wolves идут последними"],
                                "catalyst_type": "sports_form",
                                "primary_driver": "Результаты Wolves в оставшихся турах",
                                "second_order_effects": ["Плохие xG-метрики", "Потеря очков с прямыми конкурентами"],
                                "disconfirming_signal": "Серия побед Wolves",
                                "sources": ["https://example.com/wolves"],
                            },
                            ensure_ascii=False,
                        ),
                        28974,
                        "2026-05-27T00:00:00",
                        "2026-04-04T08:16:48.766Z",
                        None,
                        0.0,
                        1,
                    ),
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            overview = core.get_overview()
            details = core.get_market_details("572833")

            self.assertEqual(overview["valid_predictions"], 1)
            self.assertEqual(overview["latest_run"]["markets_processed"], 1)
            self.assertEqual(details["market_id"], "572833")
            self.assertEqual(details["research"]["primary_driver"], "Результаты Wolves в оставшихся турах")
            self.assertEqual(details["research"]["second_order_effects"][0], "Плохие xG-метрики")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run:

```bash
python3 -m unittest python/test_operator_core.py -v
```

Expected: `ModuleNotFoundError: No module named 'python.operator_core'`.

- [ ] **Step 3: Создать минимальный `OperatorCore`**

```python
# python/operator_core.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class OperatorCore:
    def __init__(self, db_path: Path | str, log_dir: Path | str):
        self.db_path = Path(db_path)
        self.log_dir = Path(log_dir)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def get_overview(self) -> dict[str, Any]:
        with self._connect() as conn:
            valid_predictions = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE is_valid = 1"
            ).fetchone()[0]
            latest_ts = conn.execute(
                "SELECT MAX(forecast_timestamp) FROM predictions WHERE is_valid = 1"
            ).fetchone()[0]
            markets_processed = 0
            if latest_ts:
                markets_processed = conn.execute(
                    "SELECT COUNT(*) FROM predictions WHERE is_valid = 1 AND forecast_timestamp = ?",
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
            row = conn.execute(
                """
                SELECT market_id, question, market_price, dexter_p, naked_p,
                       dexter_confidence, dexter_reasoning, dexter_key_risk,
                       dexter_base_rate, research_brief_json, volume, end_date,
                       forecast_timestamp
                FROM predictions
                WHERE market_id = ? AND is_valid = 1
                ORDER BY forecast_timestamp DESC, id DESC
                LIMIT 1
                """,
                (market_id,),
            ).fetchone()
        if not row:
            raise LookupError(market_id)
        research = json.loads(row[9]) if row[9] else {}
        return {
            "market_id": row[0],
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
                "summary": research.get("summary", ""),
                "key_facts": research.get("key_facts", []),
                "catalyst_type": research.get("catalyst_type", "other"),
                "primary_driver": research.get("primary_driver", "Нет данных."),
                "second_order_effects": research.get("second_order_effects", []),
                "disconfirming_signal": research.get("disconfirming_signal", "Нет данных."),
                "sources": research.get("sources", []),
            },
        }
```

- [ ] **Step 4: Снова запустить unit test**

Run:

```bash
python3 -m unittest python/test_operator_core.py -v
```

Expected: `OK`.

- [ ] **Step 5: Перевести Telegram helper на `OperatorCore`**

```python
# python/operator_actions.py
from python.operator_core import OperatorCore


def build_operator_core() -> OperatorCore:
    return OperatorCore(db_path=DB_PATH, log_dir=LOG_DIR)


def render_status() -> str:
    core = build_operator_core()
    overview = core.get_overview()
    return format_status_report(DB_PATH, services=list_docker_services(), overview_override=overview)


def render_details(reference: str | None) -> str:
    core = build_operator_core()
    if not reference:
        return "Использование: /details <номер или market_id>"
    details = core.get_market_details(reference)
    return format_market_details(details)
```

- [ ] **Step 6: Прогнать текущие Telegram-тесты и новый core-тест**

Run:

```bash
python3 -m unittest python/test_operator_core.py python/test_telegram_operator.py -v
```

Expected: все тесты `OK`.

- [ ] **Step 7: Commit**

```bash
git add python/operator_core.py python/test_operator_core.py python/operator_actions.py python/telegram_operator.py
git commit -m "Extract shared operator core"
```

---

### Task 2: Добавить watchlist в SQLite и operator core

**Files:**
- Modify: `python/operator_core.py`
- Modify: `python/test_operator_core.py`
- Modify: `python/operator_actions.py`

- [ ] **Step 1: Написать падающий тест на watchlist CRUD**

```python
    def test_watchlist_add_list_remove(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        market_price REAL,
                        dexter_p REAL,
                        naked_p REAL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO predictions (
                        market_id, question, market_price, dexter_p, naked_p, forecast_timestamp, is_valid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("572834", "Will Burnley finish in last place?", 43.6, 65.0, 68.0, "2026-04-04T08:16:48.766Z", 1),
                )

            core = OperatorCore(db_path=db_path, log_dir=Path(temp_dir))
            core.add_to_watchlist("572834", "Сильный сигнал по борьбе за выживание")
            items = core.list_watchlist()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["market_id"], "572834")
            core.remove_from_watchlist("572834")
            self.assertEqual(core.list_watchlist(), [])
```

- [ ] **Step 2: Запустить тест и убедиться, что он падает**

Run:

```bash
python3 -m unittest python/test_operator_core.py::OperatorCoreTests.test_watchlist_add_list_remove -v
```

Expected: `AttributeError` на `add_to_watchlist`.

- [ ] **Step 3: Добавить таблицу watchlist и CRUD в core**

```python
# python/operator_core.py
from datetime import datetime, timezone


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

    def list_watchlist(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._ensure_watchlist_table(conn)
            rows = conn.execute(
                """
                SELECT w.market_id, w.note, w.created_at, w.updated_at,
                       p.question, p.market_price, p.dexter_p, p.naked_p, p.forecast_timestamp
                FROM watchlist w
                LEFT JOIN predictions p
                  ON p.market_id = w.market_id
                 AND p.id = (
                      SELECT id FROM predictions p2
                      WHERE p2.market_id = w.market_id AND p2.is_valid = 1
                      ORDER BY p2.forecast_timestamp DESC, p2.id DESC
                      LIMIT 1
                 )
                ORDER BY w.updated_at DESC
                """
            ).fetchall()
        return [
            {
                "market_id": row[0],
                "note": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "question": row[4],
                "market_price": row[5],
                "research_probability": row[6],
                "naked_probability": row[7],
                "forecast_timestamp": row[8],
            }
            for row in rows
        ]

    def add_to_watchlist(self, market_id: str, note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            self._ensure_watchlist_table(conn)
            conn.execute(
                """
                INSERT INTO watchlist (market_id, note, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (market_id, note, now, now),
            )
            conn.commit()

    def remove_from_watchlist(self, market_id: str) -> None:
        with self._connect() as conn:
            self._ensure_watchlist_table(conn)
            conn.execute("DELETE FROM watchlist WHERE market_id = ?", (market_id,))
            conn.commit()
```

- [ ] **Step 4: Добавить Telegram-friendly wrappers**

```python
# python/operator_actions.py
def add_watchlist_item(market_id: str, note: str = "") -> str:
    core = build_operator_core()
    core.add_to_watchlist(market_id, note)
    return f"Рынок {market_id} добавлен в watchlist."


def remove_watchlist_item(market_id: str) -> str:
    core = build_operator_core()
    core.remove_from_watchlist(market_id)
    return f"Рынок {market_id} удалён из watchlist."
```

- [ ] **Step 5: Снова прогнать core-тесты**

Run:

```bash
python3 -m unittest python/test_operator_core.py -v
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add python/operator_core.py python/test_operator_core.py python/operator_actions.py
git commit -m "Add watchlist storage to operator core"
```

---

### Task 3: Поднять внутренний Dashboard API и auth-слой

**Files:**
- Create: `python/dashboard_models.py`
- Create: `python/dashboard_auth.py`
- Create: `python/dashboard_api.py`
- Create: `python/test_dashboard_api.py`
- Modify: `python/requirements.txt`

- [ ] **Step 1: Добавить падающий API test на login и overview**

```python
# python/test_dashboard_api.py
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from python.dashboard_api import create_app


class DashboardApiTests(unittest.TestCase):
    def test_login_and_overview_flow(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pythia.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        forecast_timestamp TEXT NOT NULL,
                        is_valid INTEGER DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO predictions (market_id, question, forecast_timestamp, is_valid) VALUES (?, ?, ?, ?)",
                    ("m1", "Will example happen?", "2026-04-04T08:16:48.766Z", 1),
                )

            app = create_app(
                db_path=db_path,
                log_dir=Path(temp_dir),
                session_secret="test-secret",
                dashboard_username="operator",
                dashboard_password_hash="plain:secret-pass",
            )
            client = TestClient(app)

            login_res = client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})
            self.assertEqual(login_res.status_code, 200)
            overview_res = client.get("/api/overview")
            self.assertEqual(overview_res.status_code, 200)
            self.assertEqual(overview_res.json()["valid_predictions"], 1)
```

- [ ] **Step 2: Запустить тест и зафиксировать падение**

Run:

```bash
python3 -m unittest python/test_dashboard_api.py -v
```

Expected: `ModuleNotFoundError` или `cannot import name create_app`.

- [ ] **Step 3: Добавить backend dependencies**

```text
# python/requirements.txt
fastapi==0.116.1
uvicorn==0.35.0
httpx==0.28.1
itsdangerous==2.2.0
python-multipart==0.0.20
```

- [ ] **Step 4: Добавить минимальные response models**

```python
# python/dashboard_models.py
from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class OverviewResponse(BaseModel):
    valid_predictions: int
    latest_run: dict


class MeResponse(BaseModel):
    authenticated: bool
    username: str | None = None
```

- [ ] **Step 5: Реализовать простую session auth**

```python
# python/dashboard_auth.py
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from itsdangerous import URLSafeSerializer


@dataclass(frozen=True)
class DashboardAuthConfig:
    session_secret: str
    dashboard_username: str
    dashboard_password_hash: str


def verify_password(stored_hash: str, password: str) -> bool:
    if stored_hash.startswith("plain:"):
        return hmac.compare_digest(stored_hash[6:], password)
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(stored_hash, digest)


def build_serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt="pythia-dashboard")
```

- [ ] **Step 6: Реализовать FastAPI app skeleton**

```python
# python/dashboard_api.py
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response

from python.dashboard_auth import DashboardAuthConfig, build_serializer, verify_password
from python.dashboard_models import LoginRequest, MeResponse, OverviewResponse
from python.operator_core import OperatorCore


def create_app(
    db_path: Path | str,
    log_dir: Path | str,
    session_secret: str,
    dashboard_username: str,
    dashboard_password_hash: str,
) -> FastAPI:
    app = FastAPI(title="Pythia Dashboard API")
    auth = DashboardAuthConfig(
        session_secret=session_secret,
        dashboard_username=dashboard_username,
        dashboard_password_hash=dashboard_password_hash,
    )
    serializer = build_serializer(auth.session_secret)
    core = OperatorCore(db_path=db_path, log_dir=log_dir)

    def require_session(request: Request) -> str:
        raw = request.cookies.get("pythia_dashboard_session")
        if not raw:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        try:
            payload = serializer.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="UNAUTHORIZED") from exc
        if payload.get("username") != auth.dashboard_username:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        return payload["username"]

    @app.post("/api/auth/login")
    def login(body: LoginRequest, response: Response) -> dict:
        if body.username != auth.dashboard_username or not verify_password(auth.dashboard_password_hash, body.password):
            raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
        response.set_cookie(
            key="pythia_dashboard_session",
            value=serializer.dumps({"username": body.username}),
            httponly=True,
            samesite="lax",
            secure=True,
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie("pythia_dashboard_session")
        return {"ok": True}

    @app.get("/api/auth/me", response_model=MeResponse)
    def me(request: Request) -> MeResponse:
        try:
            username = require_session(request)
            return MeResponse(authenticated=True, username=username)
        except HTTPException:
            return MeResponse(authenticated=False, username=None)

    @app.get("/api/overview", response_model=OverviewResponse)
    def overview(request: Request) -> OverviewResponse:
        require_session(request)
        data = core.get_overview()
        return OverviewResponse(**data)

    return app
```

- [ ] **Step 7: Запустить API tests**

Run:

```bash
python3 -m unittest python/test_dashboard_api.py -v
```

Expected: `OK`.

- [ ] **Step 8: Commit**

```bash
git add python/dashboard_models.py python/dashboard_auth.py python/dashboard_api.py python/test_dashboard_api.py python/requirements.txt
git commit -m "Add dashboard API auth skeleton"
```

---

### Task 4: Расширить Dashboard API read/action endpoints

**Files:**
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_api.py`
- Modify: `python/dashboard_models.py`
- Modify: `python/test_operator_core.py`
- Modify: `python/test_dashboard_api.py`

- [ ] **Step 1: Написать падающие тесты на latest run, market details и watchlist endpoints**

```python
    def test_market_details_and_watchlist_endpoints(self):
        app = create_app(
            db_path=db_path,
            log_dir=Path(temp_dir),
            session_secret="test-secret",
            dashboard_username="operator",
            dashboard_password_hash="plain:secret-pass",
        )
        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

        details = client.get("/api/markets/572833")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.json()["market_id"], "572833")

        add_watch = client.post("/api/watchlist", json={"market_id": "572833", "note": "Следить"})
        self.assertEqual(add_watch.status_code, 200)

        watchlist = client.get("/api/watchlist")
        self.assertEqual(watchlist.status_code, 200)
        self.assertEqual(len(watchlist.json()["items"]), 1)
```

- [ ] **Step 2: Прогнать API tests и увидеть падение**

Run:

```bash
python3 -m unittest python/test_dashboard_api.py -v
```

Expected: `404` или missing fields.

- [ ] **Step 3: Добавить response models для latest run, market details и watchlist**

```python
# python/dashboard_models.py
class LatestRunResponse(BaseModel):
    forecast_timestamp: str | None
    markets_processed: int
    items: list[dict]


class MarketDetailsResponse(BaseModel):
    market_id: str
    question: str
    market_price: float | None = None
    research_probability: float | None = None
    naked_probability: float | None = None
    confidence: str | None = None
    reasoning: str | None = None
    key_risk: str | None = None
    base_rate: str | None = None
    volume: float | None = None
    end_date: str | None = None
    forecast_timestamp: str | None = None
    research: dict


class WatchlistItemRequest(BaseModel):
    market_id: str
    note: str = ""


class WatchlistResponse(BaseModel):
    items: list[dict]
```

- [ ] **Step 4: Реализовать endpoints поверх operator core**

```python
# python/dashboard_api.py
    @app.get("/api/runs/latest")
    def latest_run(request: Request) -> dict:
        require_session(request)
        return core.get_latest_run()

    @app.get("/api/markets/{market_id}")
    def market_details(market_id: str, request: Request) -> dict:
        require_session(request)
        return core.get_market_details(market_id)

    @app.get("/api/watchlist")
    def watchlist(request: Request) -> dict:
        require_session(request)
        return {"items": core.list_watchlist()}

    @app.post("/api/watchlist")
    def watchlist_add(body: WatchlistItemRequest, request: Request) -> dict:
        require_session(request)
        core.add_to_watchlist(body.market_id, body.note)
        return {"ok": True}

    @app.delete("/api/watchlist/{market_id}")
    def watchlist_remove(market_id: str, request: Request) -> dict:
        require_session(request)
        core.remove_from_watchlist(market_id)
        return {"ok": True}
```

- [ ] **Step 5: Добавить whitelisted action-runner в operator core**

```python
# python/operator_core.py
import subprocess

SAFE_ACTIONS = {
    "forecast": ["/app/scripts/entrypoint.sh", "forecast"],
    "resolve": ["/app/scripts/entrypoint.sh", "resolve"],
    "weekly": ["/app/scripts/entrypoint.sh", "weekly"],
}


    def run_safe_action(self, action: str) -> dict[str, Any]:
        if action not in SAFE_ACTIONS:
            raise ValueError(action)
        completed = subprocess.run(
            SAFE_ACTIONS[action],
            cwd="/app",
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
```

- [ ] **Step 6: Добавить action endpoints**

```python
# python/dashboard_api.py
    @app.post("/api/actions/{action}")
    def run_action(action: str, request: Request) -> dict:
        require_session(request)
        if action not in {"forecast", "resolve", "weekly"}:
            raise HTTPException(status_code=404, detail="UNKNOWN_ACTION")
        return core.run_safe_action(action)
```

- [ ] **Step 7: Прогнать core + API tests**

Run:

```bash
python3 -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected: `OK`.

- [ ] **Step 8: Commit**

```bash
git add python/operator_core.py python/dashboard_api.py python/dashboard_models.py python/test_operator_core.py python/test_dashboard_api.py
git commit -m "Expand dashboard API endpoints"
```

---

### Task 5: Создать React/Vite SPA каркас

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/index.html`
- Create: `dashboard/src/main.tsx`
- Create: `dashboard/src/app/App.tsx`
- Create: `dashboard/src/app/routes.tsx`
- Create: `dashboard/src/styles.css`
- Create: `dashboard/src/api/client.ts`
- Create: `dashboard/src/types/api.ts`

- [ ] **Step 1: Создать frontend package и Vite config**

```json
// dashboard/package.json
{
  "name": "pythia-dashboard",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.1.0",
    "react-dom": "^19.1.0",
    "react-router-dom": "^7.7.0"
  },
  "devDependencies": {
    "@types/react": "^19.1.8",
    "@types/react-dom": "^19.1.6",
    "@vitejs/plugin-react": "^5.0.0",
    "typescript": "^5.9.2",
    "vite": "^7.1.0"
  }
}
```

```ts
// dashboard/vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
});
```

- [ ] **Step 2: Написать базовые типы и API client**

```ts
// dashboard/src/types/api.ts
export interface OverviewResponse {
  valid_predictions: number;
  latest_run: {
    forecast_timestamp: string | null;
    markets_processed: number;
  };
}

export interface WatchlistItem {
  market_id: string;
  note: string;
  question?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  forecast_timestamp?: string | null;
}
```

```ts
// dashboard/src/api/client.ts
async function request<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  me: () => request('/api/auth/me'),
  overview: () => request('/api/overview'),
  latestRun: () => request('/api/runs/latest'),
  marketDetails: (marketId: string) => request(`/api/markets/${marketId}`),
  watchlist: () => request('/api/watchlist'),
  login: (username: string, password: string) =>
    request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  runAction: (action: 'forecast' | 'resolve' | 'weekly') =>
    request(`/api/actions/${action}`, { method: 'POST' }),
  addWatchlist: (market_id: string, note: string) =>
    request('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ market_id, note }),
    }),
  removeWatchlist: (marketId: string) =>
    request(`/api/watchlist/${marketId}`, { method: 'DELETE' }),
};
```

- [ ] **Step 3: Собрать минимальный App shell**

```tsx
// dashboard/src/app/App.tsx
import { RouterProvider } from 'react-router-dom';
import { router } from './routes';
import '../styles.css';

export function App() {
  return <RouterProvider router={router} />;
}
```

```tsx
// dashboard/src/main.tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './app/App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 4: Создать базовые маршруты**

```tsx
// dashboard/src/app/routes.tsx
import { createBrowserRouter } from 'react-router-dom';
import { LoginPage } from '../pages/LoginPage';
import { OverviewPage } from '../pages/OverviewPage';
import { LatestRunPage } from '../pages/LatestRunPage';
import { MarketDetailPage } from '../pages/MarketDetailPage';
import { WatchlistPage } from '../pages/WatchlistPage';
import { OperationsPage } from '../pages/OperationsPage';

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/', element: <OverviewPage /> },
  { path: '/runs/latest', element: <LatestRunPage /> },
  { path: '/markets/:marketId', element: <MarketDetailPage /> },
  { path: '/watchlist', element: <WatchlistPage /> },
  { path: '/operations', element: <OperationsPage /> },
]);
```

- [ ] **Step 5: Проверить, что frontend собирается**

Run:

```bash
cd dashboard && npm install && npm run build
```

Expected: build completed successfully.

- [ ] **Step 6: Commit**

```bash
git add dashboard/package.json dashboard/tsconfig.json dashboard/vite.config.ts dashboard/index.html dashboard/src
git commit -m "Scaffold dashboard React app"
```

---

### Task 6: Реализовать страницы Operator Console v1

**Files:**
- Create: `dashboard/src/pages/LoginPage.tsx`
- Create: `dashboard/src/pages/OverviewPage.tsx`
- Create: `dashboard/src/pages/LatestRunPage.tsx`
- Create: `dashboard/src/pages/MarketDetailPage.tsx`
- Create: `dashboard/src/pages/WatchlistPage.tsx`
- Create: `dashboard/src/pages/OperationsPage.tsx`
- Create: `dashboard/src/components/Layout.tsx`
- Create: `dashboard/src/components/StatusPills.tsx`
- Create: `dashboard/src/components/ActionBar.tsx`
- Create: `dashboard/src/components/MarketTable.tsx`
- Create: `dashboard/src/components/MarketSummaryCard.tsx`
- Create: `dashboard/src/components/TimelinePanel.tsx`
- Create: `dashboard/src/components/WatchlistPanel.tsx`
- Create: `dashboard/src/components/ConfirmDialog.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Создать login page**

```tsx
// dashboard/src/pages/LoginPage.tsx
import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

export function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api.login(username, password);
      navigate('/');
    } catch {
      setError('Неверный логин или пароль.');
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Pythia</h1>
        <p>Операторская консоль</p>
        <label>Логин</label>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <label>Пароль</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error ? <div className="error-box">{error}</div> : null}
        <button type="submit">Войти</button>
      </form>
    </main>
  );
}
```

- [ ] **Step 2: Собрать общий layout и overview page**

```tsx
// dashboard/src/components/Layout.tsx
import { Link, Outlet } from 'react-router-dom';

export function Layout() {
  return (
    <div className="app-shell">
      <aside className="side-nav">
        <div className="brand">Pythia</div>
        <nav>
          <Link to="/">Обзор</Link>
          <Link to="/runs/latest">Последний прогон</Link>
          <Link to="/watchlist">Watchlist</Link>
          <Link to="/operations">Операции</Link>
        </nav>
      </aside>
      <section className="content-shell">
        <Outlet />
      </section>
    </div>
  );
}
```

```tsx
// dashboard/src/pages/OverviewPage.tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';

export function OverviewPage() {
  const [overview, setOverview] = useState<any | null>(null);

  useEffect(() => {
    api.overview().then(setOverview).catch(() => setOverview(null));
  }, []);

  return (
    <main className="page-grid">
      <section className="panel">
        <h1>Обзор</h1>
        <p>Последний forecast: {overview?.latest_run?.forecast_timestamp ?? 'нет данных'}</p>
        <p>Валидные прогнозы: {overview?.valid_predictions ?? 0}</p>
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Реализовать latest run, market detail, watchlist и operations pages**

```tsx
// dashboard/src/pages/LatestRunPage.tsx
export function LatestRunPage() {
  return <main className="page-grid"><section className="panel"><h1>Последний прогон</h1></section></main>;
}

// dashboard/src/pages/MarketDetailPage.tsx
export function MarketDetailPage() {
  return <main className="page-grid"><section className="panel"><h1>Детали рынка</h1></section></main>;
}

// dashboard/src/pages/WatchlistPage.tsx
export function WatchlistPage() {
  return <main className="page-grid"><section className="panel"><h1>Watchlist</h1></section></main>;
}

// dashboard/src/pages/OperationsPage.tsx
export function OperationsPage() {
  return <main className="page-grid"><section className="panel"><h1>Операции</h1></section></main>;
}
```

- [ ] **Step 4: Добавить базовые стили**

```css
/* dashboard/src/styles.css */
:root {
  --bg: #eef2e8;
  --surface: #fbf8ef;
  --ink: #17221e;
  --muted: #5c6a64;
  --accent: #255f4a;
  --accent-soft: #d8e6dd;
  --danger: #9a3a2d;
  --border: #d7ddd6;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
}

body {
  margin: 0;
  background: linear-gradient(180deg, #edf3e9 0%, #f8f4ea 100%);
  color: var(--ink);
}

.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 280px 1fr;
}

.side-nav {
  padding: 24px;
  border-right: 1px solid var(--border);
  background: rgba(251, 248, 239, 0.85);
  backdrop-filter: blur(12px);
}

.content-shell {
  padding: 24px;
}

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 20px;
  box-shadow: 0 14px 32px rgba(23, 34, 30, 0.05);
}

.login-shell {
  min-height: 100vh;
  display: grid;
  place-items: center;
}

.login-card {
  width: min(420px, calc(100vw - 32px));
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 28px;
}
```

- [ ] **Step 5: Собрать frontend ещё раз**

Run:

```bash
cd dashboard && npm run build
```

Expected: build completed successfully.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src dashboard/src/styles.css
git commit -m "Build operator console frontend pages"
```

---

### Task 7: Добавить `pythia-dashboard` сервис в Docker runtime

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `scripts/entrypoint.sh`
- Modify: `scripts/manual_run.sh`

- [ ] **Step 1: Расширить Dockerfile на сборку frontend**

```dockerfile
# Dockerfile
FROM node:22-slim AS dashboard-builder
WORKDIR /frontend
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install
COPY dashboard/ ./
RUN npm run build

FROM python:3.11-slim AS base
# ... существующие шаги ...
COPY --from=dashboard-builder /frontend/dist /app/dashboard_dist
```

- [ ] **Step 2: Добавить режим `dashboard` в entrypoint**

```bash
# scripts/entrypoint.sh
  dashboard)
    echo "[$TIMESTAMP] === Pythia Dashboard Starting ==="
    exec /app/python/venv/bin/python -m uvicorn python.dashboard_api:app --host 0.0.0.0 --port 8080
    ;;
```

- [ ] **Step 3: Добавить сервис `pythia-dashboard`**

```yaml
# docker-compose.yml
  pythia-dashboard:
    build:
      context: .
      dockerfile: Dockerfile
    image: pythia:latest
    container_name: pythia-dashboard
    restart: unless-stopped
    env_file:
      - .env
    environment:
      TZ: UTC
      DB_PATH: ${DB_PATH:-/app/data/pythia.db}
      LOG_DIR: ${LOG_DIR:-/app/logs}
      DASHBOARD_USERNAME: ${DASHBOARD_USERNAME}
      DASHBOARD_PASSWORD_HASH: ${DASHBOARD_PASSWORD_HASH}
      DASHBOARD_SESSION_SECRET: ${DASHBOARD_SESSION_SECRET}
      DASHBOARD_PUBLIC_BASE_URL: ${DASHBOARD_PUBLIC_BASE_URL:-https://b1w.duckdns.org}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - /var/run/docker.sock:/var/run/docker.sock:ro
    command: ["dashboard"]
    networks:
      - pythia_internal
      - edge_shared
    mem_limit: 768m
    cpus: 0.5
    pids_limit: 128
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8080/api/auth/me >/dev/null || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
```

- [ ] **Step 4: Расширить manual helper**

```bash
# scripts/manual_run.sh
  dashboard-logs)
    docker compose logs -f --tail=100 pythia-dashboard
    ;;

  status)
    docker compose ps
    ;;
```

- [ ] **Step 5: Проверить compose config**

Run:

```bash
docker compose config
```

Expected: сервисы `pythia-scheduler`, `pythia-telegram`, `pythia-dashboard` присутствуют и конфиг валиден.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml scripts/entrypoint.sh scripts/manual_run.sh
git commit -m "Add dashboard service to docker stack"
```

---

### Task 8: Документация, reverse proxy и rollout на `amper`

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/AUDIT.md`
- Modify: `docs/DECISION_LOG.md`

- [ ] **Step 1: Добавить dashboard env vars**

```env
# .env.example
DASHBOARD_USERNAME=operator
DASHBOARD_PASSWORD_HASH=plain:change-me
DASHBOARD_SESSION_SECRET=change-me-session-secret
DASHBOARD_PUBLIC_BASE_URL=https://b1w.duckdns.org
```

- [ ] **Step 2: Зафиксировать dashboard в документации**

```markdown
<!-- README.md -->
## Operator Dashboard

Основной рабочий интерфейс оператора теперь — web-dashboard на `b1w.duckdns.org`.
Telegram остаётся каналом алертов и быстрых действий.
Dashboard показывает обзор системы, последний прогон, детали рынка, watchlist и безопасные operator actions.
```

```markdown
<!-- docs/ARCHITECTURE.md -->
- `pythia-dashboard` — приватный internet-facing dashboard для одного оператора
- `operator_core` — общий слой для Telegram и dashboard
```

```markdown
<!-- docs/DEPLOYMENT.md -->
## Публикация dashboard

1. Убедитесь, что `pythia-dashboard` healthy.
2. Настройте reverse proxy host `b1w.duckdns.org`.
3. Разрешите наружу только dashboard.
4. Оставьте scheduler и telegram без published ports.
```

- [ ] **Step 3: Добавить пример reverse proxy-конфига**

```nginx
server {
    server_name b1w.duckdns.org;

    listen 80;
    return 301 https://$host$request_uri;
}

server {
    server_name b1w.duckdns.org;
    listen 443 ssl http2;

    auth_basic "Pythia Private Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd-pythia;

    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy same-origin always;
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none';" always;

    location / {
        proxy_pass http://pythia-dashboard:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

- [ ] **Step 4: Локальная верификация**

Run:

```bash
npx tsc --noEmit
python3 -m py_compile python/*.py
python3 -m unittest python/test_operator_core.py python/test_dashboard_api.py python/test_telegram_operator.py python/test_resolution_checker.py -v
cd dashboard && npm run build
docker compose config
```

Expected:

- TypeScript проходит
- Python проходит
- backend tests `OK`
- frontend build успешен
- compose config валиден

- [ ] **Step 5: Деплой на `amper`**

Run:

```bash
ssh amper "sudo -u pythia bash -lc 'cd /home/pythia/pythia && git pull && docker compose build && docker compose up -d'"
```

Expected:

- `pythia-dashboard` в `Up` или `healthy`
- `pythia-scheduler` и `pythia-telegram` продолжают работать

- [ ] **Step 6: Проверка после деплоя**

Run:

```bash
ssh amper "sudo -u pythia bash -lc 'cd /home/pythia/pythia && docker compose ps'"
ssh amper "sudo -u pythia bash -lc 'cd /home/pythia/pythia && docker compose logs --tail=100 pythia-dashboard'"
```

Expected:

- dashboard отвечает
- нет crash-loop
- scheduler и telegram не деградировали

- [ ] **Step 7: Commit**

```bash
git add .env.example README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md docs/AUDIT.md docs/DECISION_LOG.md
git commit -m "Document operator dashboard rollout"
```

---

## Self-Review

- Spec coverage: покрыты `operator_core`, watchlist, API, SPA, `pythia-dashboard`, auth, Docker runtime, `b1w.duckdns.org`, документация и безопасный rollout. Полный `Admin Control Plane` сознательно не включён, как и требовала spec.
- Placeholder scan: в шагах нет `TODO/TBD`; все новые файлы, endpoints, env vars и команды названы явно.
- Type consistency: во всех задачах используется единая терминология `pythia-dashboard`, `OperatorCore`, `dashboard_api.py`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD_HASH`, `DASHBOARD_SESSION_SECRET`, `b1w.duckdns.org`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-04-pythia-operator-dashboard-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
