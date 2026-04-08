# Pythia

Дата актуализации: 2026-04-08

Pythia - это приватный аналитический `decision desk` для Polymarket. Система ежедневно ищет новые сильные рынки, строит прогнозы, ведет `decision journal` и paper/shadow-позиции, а для рынков из `Избранного` запускает отдельный `watchlist refresh`, который сам отслеживает изменения тезиса, цены и новостей и поднимает оператору только важные обновления.

## Что важно понять сразу

Если вы новый разработчик, начните с трех фактов:

1. `Pythia` уже не просто forecast-пайплайн. Сейчас это связка `forecast stack + decision core + autonomous watchlist engine + dashboard + Telegram`.
2. В production пока нет live-trading. Система работает в paper/shadow режиме и копит валидный decision dataset.
3. Главная цель проекта - не "писать reasoning от LLM", а уменьшать когнитивную нагрузку оператора и улучшать качество решений на реальных данных Polymarket.

## Что уже реально работает в production

- daily `forecast` на всем рынке Polymarket;
- `Research+R1` и `Naked R1`;
- отбор рынков с дедупликацией и diversity constraints;
- `decision core`:
  - `signals`
  - `decisions`
  - `positions`
  - `reviews`
  - `postmortems`
- paper/shadow lifecycle без реальных ордеров;
- отдельный `watchlist refresh` каждые 6 часов;
- `watch dossiers` и `watch updates`;
- `attention_items` и triage-очередь `urgent / today / later`;
- `Сводка` как command-center baseline;
- `Избранное` как triage-board по watched-рынкам;
- dashboard как основной интерфейс оператора;
- Telegram как outbound inbox для `urgent/today` attention updates и fallback-команд.

## Что еще не закончено

Следующие большие куски еще впереди:

- inbound Telegram triage;
- decision-quality метрики поверх operator/shadow performance;
- любой live execution через Polymarket API.

Это важно: если в старых design-docs что-то выглядит "готовым", а в этом README или `WORKSTREAM` это отмечено как будущее, верить нужно текущему `WORKSTREAM`.

## Как система устроена сейчас

```text
Polymarket Gamma API
  -> daily forecast
  -> Research+R1 / Naked R1
  -> predictions + signals
  -> decision core
  -> autonomous watchlist engine
  -> SQLite + logs + Brier/PnL
  -> Dashboard API + Telegram alerts
```

### 1. Daily forecast

Раз в день Pythia:

- выбирает рынки;
- запускает research;
- строит прогнозы;
- создает `signals`;
- при сильных правилах может открыть `shadow` paper-position.

Точка входа в коде:

- `src/main.ts`
- `src/services/market_selector.ts`
- `src/services/research_runner.ts`
- `src/services/naked_r1.ts`
- `src/services/decision_core.ts`

### 2. Decision core

Это слой действий и paper lifecycle.

Он отвечает за:

- создание actionable `signals`;
- manual/shadow `decisions`;
- `positions`;
- `reviews`;
- `postmortems`.

Главные файлы:

- `src/services/decision_core.ts`
- `src/services/db.ts`
- `python/operator_core.py`

### 3. Autonomous Watchlist Engine

Это отдельный мотор сопровождения рынков из `Избранного`.

Он:

- создает `watch dossier` при добавлении рынка в `Избранное`;
- переиспользует свежий forecast snapshot или ставит рынок в `pending_deep_review`;
- каждые 6 часов запускает `watchlist refresh`;
- сначала делает дешевую monitoring-проверку;
- запускает `deep review` только по триггерам;
- пишет `watch updates` для dashboard и Telegram.

Главные файлы:

- `src/watchlist_refresh.ts`
- `src/services/watchlist_engine.ts`
- `python/watchlist_store.py`

### 4. Attention + operator layer

Dashboard - главный интерфейс оператора.

`attention engine` поднимает operator-facing triage-объекты из `watch updates`, `reviews` и system degradation. Dashboard показывает полную очередь внимания. Telegram рассылает только `urgent/today`.

Главные файлы:

- `python/attention_store.py`
- `python/dashboard_api.py`
- `python/dashboard_models.py`
- `python/operator_core.py`
- `python/telegram_bot.py`
- `dashboard/src/pages/OverviewPage.tsx`
- `dashboard/src/pages/LatestRunPage.tsx`
- `dashboard/src/pages/MarketDetailPage.tsx`
- `dashboard/src/pages/WatchlistPage.tsx`

## Какой код читать новому разработчику

Лучший маршрут входа за первые 30 минут такой:

1. Прочитать [docs/PYTHIA_V2_WORKSTREAM.md](/Users/gerwer/Desktop/Pythia/docs/PYTHIA_V2_WORKSTREAM.md)
2. Прочитать [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md)
3. Открыть:
   - `src/main.ts`
   - `src/services/decision_core.ts`
   - `src/services/watchlist_engine.ts`
   - `src/watchlist_refresh.ts`
   - `python/operator_core.py`
   - `python/watchlist_store.py`
4. После этого уже смотреть dashboard pages.

Если нужен внешний взгляд на текущее состояние production, читайте [docs/AUDIT.md](/Users/gerwer/Desktop/Pythia/docs/AUDIT.md).

## Как думать о документации

Порядок доверия такой:

1. [docs/PYTHIA_V2_WORKSTREAM.md](/Users/gerwer/Desktop/Pythia/docs/PYTHIA_V2_WORKSTREAM.md)
2. Этот `README`
3. [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md)
4. [docs/DEPLOYMENT.md](/Users/gerwer/Desktop/Pythia/docs/DEPLOYMENT.md)
5. [docs/AUDIT.md](/Users/gerwer/Desktop/Pythia/docs/AUDIT.md)
6. design/spec docs в `docs/superpowers/specs/`

Исторические spec-документы полезны для понимания намерений, но они не равны текущему production-state.

## Быстрый запуск на `amper`

Работайте под пользователем `pythia`.

```bash
cd /home/pythia/pythia
cp .env.example .env
bash scripts/day0_check.sh
docker compose build
bash scripts/manual_run.sh forecast
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
docker compose up -d
```

Подробный production-runbook лежит в [docs/DEPLOYMENT.md](/Users/gerwer/Desktop/Pythia/docs/DEPLOYMENT.md).

## Ежедневная эксплуатация

Главная логика сейчас такая:

- `Сводка` показывает очередь внимания и ближайшие действия;
- `Сигналы` дают новые идеи;
- `Разбор рынка` показывает состояние тезиса, triage и следующее действие;
- `Избранное` стало triage-board контуром сопровождения, а не списком `market_id`;
- Telegram нужен для кратких `urgent/today` alerts, а не для чтения полных разборов.

Ручные команды:

- `bash scripts/manual_run.sh status`
- `bash scripts/manual_run.sh forecast`
- `bash scripts/manual_run.sh watchlist-refresh`
- `bash scripts/manual_run.sh watch-alerts`
- `bash scripts/manual_run.sh logs`

## Расписание

`supercronic` сейчас запускает:

- `forecast` - 06:00 UTC
- `watchlist refresh` - каждые 6 часов
- `watch alerts` - через 10 минут после каждого refresh
- `resolution` - 18:00 UTC
- `weekly report` - воскресенье, 20:00 UTC

## Что не надо делать

- не хранить реальные trading-ключи на сервере;
- не превращать dashboard в dump фактов и ссылок;
- не путать `watchlist engine` с `daily forecast`;
- не считать старые spec docs источником текущей правды;
- не воспринимать LLM как "магический источник alpha".

## Карта документов

- [docs/PYTHIA_V2_WORKSTREAM.md](/Users/gerwer/Desktop/Pythia/docs/PYTHIA_V2_WORKSTREAM.md) - главный source of truth по фазе проекта и roadmap
- [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md) - как система разрезана на runtime-контуры и модули
- [docs/DEPLOYMENT.md](/Users/gerwer/Desktop/Pythia/docs/DEPLOYMENT.md) - как деплоить и обновлять production на `amper`
- [docs/AUDIT.md](/Users/gerwer/Desktop/Pythia/docs/AUDIT.md) - внешний аудит текущего production-state
- [docs/DECISION_LOG.md](/Users/gerwer/Desktop/Pythia/docs/DECISION_LOG.md) - ключевые архитектурные и продуктовые решения

## Текущий критерий успеха

На этом этапе мы движемся правильно, если:

- paper/shadow-контур копит валидный track record;
- `Избранное` реально снижает ручной follow-up;
- Telegram поднимает только важные изменения;
- новые улучшения можно проверять на реальных paper-результатах, а не на догадках.
