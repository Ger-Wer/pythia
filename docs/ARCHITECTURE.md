# Архитектура Pythia

Дата актуализации: 2026-04-28

## Роль документа

Этот файл объясняет, как Pythia разрезана на runtime-контуры и где искать соответствующий код.

Если нужен общий статус проекта, начинайте с [docs/PYTHIA_V2_WORKSTREAM.md](./PYTHIA_V2_WORKSTREAM.md). Если нужен полный набор активных документов, начинайте с [docs/README.md](./README.md).

## Система в одном абзаце

Pythia - это Docker-first `decision desk` для Polymarket. Она каждый день ищет новые сигналы, превращает их в `signals` и paper/shadow lifecycle через `decision core`, отдельно сопровождает выбранные рынки через `watchlist engine`, а затем поднимает operator-facing triage-очередь через `attention engine`. Dashboard - главный интерфейс оператора. Telegram уже работает не только как outbound inbox, но и как inbound operator-command surface для attention/triage workflow.

## Главные runtime-контуры

### 1. Forecast stack

Задача:

- искать новые рынки;
- делать прогнозы;
- строить первичные `signals`.

Основные файлы:

- `src/main.ts`
- `src/services/market_selector.ts`
- `src/services/research_runner.ts`
- `src/services/naked_r1.ts`
- `src/prompts/synthesis.ts`

### 2. Decision core

Задача:

- превращать прогнозы в actionable `signals`;
- открывать и сопровождать paper/shadow позиции;
- хранить operator decisions, reviews и postmortems.

Сущности:

- `signals`
- `books`
- `decisions`
- `positions`
- `position_events`
- `reviews`
- `postmortems`

Основные файлы:

- `src/services/decision_core.ts`
- `src/services/db.ts` (primary, SQLite)
- `src/services/db_shadow.ts` (Dual-Loop Phase 1 Track D wrapper — fan-out в Postgres при `DUAL_LOOP_SHADOW_PG=true`; no-op иначе)
- `src/services/db_pg.ts` + `src/db/pg/schema.ts` (drizzle-orm адаптер для Postgres shadow store; см. `docs/dual_loop/db_migration.md`)
- `python/operator_core.py`

### 3. Autonomous watchlist engine

Задача:

- сопровождать рынки из `Избранного`;
- создавать `watch dossier`;
- запускать `watchlist refresh`;
- делать дешевую monitoring-проверку;
- запускать `deep review` только по триггерам;
- создавать короткие `watch updates`.

Сущности:

- `watch_dossiers`
- `watch_refresh_runs`
- `watch_refresh_snapshots`
- `watch_updates`

Основные файлы:

- `src/watchlist_refresh.ts`
- `src/services/watchlist_engine.ts`
- `python/watchlist_store.py`

### 4. Operator layer

Задача:

- строить `attention_items` поверх watched-рынков, due reviews и system degradation;
- отдавать данные для dashboard;
- обеспечивать whitelisted operator actions;
- отправлять в Telegram только `urgent/today` inbox-элементы.

Сущности:

- `attention_items`
- `attention_item_events`

Основные файлы:

- `python/attention_store.py`
- `python/dashboard_api.py`
- `python/dashboard_models.py`
- `python/operator_core.py`
- `python/telegram_bot.py`
- `dashboard/src/pages/*`

## Как идут данные

### Daily forecast

```text
Polymarket -> market selection -> research -> synthesis -> predictions
          -> decision core -> signals / shadow positions
          -> dashboard + Telegram alerts
```

### Watchlist refresh

```text
watch dossier -> current market snapshot -> monitoring mode
              -> optional deep review -> watch update
              -> attention item -> dashboard + Telegram inbox alert
```

### Resolution flow

```text
resolved market -> update prediction outcome -> close paper positions
                -> postmortem -> quality metrics
```

## Code map для нового разработчика

Если нужно быстро понять проект, читайте модули в таком порядке:

1. `src/main.ts`
2. `src/services/decision_core.ts`
3. `src/services/watchlist_engine.ts`
4. `src/watchlist_refresh.ts`
5. `python/operator_core.py`
6. `python/watchlist_store.py`
7. `python/dashboard_api.py`
8. `dashboard/src/pages/MarketDetailPage.tsx`
9. `dashboard/src/pages/WatchlistPage.tsx`

## Dashboard-модель

Текущий dashboard строится по принципу `decision-first`, но еще не стал полноценным command center.

Что уже есть:

- `Сводка` как command-center baseline
- `Качество`
- `Shadow performance`
- `Operator vs shadow`
- `Сигналы`
- dossier-first `Разбор рынка`
- `Избранное` как triage-board по `watch dossiers`
- `Действия`
- `attention_items` с triage-переходами в dashboard

Что еще впереди:

- weekly discipline/review loop поверх quality layer;
- еще более короткий operator workflow;
- real-time live Command Center feed для Dual-Loop preview route.

Текущее важное transitional замечание по Dual-Loop: inbound bus-события (`watchlist/signals/triggers/exec/kill_ack`) уже сведены к protobuf-first projection в `gateway-api`, но outbound operator-mutation path из `gateway-api` (`signals.decisions`, `control.kill`) пока еще живет как compatibility JSON envelope. Это не целевая архитектура, а промежуточная стадия миграции.

## Deployment topology

Production живет на `amper`.

Контейнеры:

- `pythia-scheduler`
- `pythia-telegram`
- `pythia-dashboard`

Важные свойства:

- deploy только под пользователем `pythia`;
- dashboard публикуется только на `127.0.0.1:8787`;
- наружу он отдается через reverse proxy;
- данные и логи лежат в bind-mounted `data/` и `logs/`.

## Расписание

- `forecast` - 06:00 UTC
- `watchlist refresh` - каждые 6 часов
- `watch alerts` - через 10 минут после каждого refresh
- `resolution` - 18:00 UTC
- `weekly report` - воскресенье, 20:00 UTC

## Границы и не-цели

Сейчас Pythia:

- не ставит реальные ордера;
- не хранит trading-ключи;
- не является автотрейдером;
- не открывает публичный backend без авторизации.

Это важно для чтения кода: foundation `attention engine` уже в production, inbound Telegram triage и quality pages уже есть в коде, а вот live execution-автоматизация и paper executor Fast Loop пока еще не production reality.
