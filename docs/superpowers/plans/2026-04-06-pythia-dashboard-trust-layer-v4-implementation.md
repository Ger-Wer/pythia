# Pythia Dashboard Trust Layer v4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в Pythia полноценный trust-layer: страницу качества, ежедневный digest по главным сигналам, исторический график в разборе рынка, усиленную прозрачность вывода и единый слой объяснения метрик.

**Architecture:** Реализация идёт поверх уже существующего operator dashboard. Backend остаётся в `python/operator_core.py` и `python/dashboard_api.py`, но качество и trust-данные выносятся в отдельный helper-модуль, чтобы не раздувать `operator_core`. Frontend получает новый экран `Качество`, усиливает `Сводку` и `Разбор рынка`, а график строится собственным SVG-компонентом без добавления chart-зависимостей.

**Tech Stack:** FastAPI + unittest, SQLite, существующие `edge_calculator.py` / `analyze.py`, React + TypeScript + Vitest + Testing Library, CSS без сторонних UI/chart библиотек.

---

## File Map

**Backend**
- Create: `python/quality_metrics.py` — чтение resolved-данных из SQLite, quality snapshot, breakdown по категориям, decision summary, поиск актуального calibration PNG.
- Modify: `python/operator_core.py` — добавить `get_quality()` и digest-данные для `overview`, не дублируя SQL по метрикам.
- Modify: `python/dashboard_models.py` — новые response-модели `QualityResponse`, `QualityMetricGlossaryItem`, `DailyDigestItem`.
- Modify: `python/dashboard_api.py` — новый endpoint `GET /api/quality`.
- Modify: `python/test_operator_core.py` — unit-тесты quality/digest/transparency payload.
- Modify: `python/test_dashboard_api.py` — API-тесты `GET /api/quality`.

**Frontend**
- Create: `dashboard/src/pages/QualityPage.tsx` — экран доверия и качества.
- Create: `dashboard/src/components/QualityKpiGrid.tsx` — KPI-блок для resolved, Brier, edge, decision.
- Create: `dashboard/src/components/CategoryBreakdownTable.tsx` — таблица качества по категориям.
- Create: `dashboard/src/components/MetricGlossaryPanel.tsx` — “Как читать метрики”.
- Create: `dashboard/src/components/DailyDigestCard.tsx` — commentary-layer для `Сводки`.
- Create: `dashboard/src/components/ProbabilityHistoryChart.tsx` — SVG-график `Research+R1 / Naked / рынок`.
- Create: `dashboard/src/__tests__/QualityPage.test.tsx` — тесты trust-page.
- Modify: `dashboard/src/app/routes.tsx` — маршрут `/quality`.
- Modify: `dashboard/src/components/Layout.tsx` — пункт навигации `Качество`.
- Modify: `dashboard/src/pages/OverviewPage.tsx` — блок `Главные сигналы дня`.
- Modify: `dashboard/src/pages/MarketDetailPage.tsx` — график и transparency-слой.
- Modify: `dashboard/src/content/tooltips.ts` — метрики и glossary в одном стиле.
- Modify: `dashboard/src/types/api.ts` — quality/digest/transparency DTO.
- Modify: `dashboard/src/styles.css` — стили trust-layer.
- Modify: `dashboard/src/__tests__/OverviewPage.test.tsx`
- Modify: `dashboard/src/__tests__/MarketDetailPage.test.tsx`
- Modify: `dashboard/src/__tests__/InfoTooltip.test.tsx`

**Docs**
- Modify: `README.md`
- Modify: `docs/AUDIT.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEPLOYMENT.md`

---

### Task 1: Добавить backend-контракт страницы `Качество`

**Files:**
- Create: `python/quality_metrics.py`
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `python/dashboard_api.py`
- Modify: `python/test_operator_core.py`
- Modify: `python/test_dashboard_api.py`
- Modify: `dashboard/src/types/api.ts`

- [ ] **Step 1: Написать падающие backend-тесты для quality contract**

Покрыть два сценария:
- resolved-данных ещё мало;
- данных достаточно и quality payload содержит `brier`, `edge`, `decision`, `category_breakdown`, `calibration_image`, `glossary`.

Run:
```bash
python/venv/bin/python -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected:
- новые тесты падают на отсутствии `get_quality()` и `/api/quality`.

- [ ] **Step 2: Создать `python/quality_metrics.py`**

Добавить в модуль:
- `prediction_columns(conn) -> set[str]`
- `valid_filter(columns: set[str]) -> str`
- `load_quality_snapshot(db_path: Path | str) -> dict`

`load_quality_snapshot()` должен вернуть структуру:

```python
{
    "resolved_predictions": 0,
    "minimum_needed": 20,
    "status": "INSUFFICIENT_DATA" | "COMPLETE",
    "decision": "COLLECT" | "GO" | "PIVOT" | "STOP",
    "summary": "человеческая фраза для оператора",
    "metrics": {
        "brier_research": 0.0 | None,
        "brier_naked": 0.0 | None,
        "brier_market": 0.0 | None,
        "edge_research_vs_market": 0.0 | None,
        "edge_naked_vs_market": 0.0 | None,
    },
    "category_breakdown": [
        {
            "category": "Politics",
            "resolved": 4,
            "brier_research": 0.1821,
            "brier_market": 0.1942,
        }
    ],
    "calibration_image": "logs/calibration_20260406.png" | None,
    "glossary": [
        {
            "id": "brier",
            "label": "Brier score",
            "what": "Что это",
            "why": "Почему важно",
            "how": "Как читать",
        }
    ],
}
```

Данные брать из:
- SQLite для counts и breakdown;
- `check_edge()` из `python/edge_calculator.py` для итоговых Brier/edge/decision;
- `logs/calibration_*.png` как последний доступный calibration asset.

- [ ] **Step 3: Подключить quality snapshot к operator core и API**

В `python/operator_core.py` добавить:
- `def get_quality(self) -> dict:`

В `python/dashboard_models.py` добавить модели:
- `QualityMetricGlossaryItem`
- `QualityMetricSummary`
- `QualityCategoryRow`
- `QualityResponse`

В `python/dashboard_api.py` добавить:

```python
@app.get("/api/quality", response_model=QualityResponse)
def quality(request: Request) -> QualityResponse:
    require_session(request)
    return QualityResponse(**core.get_quality())
```

В `dashboard/src/types/api.ts` синхронизировать интерфейсы:
- `QualityResponse`
- `QualityMetricSummary`
- `QualityCategoryRow`
- `QualityGlossaryItem`

- [ ] **Step 4: Прогнать backend-тесты до зелёного**

Run:
```bash
python/venv/bin/python -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected:
- `OK`

- [ ] **Step 5: Commit**

```bash
git add python/quality_metrics.py python/operator_core.py python/dashboard_models.py python/dashboard_api.py python/test_operator_core.py python/test_dashboard_api.py dashboard/src/types/api.ts
git commit -m "feat: add dashboard quality api"
```

---

### Task 2: Реализовать страницу `Качество`

**Files:**
- Create: `dashboard/src/pages/QualityPage.tsx`
- Create: `dashboard/src/components/QualityKpiGrid.tsx`
- Create: `dashboard/src/components/CategoryBreakdownTable.tsx`
- Create: `dashboard/src/components/MetricGlossaryPanel.tsx`
- Create: `dashboard/src/__tests__/QualityPage.test.tsx`
- Modify: `dashboard/src/app/routes.tsx`
- Modify: `dashboard/src/components/Layout.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий frontend-тест для страницы качества**

Тест должен проверять два состояния:
- при `status = INSUFFICIENT_DATA` есть честное сообщение “недостаточно resolved-рынков”;
- при `status = COMPLETE` видны `Brier`, `edge vs market`, `решение`, `category breakdown`, `Как читать метрики`.

Run:
```bash
cd dashboard && npm test -- QualityPage.test.tsx
```

Expected:
- `FAIL`

- [ ] **Step 2: Собрать новую страницу и маршрут**

В `dashboard/src/app/routes.tsx` добавить:

```tsx
{ path: "quality", element: <QualityPage /> },
```

В `dashboard/src/components/Layout.tsx` добавить пункт навигации:
- `Качество`

В `dashboard/src/pages/QualityPage.tsx`:
- загрузить `/api/quality`;
- отрисовать hero с human-summary;
- отрисовать `QualityKpiGrid`;
- при наличии resolved-данных показать `CategoryBreakdownTable`;
- ниже показать `MetricGlossaryPanel`.

Не добавлять сторонние chart/UI-библиотеки.

- [ ] **Step 3: Довести стили**

В `dashboard/src/styles.css` добавить классы:
- `.quality-hero`
- `.quality-grid`
- `.quality-summary`
- `.quality-breakdown-table`
- `.glossary-grid`

Смысл визуального слоя:
- без “аналитической свалки”;
- одна доминирующая summary-зона сверху;
- читаемые карточки, пригодные и на мобильном.

- [ ] **Step 4: Прогнать тест страницы качества**

Run:
```bash
cd dashboard && npm test -- QualityPage.test.tsx
```

Expected:
- `PASS`

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/QualityPage.tsx dashboard/src/components/QualityKpiGrid.tsx dashboard/src/components/CategoryBreakdownTable.tsx dashboard/src/components/MetricGlossaryPanel.tsx dashboard/src/__tests__/QualityPage.test.tsx dashboard/src/app/routes.tsx dashboard/src/components/Layout.tsx dashboard/src/styles.css
git commit -m "feat: add dashboard quality page"
```

---

### Task 3: Добавить `Главные сигналы дня` на `Сводку`

**Files:**
- Create: `dashboard/src/components/DailyDigestCard.tsx`
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `dashboard/src/types/api.ts`
- Modify: `dashboard/src/pages/OverviewPage.tsx`
- Modify: `dashboard/src/__tests__/OverviewPage.test.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающие тесты для digest-слоя**

Новые ожидания:
- `OverviewPage` показывает блок `Главные сигналы дня`;
- есть один headline по последнему прогону;
- есть до `3` digest-items;
- каждый item содержит `почему это важно`.

Run:
```bash
cd dashboard && npm test -- OverviewPage.test.tsx
```

Expected:
- `FAIL`

- [ ] **Step 2: Добавить digest в overview payload**

В `python/operator_core.py` расширить `get_overview()` полем:

```python
"daily_digest": {
    "headline": "Сегодня Pythia видит 2 сигнала, которые заметно расходятся с рынком.",
    "summary": "Главный фокус — рынки с двузначным edge и research=ON.",
    "items": [
        {
            "market_id": "...",
            "title": "...",
            "detail": "...",
            "reason_why_it_matters": "...",
        }
    ],
}
```

Digest генерировать детерминированно из:
- `top_signals`;
- `research_status`;
- `watchlist_count`;
- `latest_run.markets_processed`.

Никаких дополнительных LLM-вызовов в v4.

- [ ] **Step 3: Отрисовать digest в `OverviewPage`**

`DailyDigestCard` должен показывать:
- headline;
- 1 короткий summary;
- до `3` пунктов с CTA `Открыть разбор`.

Блок должен стоять выше списка карточек внимания, а не ниже него.

- [ ] **Step 4: Прогнать тест**

Run:
```bash
cd dashboard && npm test -- OverviewPage.test.tsx
```

Expected:
- `PASS`

- [ ] **Step 5: Commit**

```bash
git add python/operator_core.py python/dashboard_models.py dashboard/src/types/api.ts dashboard/src/components/DailyDigestCard.tsx dashboard/src/pages/OverviewPage.tsx dashboard/src/__tests__/OverviewPage.test.tsx dashboard/src/styles.css
git commit -m "feat: add daily digest to overview"
```

---

### Task 4: Усилить `Разбор рынка` графиком и прозрачностью

**Files:**
- Create: `dashboard/src/components/ProbabilityHistoryChart.tsx`
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `dashboard/src/types/api.ts`
- Modify: `dashboard/src/pages/MarketDetailPage.tsx`
- Modify: `dashboard/src/__tests__/MarketDetailPage.test.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий тест для history/transparency слоя**

Тест должен проверить:
- на `MarketDetailPage` появляется графический блок `История оценки`;
- ниже есть блок `Прозрачность вывода`;
- в transparency-блоке есть:
  - `Какие факты использованы`
  - `Какие источники использованы`
  - `Что может опровергнуть прогноз`
  - `Последнее обновление`

Run:
```bash
cd dashboard && npm test -- MarketDetailPage.test.tsx
```

Expected:
- `FAIL`

- [ ] **Step 2: Обогатить backend payload**

В `python/operator_core.py` расширить `get_market_details()` полями:
- `last_updated`
- `transparency = { facts, sources, falsifier, updated_at }`

`facts` брать из `research.key_facts`, `summary`, `primary_driver`;
`falsifier` брать из `disconfirming_signal` или `key_risk`.

В `dashboard/src/types/api.ts` и `python/dashboard_models.py` синхронизировать эти поля.

- [ ] **Step 3: Реализовать `ProbabilityHistoryChart`**

Сделать собственный SVG-график без новой зависимости.

Минимальный контракт:

```tsx
type ProbabilityHistoryPoint = {
  forecast_timestamp?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
};
```

График должен:
- показывать три линии;
- иметь простую легенду;
- быть устойчивым к `null`;
- нормально ужиматься на мобильном.

- [ ] **Step 4: Обновить `MarketDetailPage`**

Порядок блоков:
1. `Что думает Pythia`
2. `Почему расходится с рынком`
3. `История оценки`
4. `Что может сломать прогноз`
5. `Прозрачность вывода`
6. `Что отслеживать дальше`

Таймлайн и длинный research prose оставить ниже как вторичный слой.

- [ ] **Step 5: Прогнать тест**

Run:
```bash
cd dashboard && npm test -- MarketDetailPage.test.tsx
```

Expected:
- `PASS`

- [ ] **Step 6: Commit**

```bash
git add python/operator_core.py python/dashboard_models.py dashboard/src/types/api.ts dashboard/src/components/ProbabilityHistoryChart.tsx dashboard/src/pages/MarketDetailPage.tsx dashboard/src/__tests__/MarketDetailPage.test.tsx dashboard/src/styles.css
git commit -m "feat: add market history and transparency layer"
```

---

### Task 5: Довести единый слой объяснения метрик и обновить документацию

**Files:**
- Modify: `dashboard/src/content/tooltips.ts`
- Modify: `dashboard/src/components/InfoTooltip.tsx`
- Modify: `dashboard/src/components/MetricGlossaryPanel.tsx`
- Modify: `dashboard/src/__tests__/InfoTooltip.test.tsx`
- Modify: `README.md`
- Modify: `docs/AUDIT.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Написать падающий тест на общую модель glossary/tooltips**

Проверить:
- tooltip для `Brier`;
- tooltip для `edge`;
- tooltip для `confidence`;
- одинаковую трёхчастную структуру:
  - `Что это`
  - `Почему важно`
  - `Как читать`

Run:
```bash
cd dashboard && npm test -- InfoTooltip.test.tsx
```

Expected:
- `FAIL`

- [ ] **Step 2: Свести glossary и tooltips к одному словарю**

В `dashboard/src/content/tooltips.ts` привести trust-метрики к единому контракту и добавить записи:
- `brier`
- `edge`
- `resolved`
- `confidence`
- `research-status`
- `calibration`

`MetricGlossaryPanel` должен использовать тот же источник данных, а не отдельные вручную написанные описания.

- [ ] **Step 3: Обновить документацию**

Внести правки:
- `README.md` — новый раздел `Качество` и trust-layer;
- `docs/AUDIT.md` — источники доверия, quality page, commentary layer, history chart;
- `docs/ARCHITECTURE.md` — quality snapshot + shared glossary model;
- `docs/DEPLOYMENT.md` — операторский smoke-check после деплоя: `Сводка`, `Качество`, `Разбор`.

- [ ] **Step 4: Прогнать полную верификацию**

Run:
```bash
python/venv/bin/python -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
cd dashboard && npm test
cd dashboard && npm run build
```

Expected:
- backend tests `OK`
- frontend tests `PASS`
- build `✓ built`

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/content/tooltips.ts dashboard/src/components/InfoTooltip.tsx dashboard/src/components/MetricGlossaryPanel.tsx dashboard/src/__tests__/InfoTooltip.test.tsx README.md docs/AUDIT.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md
git commit -m "docs: finalize dashboard trust layer v4"
```

---

## Rollout Notes

- В `v4` не добавлять новые внешние зависимости, если можно обойтись существующим стеком.
- `Daily digest` должен быть детерминированным и дешёвым: никаких новых LLM-вызовов до накопления resolved-данных.
- `Quality page` должна честно уметь жить в состоянии “данных пока недостаточно”.
- Если calibration PNG отсутствует, не считать это ошибкой: просто показывать human-message, что график появится после накопления resolved-рынков.

## Done Definition

`v4` считается завершённым, когда:

1. В навигации есть `Качество`, и страница открывается без ошибок.
2. `Сводка` показывает `Главные сигналы дня` как отдельный commentary-layer.
3. `Разбор рынка` показывает график `Research+R1 / Naked / рынок`.
4. Оператор может понять `Brier`, `edge`, `confidence`, `research status` без чтения исходников.
5. Все backend/frontend тесты зелёные, а dashboard production-build собирается.
