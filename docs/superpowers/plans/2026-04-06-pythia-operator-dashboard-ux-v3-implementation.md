# Pythia Operator Dashboard UX v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Снизить когнитивную нагрузку оператора: сделать `Сводку` центром принятия решений, превратить `Health` в реальную диагностику, упростить `Сигналы`, сжать `Разбор рынка` до 4 смысловых блоков и довести tooltip-систему до устойчивого и понятного состояния.

**Architecture:** Меняем только операторский слой: FastAPI `operator_core/dashboard_api` расширяет summary-first контракт, React dashboard перестраивает только верхние сценарии `Overview / Signals / Market Detail / Tooltips`, а forecasting pipeline, auth-модель и docker-layout не трогаются. Реализация идёт TDD-итерациями: сначала backend-контракт и тесты, потом UI-компоненты, затем контент и документация.

**Tech Stack:** React + TypeScript + Vitest + Testing Library, FastAPI + unittest, SQLite-backed `operator_core`, существующий Docker-first deployment.

---

### Task 1: Реальный Health и action-ready Overview contract

**Files:**
- Modify: `python/operator_core.py`
- Modify: `python/dashboard_models.py`
- Modify: `python/test_operator_core.py`
- Modify: `python/test_dashboard_api.py`
- Modify: `dashboard/src/types/api.ts`

- [ ] **Step 1: Написать падающие backend-тесты для health/overview**

Добавить проверки, что:
- `get_health()` возвращает реальные компоненты: `sqlite`, `llm`, `gamma`, `exa`, `tavily`, `scheduler`, `telegram`;
- `get_health()` умеет выставлять `ON / DEGRADED / OFF` и человекочитаемые `detail`;
- `get_overview()` возвращает один `next_step` и массив `attention_items` с `title`, `detail`, `market_id?`, `action_label?`.

Run:
```bash
python/venv/bin/python -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected: `FAIL` на новых ожиданиях contract.

- [ ] **Step 2: Реализовать минимальный backend-контракт**

В `python/operator_core.py`:
- добавить helper для чтения env/конфигурации внешних зависимостей;
- собрать `checks` по наличию ключей и доступности локальных runtime-артефактов;
- формировать `attention_items` из research-status, пустого прогона, сильных сигналов и пустого watchlist;
- формировать `next_step` одной фразой для оператора.

В `python/dashboard_models.py`:
- расширить `OverviewResponse` и `HealthResponse` под новые поля.

- [ ] **Step 3: Синхронизировать frontend-типы**

Обновить `dashboard/src/types/api.ts`, чтобы появились:
- `attention_items`
- `next_step`
- расширенный `HealthCheckItem`
- `HealthResponse` с новыми полями и деталями.

- [ ] **Step 4: Прогнать backend-тесты до зелёного**

Run:
```bash
python/venv/bin/python -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add python/operator_core.py python/dashboard_models.py python/test_operator_core.py python/test_dashboard_api.py dashboard/src/types/api.ts
git commit -m "feat: enrich operator health and overview contract"
```

### Task 2: Пересобрать `Сводку` в decision-first экран

**Files:**
- Modify: `dashboard/src/pages/OverviewPage.tsx`
- Modify: `dashboard/src/components/StatusPills.tsx`
- Modify: `dashboard/src/components/ActionSummaryCard.tsx`
- Modify: `dashboard/src/components/KpiCard.tsx`
- Modify: `dashboard/src/__tests__/OverviewPage.test.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий frontend-тест для нового Overview**

Добавить ожидания, что `OverviewPage` показывает:
- короткий `next_step`;
- `3` главных сигнала максимум;
- отдельный блок `Что делать сейчас`;
- быстрые действия `Запустить прогноз`, `Проверить resolved`, `Обновить статус`;
- явный research/system summary в hero.

Run:
```bash
cd dashboard && npm test -- OverviewPage.test.tsx
```

Expected: `FAIL`.

- [ ] **Step 2: Реализовать новый layout Overview**

В `OverviewPage.tsx`:
- вывести `next_step`;
- заменить общий список attention на карточки внимания;
- показать максимум `3` сигнала;
- добавить safe quick actions;
- оставить KPI, но сжать их до операторски-полезных.

В `StatusPills.tsx`:
- сделать подписи короче и более человеческими.

В `ActionSummaryCard.tsx`:
- усилить human-summary-first подачу.

- [ ] **Step 3: Довести стили**

В `styles.css`:
- оформить attention-карточки;
- оформить quick actions;
- сделать hero визуально более “одно решение на экран”.

- [ ] **Step 4: Прогнать Overview-тест**

Run:
```bash
cd dashboard && npm test -- OverviewPage.test.tsx
```

Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/OverviewPage.tsx dashboard/src/components/StatusPills.tsx dashboard/src/components/ActionSummaryCard.tsx dashboard/src/components/KpiCard.tsx dashboard/src/__tests__/OverviewPage.test.tsx dashboard/src/styles.css
git commit -m "feat: make overview decision-first"
```

### Task 3: Упростить `Сигналы` до рабочего списка решений

**Files:**
- Modify: `dashboard/src/pages/LatestRunPage.tsx`
- Modify: `dashboard/src/components/SignalList.tsx`
- Modify: `dashboard/src/utils/format.ts`
- Modify: `dashboard/src/__tests__/LatestRunPage.test.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий тест для фильтров и короткого reasoning**

Тест должен проверять:
- toggle `Только сильные`;
- toggle `Только избранное`;
- каждая карточка сигнала показывает короткую строку `Почему важно`;
- список остаётся кликабельным в `Разбор` и `Избранное`.

Run:
```bash
cd dashboard && npm test -- LatestRunPage.test.tsx
```

Expected: `FAIL`.

- [ ] **Step 2: Реализовать фильтры и сокращённую карточку**

В `LatestRunPage.tsx`:
- добавить локальные фильтры;
- считать `Почему важно` из edge/confidence/research_status;
- по умолчанию сортировать по силе сигнала.

В `SignalList.tsx`:
- сократить вторичную мета-информацию;
- вывести короткую операторскую подпись причины.

- [ ] **Step 3: Прогнать тест**

Run:
```bash
cd dashboard && npm test -- LatestRunPage.test.tsx
```

Expected: `PASS`.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/LatestRunPage.tsx dashboard/src/components/SignalList.tsx dashboard/src/utils/format.ts dashboard/src/__tests__/LatestRunPage.test.tsx dashboard/src/styles.css
git commit -m "feat: streamline dashboard signals workflow"
```

### Task 4: Сжать `Разбор рынка` до 4 смысловых блоков

**Files:**
- Modify: `dashboard/src/pages/MarketDetailPage.tsx`
- Modify: `dashboard/src/components/TimelinePanel.tsx`
- Modify: `dashboard/src/__tests__/MarketDetailPage.test.tsx`
- Modify: `dashboard/src/styles.css`

- [ ] **Step 1: Написать падающий тест для четырёх блоков**

Тест должен требовать на экране:
- `Что думает Pythia`
- `Почему расходится с рынком`
- `Что может сломать прогноз`
- `Что отслеживать дальше`

И отдельно проверять, что `источники` и `таймлайн` остаются, но уходят ниже по приоритету.

Run:
```bash
cd dashboard && npm test -- MarketDetailPage.test.tsx
```

Expected: `FAIL`.

- [ ] **Step 2: Реализовать narrative layout**

В `MarketDetailPage.tsx`:
- переименовать блоки;
- собрать финальный блок `Что отслеживать дальше` из `disconfirming_signal`, `second_order_effects`, watch-action;
- визуально опустить `Research brief` и `Timeline`.

- [ ] **Step 3: Прогнать тест**

Run:
```bash
cd dashboard && npm test -- MarketDetailPage.test.tsx
```

Expected: `PASS`.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/MarketDetailPage.tsx dashboard/src/components/TimelinePanel.tsx dashboard/src/__tests__/MarketDetailPage.test.tsx dashboard/src/styles.css
git commit -m "feat: compress market detail into operator narrative"
```

### Task 5: Довести tooltip-систему и документацию

**Files:**
- Modify: `dashboard/src/components/InfoTooltip.tsx`
- Modify: `dashboard/src/content/tooltips.ts`
- Modify: `dashboard/src/__tests__/InfoTooltip.test.tsx`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/AUDIT.md`

- [ ] **Step 1: Написать падающий тест на “одна подсказка открыта одновременно”**

Добавить тест, который:
- открывает первую подсказку;
- затем открывает вторую;
- проверяет, что первая закрылась;
- проверяет закрытие по outside tap/click.

Run:
```bash
cd dashboard && npm test -- InfoTooltip.test.tsx
```

Expected: `FAIL`.

- [ ] **Step 2: Реализовать coordinator для tooltip**

В `InfoTooltip.tsx`:
- добавить координацию через `CustomEvent` или общий lightweight channel;
- гарантировать, что открыта только одна подсказка;
- сохранить уже исправленную логику закрытия.

В `tooltips.ts`:
- переписать тексты по шаблону `Что это / Почему важно / Как читать`.

- [ ] **Step 3: Обновить документацию**

В `README.md`, `docs/ARCHITECTURE.md`, `docs/AUDIT.md`:
- зафиксировать `UX v3`;
- указать, что dashboard — primary operator surface;
- описать real health и tooltip-guided interpretation.

- [ ] **Step 4: Полная верификация**

Run:
```bash
cd dashboard && npm test
cd dashboard && npm run build
python/venv/bin/python -m unittest python/test_operator_core.py python/test_dashboard_api.py -v
```

Expected:
- frontend tests `PASS`
- dashboard build `PASS`
- backend tests `OK`

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/InfoTooltip.tsx dashboard/src/content/tooltips.ts dashboard/src/__tests__/InfoTooltip.test.tsx README.md docs/ARCHITECTURE.md docs/AUDIT.md
git commit -m "feat: finalize dashboard ux v3 guidance"
```

### Task 6: Исследование `oraclemarkets.io` и выводы для Pythia

**Files:**
- Create: `docs/ORACLEMARKETS_RESEARCH.md`

- [ ] **Step 1: Провести внешнее исследование**

Собрать материал по:
- продуктовой модели `oraclemarkets.io`;
- ключевым UX/операторским сценариям;
- что у них решено лучше, чем у Pythia;
- что не подходит нашему стеку/масштабу.

- [ ] **Step 2: Оформить письменный вывод**

В `docs/ORACLEMARKETS_RESEARCH.md` зафиксировать:
- что можно внедрить;
- что не нужно внедрять;
- что стоит отложить до post-MVP.

- [ ] **Step 3: Commit**

```bash
git add docs/ORACLEMARKETS_RESEARCH.md
git commit -m "docs: add oraclemarkets product research"
```

