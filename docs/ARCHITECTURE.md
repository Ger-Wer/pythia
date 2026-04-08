# Архитектура Pythia

Дата актуализации: 2026-04-08

## Роль документа

Этот файл объясняет, как Pythia разрезана на runtime-контуры и где искать соответствующий код.

Если нужен общий статус проекта, начинайте с [docs/PYTHIA_V2_WORKSTREAM.md](/Users/gerwer/Desktop/Pythia/docs/PYTHIA_V2_WORKSTREAM.md). Если нужен production-state для внешнего человека, смотрите [docs/AUDIT.md](/Users/gerwer/Desktop/Pythia/docs/AUDIT.md).

## Система в одном абзаце

Pythia - это Docker-first `decision desk` для Polymarket. Она каждый день ищет новые сигналы, превращает их в `signals` и paper/shadow lifecycle через `decision core`, отдельно сопровождает выбранные рынки через `watchlist engine`, а затем поднимает operator-facing triage-очередь через `attention engine`. Dashboard - главный интерфейс оператора. Telegram - outbound inbox и fallback-команды.

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
- `src/services/db.ts`
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
- `Сигналы`
- dossier-first `Разбор рынка`
- `Избранное` как triage-board по `watch dossiers`
- `Действия`
- `attention_items` с triage-переходами в dashboard

Что еще впереди:

- inbound Telegram triage;
- еще более короткий operator workflow.

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

Это важно для чтения кода: foundation `attention engine` уже в production, но inbound Telegram triage, decision-quality слой и любая live execution-автоматизация еще не production reality.
