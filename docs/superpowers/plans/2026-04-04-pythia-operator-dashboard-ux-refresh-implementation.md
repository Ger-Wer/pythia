# Pythia Operator Dashboard UX Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пересобрать первый релиз operator dashboard в человечески понятную консоль, где оператор понимает состояние системы, сильные сигналы и безопасные действия без Telegram-команд, `market_id` и чтения сырых логов.

**Architecture:** UX-refresh идёт поверх уже существующего `operator_core + FastAPI + React/Vite` каркаса. Сначала расширяется backend-контракт под summary-first данные, action summaries, history, health и watchlist flags, затем поверх этого пересобирается frontend: `Overview` становится decision-first экраном, `Latest Run` превращается в `Сигналы`, `Watchlist` убирает ручной `market_id`, `Operations` скрывает raw logs за вторичным действием, а tooltip system объясняет метрики и блоки.

**Tech Stack:** React, Vite, TypeScript, Python 3.11, FastAPI, SQLite, Docker Compose, Vitest, Testing Library, Python unittest

---

## Карта файлов

### Создать

- `dashboard/src/components/InfoTooltip.tsx` — единый tooltip/popover-компонент для hover/focus/tap объяснений
- `dashboard/src/components/SectionHeader.tsx` — заголовок секции с title, subtitle и optional tooltip
- `dashboard/src/components/KpiCard.tsx` — карточка ключевой метрики для `Сводки`
- `dashboard/src/components/SignalList.tsx` — компактный список главных сигналов для `Overview`
- `dashboard/src/components/ActionSummaryCard.tsx` — человеко-понятный результат последнего action
- `dashboard/src/content/tooltips.ts` — централизованный словарь подсказок
- `dashboard/src/utils/format.ts` — общие форматтеры процентов, дат, edge и short labels
- `dashboard/src/test/setup.ts` — setup для Vitest и Testing Library
- `dashboard/src/__tests__/InfoTooltip.test.tsx` — тесты tooltip UX
- `dashboard/src/__tests__/OverviewPage.test.tsx` — тесты summary-first overview
- `dashboard/src/__tests__/WatchlistPage.test.tsx` — тесты сценария без `market_id`
- `dashboard/src/__tests__/OperationsPage.test.tsx` — тесты human-summary-first operations

### Изменить

- `python/operator_core.py` — добавить summary-contract для overview, health, logs, top signals, watch flags, market history и action summaries
- `python/dashboard_models.py` — описать новые response models вместо слишком общих `dict[str, Any]`
- `python/dashboard_api.py` — добавить `GET /api/health`, `GET /api/logs`, обновить `overview`, `latest_run`, `market_details`, `watchlist`, `actions`
- `python/test_operator_core.py` — новые unit tests под summary-contract
- `python/test_dashboard_api.py` — новые API tests под health/logs/overview/actions/watch flags
- `dashboard/package.json` — добавить frontend test stack и scripts
- `dashboard/tsconfig.json` — учесть vitest/jsdom типы
- `dashboard/vite.config.ts` — включить test config
- `dashboard/src/types/api.ts` — новые строго типизированные DTO
- `dashboard/src/api/client.ts` — методы `health`, `logs`, `toggleWatchlist`, новые response types
- `dashboard/src/components/Layout.tsx` — переименовать навигацию и добавить более понятную shell-модель
- `dashboard/src/components/StatusPills.tsx` — понятные статусы и helper labels
- `dashboard/src/components/MarketTable.tsx` — превратить в таблицу сигналов с confidence, research status и watch action
- `dashboard/src/components/MarketSummaryCard.tsx` — либо удалить, либо встроить в новый `SignalList`
- `dashboard/src/components/WatchlistPanel.tsx` — убрать зависимость от ручного `market_id`
- `dashboard/src/components/ActionBar.tsx` — labels и статусы операторского уровня
- `dashboard/src/components/TimelinePanel.tsx` — показывать историю прогнозов по дням, а не только две даты
- `dashboard/src/pages/OverviewPage.tsx` — собрать `Hero summary`, `Что требует внимания`, `Состояние данных`, `Быстрые действия`, `Последний результат`
- `dashboard/src/pages/LatestRunPage.tsx` — переосмыслить как экран `Сигналы`
- `dashboard/src/pages/MarketDetailPage.tsx` — narrative layout вместо списка полей
- `dashboard/src/pages/WatchlistPage.tsx` — убрать форму `market_id`, показать уже отслеживаемые рынки
- `dashboard/src/pages/OperationsPage.tsx` — показывать human summary first, raw log only on demand
- `dashboard/src/pages/LoginPage.tsx` — не менять визуальный язык, только при необходимости текстовые пояснения
- `dashboard/src/app/routes.tsx` — сохранить routes, но обновить labels и breadcrumbs/заголовки
- `dashboard/src/styles.css` — поддержать tooltip system, новые grid layouts, summary cards, mobile popovers
- `README.md` — описать новый операторский UX
- `docs/ARCHITECTURE.md` — зафиксировать dashboard как decision-first interface
- `docs/DEPLOYMENT.md` — описать как проверять tooltip UX, health, watchlist flow и action summaries
- `docs/AUDIT.md` — обновить фактическое описание dashboard и operator workflow

### Не трогать без необходимости

- `src/main.ts`
- `src/services/market_selector.ts`
- `src/services/research_runner.ts`
- `src/prompts/synthesis.ts`
- `python/resolution_checker.py`
- `python/edge_calculator.py`

---

### Task 1: Расширить backend-контракт под decision-first dashboard

**Files:**
- Modify: `python/test_operator_core.py`
- Modify: `python/test_dashboard_api.py`
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `python/dashboard_api.py`

- [ ] **Step 1: Добавить падающий unit test на новый overview-contract**

```python
def test_overview_returns_summary_counts_top_signals_and_last_action(self):
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
                    forecast_timestamp TEXT NOT NULL,
                    outcome INTEGER,
                    is_valid INTEGER DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE watchlist (
                    market_id TEXT PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO predictions (
                    market_id, question, category, market_price, dexter_p, naked_p,
                    dexter_confidence, forecast_timestamp, outcome, is_valid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("m1", "Will Wolves finish last?", "sports", 43.6, 65.0, 68.0, "medium", "2026-04-04T08:16:48Z", None, 1),
                    ("m2", "Will Fed cut rates?", "politics", 58.0, 41.0, 46.0, "low", "2026-04-04T08:16:48Z", 1, 1),
                    ("m3", "Will BTC hit 150k?", "crypto", 31.0, 48.0, 44.0, "medium", "2026-04-04T08:16:48Z", None, 1),
                ],
            )
            conn.execute(
                "INSERT INTO watchlist (market_id, note, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("m1", "Проверить после следующего прогона", "2026-04-04T09:00:00Z", "2026-04-04T09:00:00Z"),
            )
            conn.commit()

        log_dir = Path(temp_dir) / "logs"
        log_dir.mkdir()
        (log_dir / "operator_action_status.json").write_text(
            json.dumps(
                {
                    "action": "forecast",
                    "status": "success",
                    "summary": "Обработано 3 рынка, research ON, 2 сильных расхождения.",
                    "started_at": "2026-04-04T09:10:00Z",
                    "finished_at": "2026-04-04T09:10:40Z",
                    "duration_seconds": 40,
                }
            ),
            encoding="utf-8",
        )

        core = OperatorCore(db_path=db_path, log_dir=log_dir)
        overview = core.get_overview()

        self.assertEqual(overview["resolved_predictions"], 1)
        self.assertEqual(overview["watchlist_count"], 1)
        self.assertEqual(len(overview["top_signals"]), 3)
        self.assertEqual(overview["last_action"]["action"], "forecast")
        self.assertIn("research", overview["statuses"])
```

- [ ] **Step 2: Добавить падающий API test на `health`, `logs` и новые action summaries**

```python
def test_dashboard_api_returns_health_logs_and_human_action_summary(self):
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "pythia.db"
        log_dir = Path(temp_dir) / "logs"
        log_dir.mkdir()
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
                ("m1", "Will example happen?", "2026-04-04T08:16:48Z", 1),
            )
            conn.commit()

        (log_dir / "forecast_20260404.log").write_text(
            "[2026-04-04T09:10:00Z] Forecast completed successfully\\n",
            encoding="utf-8",
        )

        app = create_app(
            db_path=db_path,
            log_dir=log_dir,
            session_secret="test-secret",
            dashboard_username="operator",
            dashboard_password_hash="plain:secret-pass",
        )
        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "operator", "password": "secret-pass"})

        health_res = client.get("/api/health")
        logs_res = client.get("/api/logs")
        action_res = client.post("/api/actions/forecast")

        self.assertEqual(health_res.status_code, 200)
        self.assertEqual(logs_res.status_code, 200)
        self.assertEqual(action_res.status_code, 200)
        self.assertIn("summary", action_res.json())
```

- [ ] **Step 3: Запустить backend tests и убедиться, что они падают**

Run:

```bash
python3 -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected:

- `AssertionError` по отсутствующим ключам `resolved_predictions`, `watchlist_count`, `top_signals`, `last_action`
- `404` или `AttributeError` для `/api/health` и `/api/logs`

- [ ] **Step 4: Расширить `OperatorCore` новыми summary-методами**

```python
class OperatorCore:
    def get_overview(self) -> dict[str, Any]:
        latest_run = self.get_latest_run()
        health = self.get_health()
        last_action = self.get_last_action_summary()
        with self._connect() as conn:
            valid_filter = self._valid_where_clause(conn)
            valid_predictions = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE 1=1{valid_filter}"
            ).fetchone()[0]
            resolved_predictions = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE outcome IN (0, 1){valid_filter}"
            ).fetchone()[0]
        return {
            "statuses": {
                "system": health["system_status"],
                "research": health["research_status"],
            },
            "valid_predictions": valid_predictions,
            "resolved_predictions": resolved_predictions,
            "watchlist_count": len(self.list_watchlist()),
            "latest_run": {
                "forecast_timestamp": latest_run["forecast_timestamp"],
                "markets_processed": latest_run["markets_processed"],
            },
            "top_signals": latest_run["items"][:5],
            "last_action": last_action,
            "research_warning": health["research_warning"],
        }

    def get_health(self) -> dict[str, Any]:
        return {
            "system_status": "ON" if self.predictions_table_exists() else "DEGRADED",
            "research_status": "ON",
            "research_warning": "",
            "checks": [],
        }

    def get_last_action_summary(self) -> dict[str, Any]:
        status_path = self.log_dir / "operator_action_status.json"
        if not status_path.exists():
            return {
                "action": None,
                "status": "idle",
                "summary": "Без недавних операторских действий.",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
            }
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {
                "action": "unknown",
                "status": "failed",
                "summary": "Не удалось прочитать статус последнего действия.",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
            }

    def get_logs_summary(self) -> dict[str, Any]:
        latest_log = sorted(self.log_dir.glob("*.log"))[-1] if any(self.log_dir.glob("*.log")) else None
        excerpt = ""
        if latest_log:
            excerpt = latest_log.read_text(encoding="utf-8", errors="ignore")[-2000:]
        return {
            "latest_file": latest_log.name if latest_log else None,
            "excerpt": excerpt,
        }
```

- [ ] **Step 5: Сделать `run_safe_action()` summary-first и писать `operator_action_status.json`**

```python
def run_safe_action(self, action: str) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    completed = subprocess.run(
        ["bash", str(entrypoint), action],
        cwd=base_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    finished_at = datetime.now(timezone.utc)
    summary = self._summarize_action_output(
        action=action,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    payload = {
        "action": action,
        "status": "success" if completed.returncode == 0 else "failed",
        "summary": summary,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "technical_log": (completed.stdout or completed.stderr)[-4000:],
        "returncode": completed.returncode,
    }
    (self.log_dir / "operator_action_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload
```

- [ ] **Step 6: Расширить response models и API endpoints**

```python
class ActionResponse(BaseModel):
    action: str
    status: Literal["queued", "running", "success", "failed"]
    summary: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    technical_log: str = ""
    returncode: int | None = None


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    require_session(request)
    return core.get_health()


@app.get("/api/logs")
def logs(request: Request) -> dict[str, Any]:
    require_session(request)
    return core.get_logs_summary()


@app.post("/api/actions/{action}", response_model=ActionResponse)
def run_action(action: str, request: Request) -> ActionResponse:
    require_session(request)
    if action not in SAFE_ACTION_NAMES:
        raise HTTPException(status_code=404, detail="UNKNOWN_ACTION")
    return ActionResponse(**core.run_safe_action(action))
```

- [ ] **Step 7: Запустить backend tests повторно**

Run:

```bash
python3 -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add python/operator_core.py python/dashboard_models.py python/dashboard_api.py python/test_operator_core.py python/test_dashboard_api.py
git commit -m "feat: expand dashboard backend for ux refresh"
```

---

### Task 2: Добавить tooltip system и frontend test harness

**Files:**
- Modify: `dashboard/package.json`
- Modify: `dashboard/tsconfig.json`
- Modify: `dashboard/vite.config.ts`
- Create: `dashboard/src/test/setup.ts`
- Create: `dashboard/src/content/tooltips.ts`
- Create: `dashboard/src/components/InfoTooltip.tsx`
- Create: `dashboard/src/components/SectionHeader.tsx`
- Create: `dashboard/src/__tests__/InfoTooltip.test.tsx`

- [ ] **Step 1: Добавить падающий frontend test на tooltip**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InfoTooltip } from "../components/InfoTooltip";

test("shows tooltip content on hover and keyboard focus", async () => {
  const user = userEvent.setup();
  render(
    <InfoTooltip
      label="Что такое Edge"
      title="Edge"
      body={[
        "Что это: разница между оценкой Pythia и рынком.",
        "Почему важно: большие расхождения требуют внимания.",
      ]}
    />
  );

  const trigger = screen.getByRole("button", { name: /что такое edge/i });
  await user.hover(trigger);
  expect(screen.getByText(/разница между оценкой pythia и рынком/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Добавить test stack в `dashboard/package.json`**

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^19.1.8",
    "@types/react-dom": "^19.1.6",
    "@vitejs/plugin-react": "^5.0.0",
    "jsdom": "^25.0.1",
    "typescript": "^5.5.0",
    "vite": "^7.1.0",
    "vitest": "^2.1.1"
  }
}
```

- [ ] **Step 3: Настроить vitest**

```ts
// dashboard/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    globals: true,
  },
});
```

```ts
// dashboard/src/test/setup.ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: Реализовать tooltip content registry**

```ts
// dashboard/src/content/tooltips.ts
export const TOOLTIP_COPY = {
  edge: {
    label: "Что такое Edge",
    title: "Edge",
    body: [
      "Что это: разница между оценкой Pythia и рыночной ценой.",
      "Почему важно: большие расхождения выделяют сигналы, требующие внимания.",
      "Как читать: плюс означает выше рынка, минус означает ниже рынка.",
    ],
  },
  researchStatus: {
    label: "Что такое Research status",
    title: "Research status",
    body: [
      "Что это: состояние слоя новостного и аналитического поиска.",
      "Почему важно: degraded research снижает надёжность explainability.",
      "Как читать: ON — поиск работает, DEGRADED — частично недоступен.",
    ],
  },
} as const;
```

- [ ] **Step 5: Реализовать `InfoTooltip` и `SectionHeader`**

```tsx
// dashboard/src/components/InfoTooltip.tsx
import { useId, useState } from "react";

interface InfoTooltipProps {
  label: string;
  title: string;
  body: string[];
}

export function InfoTooltip({ label, title, body }: InfoTooltipProps) {
  const tooltipId = useId();
  const [open, setOpen] = useState(false);

  return (
    <span
      className="info-tooltip"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? tooltipId : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        className="info-tooltip-trigger"
      >
        i
      </button>
      {open ? (
        <span role="tooltip" id={tooltipId} className="info-tooltip-popover">
          <strong>{title}</strong>
          {body.map((line) => (
            <span key={line}>{line}</span>
          ))}
        </span>
      ) : null}
    </span>
  );
}
```

- [ ] **Step 6: Запустить frontend tests и убедиться, что они проходят**

Run:

```bash
cd dashboard && npm install && npm run test
```

Expected:

- `InfoTooltip.test.tsx` passes

- [ ] **Step 7: Commit**

```bash
git add dashboard/package.json dashboard/tsconfig.json dashboard/vite.config.ts dashboard/src/test/setup.ts dashboard/src/content/tooltips.ts dashboard/src/components/InfoTooltip.tsx dashboard/src/components/SectionHeader.tsx dashboard/src/__tests__/InfoTooltip.test.tsx
git commit -m "feat: add dashboard tooltip system"
```

---

### Task 3: Пересобрать `Сводку` и `Сигналы` в decision-first workflow

**Files:**
- Create: `dashboard/src/components/KpiCard.tsx`
- Create: `dashboard/src/components/SignalList.tsx`
- Create: `dashboard/src/__tests__/OverviewPage.test.tsx`
- Modify: `dashboard/src/types/api.ts`
- Modify: `dashboard/src/api/client.ts`
- Modify: `dashboard/src/components/Layout.tsx`
- Modify: `dashboard/src/components/StatusPills.tsx`
- Modify: `dashboard/src/components/MarketTable.tsx`
- Modify: `dashboard/src/pages/OverviewPage.tsx`
- Modify: `dashboard/src/pages/LatestRunPage.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий test на summary-first overview**

```tsx
test("renders hero summary, top signals and quick actions", async () => {
  vi.spyOn(api, "overview").mockResolvedValue({
    statuses: { system: "ON", research: "DEGRADED" },
    valid_predictions: 22,
    resolved_predictions: 4,
    watchlist_count: 3,
    latest_run: { forecast_timestamp: "2026-04-04T09:10:00Z", markets_processed: 11 },
    top_signals: [
      {
        market_id: "m1",
        question: "Will Wolves finish last?",
        market_price: 43.6,
        research_probability: 65,
        naked_probability: 68,
        edge: 21.4,
        category: "sports",
        confidence: "medium",
        research_status: "ON",
        is_watched: true,
      },
    ],
    last_action: {
      action: "forecast",
      status: "success",
      summary: "Обработано 11 рынков.",
      started_at: "2026-04-04T09:10:00Z",
      finished_at: "2026-04-04T09:10:40Z",
      duration_seconds: 40,
    },
    research_warning: "Exa degraded, Tavily active.",
  });

  render(<OverviewPage />);

  expect(await screen.findByText(/что требует внимания/i)).toBeInTheDocument();
  expect(screen.getByText(/обработано 11 рынков/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /запустить forecast/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Расширить frontend DTO**

```ts
export interface SignalItem {
  market_id: string;
  question: string;
  category?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  edge?: number | null;
  confidence?: string | null;
  research_status?: "ON" | "DEGRADED" | "OFF";
  is_watched?: boolean;
  volume?: number | null;
  end_date?: string | null;
}

export interface OverviewResponse {
  statuses: {
    system: "ON" | "DEGRADED" | "OFF";
    research: "ON" | "DEGRADED" | "OFF";
  };
  valid_predictions: number;
  resolved_predictions: number;
  watchlist_count: number;
  latest_run: {
    forecast_timestamp: string | null;
    markets_processed: number;
  };
  top_signals: SignalItem[];
  last_action: ActionResponse | ActionIdleResponse;
  research_warning: string;
}
```

- [ ] **Step 3: Переименовать навигацию и упростить labels**

```tsx
const NAV_ITEMS = [
  { to: "/", label: "Сводка" },
  { to: "/runs/latest", label: "Сигналы" },
  { to: "/watchlist", label: "Избранное" },
  { to: "/operations", label: "Действия" },
];
```

- [ ] **Step 4: Реализовать `KpiCard` и `SignalList`**

```tsx
export function KpiCard({ title, value, tooltipKey }: KpiCardProps) {
  const tooltip = TOOLTIP_COPY[tooltipKey];
  return (
    <article className="kpi-card">
      <SectionHeader title={title} tooltip={tooltip} />
      <strong className="kpi-value">{value}</strong>
    </article>
  );
}
```

```tsx
export function SignalList({ items, onToggleWatch }: SignalListProps) {
  return (
    <section className="panel">
      <SectionHeader title="Что требует внимания" subtitle="Главные сигналы текущего цикла." />
      {items.map((item) => (
        <article key={item.market_id} className="signal-list-item">
          <div>
            <Link to={`/markets/${item.market_id}`}>{item.question}</Link>
            <p className="muted">
              Толпа {formatPercent(item.market_price)} -> Pythia {formatPercent(item.research_probability)} ({formatEdge(item.edge)})
            </p>
          </div>
          <button type="button" className="ghost-btn" onClick={() => onToggleWatch(item.market_id)}>
            {item.is_watched ? "В избранном" : "Следить"}
          </button>
        </article>
      ))}
    </section>
  );
}
```

- [ ] **Step 5: Переписать `OverviewPage`**

```tsx
export function OverviewPage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.overview().then(setOverview).catch(() => setError("Не удалось загрузить сводку."));
  }, []);

  if (error) return <main className="page-grid"><section className="panel">{error}</section></main>;
  if (!overview) return <main className="page-grid"><section className="panel">Загрузка сводки...</section></main>;

  return (
    <main className="dashboard-grid overview-grid">
      <section className="panel hero-panel">
        <SectionHeader title="Сводка Pythia" subtitle="Что происходит сейчас и где нужен ваш взгляд." />
        <StatusPills system={overview.statuses.system} research={overview.statuses.research} />
        <p className="hero-summary">
          Последний прогон: {formatDateTime(overview.latest_run.forecast_timestamp)}. Обработано {overview.latest_run.markets_processed} рынков.
        </p>
      </section>
      <section className="kpi-grid">
        <KpiCard title="Валидные прогнозы" value={String(overview.valid_predictions)} tooltipKey="validPredictions" />
        <KpiCard title="Resolved" value={String(overview.resolved_predictions)} tooltipKey="resolved" />
        <KpiCard title="Избранное" value={String(overview.watchlist_count)} tooltipKey="watchlist" />
      </section>
      <SignalList items={overview.top_signals} onToggleWatch={handleToggleWatch} />
      <ActionSummaryCard action={overview.last_action} />
      <section className="panel">
        <SectionHeader title="Быстрые действия" subtitle="Безопасные действия без перехода в отдельный экран." />
        <ActionBar busyAction={busy} onRun={handleRunFromOverview} />
      </section>
    </main>
  );
}
```

- [ ] **Step 6: Переписать `LatestRunPage` как экран `Сигналы`**

```tsx
<main className="page-grid">
  <section className="panel">
    <SectionHeader title="Сигналы" subtitle="Рынки последнего прогона, отсортированные по важности." />
    <MarketTable items={sortedItems} onToggleWatch={handleToggleWatch} />
  </section>
</main>
```

`MarketTable` должен показывать:

- question
- category
- market_price
- research_probability
- naked_probability
- edge
- confidence
- research_status
- watch button

- [ ] **Step 7: Запустить frontend tests и build**

Run:

```bash
cd dashboard && npm run test && npm run build
```

Expected:

- overview test passes
- vite build succeeds

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/types/api.ts dashboard/src/api/client.ts dashboard/src/components/Layout.tsx dashboard/src/components/StatusPills.tsx dashboard/src/components/MarketTable.tsx dashboard/src/components/KpiCard.tsx dashboard/src/components/SignalList.tsx dashboard/src/pages/OverviewPage.tsx dashboard/src/pages/LatestRunPage.tsx dashboard/src/styles.css dashboard/src/__tests__/OverviewPage.test.tsx
git commit -m "feat: rebuild dashboard overview and signals"
```

---

### Task 4: Переделать `Рынок`, `Избранное` и `Действия` под человеческий workflow

**Files:**
- Create: `dashboard/src/components/ActionSummaryCard.tsx`
- Create: `dashboard/src/__tests__/WatchlistPage.test.tsx`
- Create: `dashboard/src/__tests__/OperationsPage.test.tsx`
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `python/dashboard_api.py`
- Modify: `dashboard/src/components/WatchlistPanel.tsx`
- Modify: `dashboard/src/components/ActionBar.tsx`
- Modify: `dashboard/src/components/TimelinePanel.tsx`
- Modify: `dashboard/src/pages/MarketDetailPage.tsx`
- Modify: `dashboard/src/pages/WatchlistPage.tsx`
- Modify: `dashboard/src/pages/OperationsPage.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий test на `WatchlistPage` без `market_id` формы**

```tsx
test("watchlist page does not require market_id input and renders tracked markets", async () => {
  vi.spyOn(api, "watchlist").mockResolvedValue({
    items: [
      {
        market_id: "m1",
        question: "Will Wolves finish last?",
        note: "Следить за обновлением формы",
        market_price: 43.6,
        research_probability: 65,
        forecast_timestamp: "2026-04-04T09:10:00Z",
      },
    ],
  });

  render(<WatchlistPage />);

  expect(await screen.findByText(/следить за обновлением формы/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/market_id/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Написать падающий test на `OperationsPage`, который требует human summary first**

```tsx
test("operations page shows human summary first and hides raw log behind details button", async () => {
  vi.spyOn(api, "runAction").mockResolvedValue({
    action: "forecast",
    status: "success",
    summary: "Обработано 11 рынков, research ON, 2 сильных сигнала.",
    started_at: "2026-04-04T09:10:00Z",
    finished_at: "2026-04-04T09:10:40Z",
    duration_seconds: 40,
    technical_log: "Gamma fetch ...",
    returncode: 0,
  });

  render(<OperationsPage />);
  await user.click(screen.getByRole("button", { name: /запустить forecast/i }));
  await user.click(screen.getByRole("button", { name: /подтвердить запуск/i }));

  expect(await screen.findByText(/обработано 11 рынков/i)).toBeInTheDocument();
  expect(screen.queryByText(/gamma fetch/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Расширить backend для `Market Detail`**

```python
def get_market_details(self, market_id: str) -> dict[str, Any]:
    latest = ...
    history_rows = conn.execute(
        """
        SELECT forecast_timestamp, market_price, dexter_p, naked_p
        FROM predictions
        WHERE market_id = ? AND is_valid = 1
        ORDER BY forecast_timestamp ASC, id ASC
        """,
        (market_id,),
    ).fetchall()
    is_watched = conn.execute(
        "SELECT 1 FROM watchlist WHERE market_id = ? LIMIT 1",
        (market_id,),
    ).fetchone() is not None
    return {
        ...,
        "verdict_summary": self._build_verdict_summary(...),
        "is_watched": is_watched,
        "history": [
            {
                "forecast_timestamp": row[0],
                "market_price": row[1],
                "research_probability": row[2],
                "naked_probability": row[3],
            }
            for row in history_rows
        ],
        "research": {
            ...,
            "confirming_signals": self._research_list(research, "confirming_signals"),
        },
    }
```

- [ ] **Step 4: Переписать `MarketDetailPage` в narrative layout**

```tsx
export function MarketDetailPage() {
  ...
  return (
    <main className="detail-grid">
      <section className="panel detail-hero">
        <SectionHeader title="Разбор рынка" subtitle="Почему Pythia расходится с рынком и что это значит." />
        <h1>{details.question}</h1>
        <p className="hero-metrics">
          Рынок {formatPercent(details.market_price)} | Research+R1 {formatPercent(details.research_probability)} | Naked {formatPercent(details.naked_probability)} | Edge {formatEdge(details.edge)}
        </p>
        <button type="button" className="ghost-btn" onClick={handleToggleWatch}>
          {details.is_watched ? "Убрать из избранного" : "Следить за рынком"}
        </button>
      </section>

      <section className="panel">
        <SectionHeader title="Вердикт Pythia" />
        <p>{details.verdict_summary}</p>
      </section>

      <section className="panel">
        <SectionHeader title="Почему модель так думает" />
        <p><strong>Обоснование:</strong> {details.reasoning}</p>
        <p><strong>Базовая частота:</strong> {details.base_rate}</p>
        <p><strong>Главный драйвер:</strong> {details.research.primary_driver}</p>
        <p><strong>Ключевой риск:</strong> {details.key_risk}</p>
      </section>

      <section className="panel">
        <SectionHeader title="На что смотреть дальше" />
        <ul>
          {details.research.confirming_signals?.map((item) => <li key={item}>{item}</li>)}
        </ul>
        <p><strong>Опровергающий сигнал:</strong> {details.research.disconfirming_signal}</p>
      </section>

      <TimelinePanel history={details.history} endDate={details.end_date} />
    </main>
  );
}
```

- [ ] **Step 5: Упростить `WatchlistPage`**

```tsx
export function WatchlistPage() {
  ...
  return (
    <main className="page-grid">
      <section className="panel">
        <SectionHeader title="Избранное" subtitle="Рынки, за которыми вы решили следить отдельно." />
        <p className="muted">Рынки добавляются сюда из экранов `Сводка`, `Сигналы` и `Разбор рынка`.</p>
      </section>
      <WatchlistPanel items={items} onRemove={onRemove} />
    </main>
  );
}
```

- [ ] **Step 6: Переделать `OperationsPage` в summary-first**

```tsx
{status ? (
  <ActionSummaryCard
    action={status.action}
    status={status.status}
    summary={status.summary}
    startedAt={status.started_at}
    finishedAt={status.finished_at}
    durationSeconds={status.duration_seconds}
    technicalLog={status.technical_log}
  />
) : (
  <p className="muted">Выберите безопасное действие. Технический лог скрыт до явного запроса.</p>
)}
```

`ActionSummaryCard` должен:

- показывать `summary` как основной результат
- скрывать `technicalLog` за toggle `Показать технический лог`

- [ ] **Step 7: Запустить frontend tests и build**

Run:

```bash
cd dashboard && npm run test && npm run build
```

Expected:

- watchlist/operations tests pass
- build succeeds

- [ ] **Step 8: Commit**

```bash
git add python/operator_core.py python/dashboard_models.py python/dashboard_api.py dashboard/src/components/WatchlistPanel.tsx dashboard/src/components/ActionBar.tsx dashboard/src/components/TimelinePanel.tsx dashboard/src/components/ActionSummaryCard.tsx dashboard/src/pages/MarketDetailPage.tsx dashboard/src/pages/WatchlistPage.tsx dashboard/src/pages/OperationsPage.tsx dashboard/src/styles.css dashboard/src/__tests__/WatchlistPage.test.tsx dashboard/src/__tests__/OperationsPage.test.tsx
git commit -m "feat: humanize dashboard detail watchlist and actions"
```

---

### Task 5: Обновить документацию и проверить релизный контур

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/AUDIT.md`

- [ ] **Step 1: Обновить `README.md`**

Добавить короткий блок:

```md
## Operator Dashboard

Основной рабочий интерфейс оператора — dashboard на `https://b1w.duckdns.org`.

В dashboard доступны:

- `Сводка` — состояние системы и главные сигналы;
- `Сигналы` — рынки последнего прогона, отсортированные по важности;
- `Избранное` — отслеживаемые рынки без ручного `market_id`;
- `Действия` — безопасные operator actions с human-readable итогами.

Подсказки `i` рядом с блоками объясняют смысл метрик и помогают читать интерфейс без внешней документации.
```

- [ ] **Step 2: Обновить `docs/ARCHITECTURE.md`**

Добавить раздел:

```md
## Dashboard UX v2

Dashboard больше не является thin UI над API-ответами.

Он реализует decision-first операторский слой:

- `Сводка` отвечает на вопрос "что происходит сейчас?";
- `Сигналы` отвечает на вопрос "какие расхождения важнее всего?";
- `Разбор рынка` объясняет "почему Pythia думает иначе, чем рынок?";
- `Действия` возвращают human summary first, а raw logs остаются вторичным уровнем.
```

- [ ] **Step 3: Обновить `docs/DEPLOYMENT.md`**

Добавить post-deploy checks:

```md
## Dashboard UX Checks

После деплоя проверьте:

1. `Сводка` открывается и показывает system/research status, counts и главные сигналы.
2. Наведение или focus на иконку `i` показывает tooltip.
3. В `Избранное` нет формы ручного ввода `market_id`.
4. `Действия` показывают человеко-понятный итог, а не raw log по умолчанию.
5. `Разбор рынка` позволяет добавить рынок в избранное без перехода в Telegram.
```

- [ ] **Step 4: Обновить `docs/AUDIT.md`**

Добавить/обновить формулировку:

```md
Dashboard актуальной версии построен как decision-first operator console.

Его задача:

- объяснять состояние системы;
- выделять главные сигналы;
- давать оператору безопасные действия;
- снижать когнитивную нагрузку через встроенные tooltip-подсказки.

Dashboard больше не требует ручного знания `market_id` в основных UX-сценариях.
Raw logs в операторском UI доступны только как вторичный технический слой.
```

- [ ] **Step 5: Выполнить полную верификацию**

Run:

```bash
cd /Users/gerwer/Desktop/Pythia
python3 -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
cd dashboard && npm run test && npm run build
```

Expected:

- backend tests pass
- frontend tests pass
- frontend build succeeds

- [ ] **Step 6: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md docs/AUDIT.md
git commit -m "docs: document dashboard ux refresh"
```

---

## Self-review

### Spec coverage

- `Overview` как summary-first экран покрыт Task 3.
- `Сигналы` вместо просто `Latest Run` покрыты Task 3.
- tooltip system покрыт Task 2.
- narrative `Market Detail` покрыт Task 4.
- watchlist без `market_id` покрыт Task 4.
- human-summary-first `Operations` покрыт Task 4.
- docs и rollout checks покрыты Task 5.

### Placeholder scan

- Нет `TODO`, `TBD`, "similar to", "implement later".
- Во всех задачах указаны конкретные файлы, команды и ожидаемые результаты.

### Type consistency

- Во всех задачах используется единая терминология `top_signals`, `last_action`, `research_warning`, `is_watched`, `technical_log`, `SignalItem`, `ActionSummaryCard`, `InfoTooltip`.
- Навигационные labels согласованы: `Сводка`, `Сигналы`, `Избранное`, `Действия`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-04-pythia-operator-dashboard-ux-refresh-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
