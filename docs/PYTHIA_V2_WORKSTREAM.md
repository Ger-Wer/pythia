# Pythia v2 — Workstream и дорожная карта

Дата актуализации: 2026-04-29 (Paper execution + live Command Center feed + real mempool dev proof)

## Роль этого документа

Это главный рабочий `source of truth` по текущему состоянию `Pythia v2`.

Если между кодом, dashboard, старыми spec-docs и заметками есть расхождения, ориентироваться нужно в таком порядке:

1. этот документ;
2. [README.md](../README.md);
3. [docs/ARCHITECTURE.md](./ARCHITECTURE.md);
4. [docs/DEPLOYMENT.md](./DEPLOYMENT.md);
5. исторические spec-docs в `docs/superpowers/specs/`.

## Что такое Pythia v2

`Pythia v2` - это не просто forecast dashboard.

Это:

- автономный аналитический комбайн;
- operator decision desk;
- paper/shadow trading среда;
- система сопровождения тезисов;
- журнал решений, review и post-mortem;
- база для улучшения алгоритмов, промптов и decision workflow.

## Финальная цель

Построить систему, которая:

- находит новые сильные сигналы;
- сама сопровождает выбранные рынки;
- помогает оператору принимать дисциплинированные решения;
- дает устойчивый положительный paper/shadow track record;
- накапливает валидный dataset для дальнейшего улучшения.

## Где мы находимся сейчас

Стадия проекта на сегодня:

- `Phase 0` завершена.
- `Phase 1` завершена.
- `Phase 2` работает в production как forecast + decision core + watchlist loop.
- `Phase 3` реализована как dashboard command-center baseline.
- `Phase 4` реализована как attention engine + Telegram operator loop.
- `Phase 5` начата: quality pages и API уже в коде, но weekly discipline loop еще не закрыт.
- Параллельно открыт отдельный **Dual-Loop** workstream: foundations уже пройдены, paper execution в dev-контуре доказан, и проект вошел в release-hardening стадию.

Коротко:

- forecast работает;
- decision core работает;
- autonomous watchlist engine уже в production;
- attention engine и inbound Telegram triage уже есть в коде;
- quality layer больше не "пустое место": есть `/quality`, `shadow performance`, `operator vs shadow`;
- Fast Loop уже вышел из чистого design: bus/contracts, `gateway-api`, живой Command Center, `sniper-ingest`, `sniper-risk` и paper-mode `sniper-executor` существуют в `main`;
- реальный mempool path в dev уже доказан через `Anvil WS -> calldata decode -> market_specific_key match -> trigger -> paper fill -> gateway state`;
- первый релиз проекта нужно понимать как `Paper Operator Release`, а не как запуск live-capital execution.

## Уже доставлено в production

### База системы

- [x] Docker-first deploy на `amper`
- [x] Dashboard как основной интерфейс
- [x] Telegram как alert/fallback канал
- [x] Daily forecast
- [x] Research+R1 и Naked R1
- [x] Дедупликация рынков и diversity constraints

### Decision core

- [x] `signals`
- [x] `books`
- [x] `decisions`
- [x] `positions`
- [x] `position_events`
- [x] `reviews`
- [x] `postmortems`
- [x] auto-shadow decision / position
- [x] manual decision API
- [x] manual close / review completion API

### Autonomous watchlist engine

- [x] `watch dossier`
- [x] `initial thesis snapshot`
- [x] `review deadline`
- [x] отдельный `watchlist refresh`
- [x] monitoring mode
- [x] deep review по триггерам
- [x] `watch_refresh_runs`
- [x] `watch_refresh_snapshots`
- [x] `watch_updates`
- [x] Telegram delivery для `watch_updates`

### Dashboard UX

- [x] `Качество`
- [x] `Shadow performance`
- [x] `Operator vs shadow`
- [x] `Главные сигналы дня`
- [x] dossier-first `Разбор рынка`
- [x] `Избранное` как triage-board по `watch dossier`
- [x] `Сводка` как command-center baseline
- [x] tooltip/glossary слой
- [x] Phase 1 UX improvements (Toast, Keyboard shortcuts, Onboarding, Loading states)
- [x] Phase 2 UX improvements (WCAG 2.1 AA compliance, colorblind-friendly palette, data-ink ratio optimization)

### Attention engine

- [x] `attention_items`
- [x] `attention_item_events`
- [x] статусы `urgent / today / later / in_progress / closed`
- [x] dashboard triage transitions
- [x] Telegram outbound inbox для `urgent/today`
- [x] inbound Telegram triage / transitions (`/inbox`, `/done`, `/today`, `/snooze`, aliases)

### Dual-Loop foundations in `main`

- [x] protobuf contracts `schemas/dual_loop/v1/*`
- [x] fixed-point wire format (`*_ppm`, `*_bps`, `*_unix_ms`; no financial `double`)
- [x] canonical bus topics `dl.v1.*`
- [x] `gateway-api` foundation
- [x] inbound protobuf bridge в `gateway-api` (с JSON fallback для тестов/legacy)
- [x] outbound protobuf-first publish path в `gateway-api` для `signals.decisions` и `control.kill` (с JSON fallback при отсутствии codecs)
- [x] Command Center route `/command-center`
- [x] `sniper-ingest` foundation + matcher + replay/test surface
- [x] `sniper-risk` pre-trade checks foundation
- [x] live gateway WebSocket feed в Command Center
- [x] `sniper-executor` paper lifecycle foundation в коде: submit/fill/revert, nonce actor, stuck-timeout, `control.kill` consumer/ack
- [x] real mempool dev proof через Anvil + UMA adapter decode + `market_specific_key`
- [x] decision: `phase1b-pg-listen-notify` не влит в `main`; канонический путь остается через Redis/NATS bus, а не через side-channel LISTEN/NOTIFY

## Что еще не закончено

### Product / UX

- [x] `Сводка` как command-center baseline
- [x] `Избранное` как главный рабочий экран сопровождения
- [x] отделить "сейчас важно" от "полезно для глубокой проверки"
- [ ] еще сильнее сократить шум в `Разборе рынка`
- [ ] weekly workflow поверх command center

### Alerting / attention

- [x] сущность `attention_item`
- [x] статусы `urgent / today / later / in_progress / closed`
- [x] связка `attention item -> dashboard workspace`
- [x] outbound Telegram inbox
- [x] inbound Telegram triage
- [ ] единый дневной digest по attention queue

### Quality / validation

- [x] quality API `/api/quality`
- [x] shadow performance page
- [x] operator vs shadow page
- [x] decision-quality dashboard foundation
- [ ] review discipline metrics
- [ ] weekly decision review
- [ ] automation поверх quality / review loop

### Dual-Loop / Fast Loop

- [x] bus/contracts foundation
- [x] `gateway-api` foundation
- [x] Command Center UI с live gateway feed
- [x] `sniper-ingest`
- [x] `sniper-risk`
- [ ] убрать residual JSON fallback в `gateway-api` publish-side (`signals.decisions`, `control.kill`) и довести весь control-plane до одного protobuf пути без compatibility режима
- [ ] автоматическая прокидка `market_specific_key` из Slow Loop producer path во все watchlist publish flows
- [ ] `services/market-data` / order book tracker для реального edge-quality контура
- [ ] release-hardening: secret rotation, stable shadow/audit persistence, 72h paper soak

### Improvement loop

- [ ] использовать накопленный dataset для signal ranking
- [ ] улучшать review triggers
- [ ] улучшать prompts по paper-результатам
- [ ] улучшать decision heuristics
- [ ] только потом думать о локальной policy/model layer

## Принятые продуктовые решения

Эти решения уже считаются зафиксированными:

- [x] Pythia = `decision desk`, а не просто forecast UI
- [x] главная единица ценности = lifecycle решения
- [x] paper/shadow trading обязателен
- [x] `Forecast run` и `Watchlist refresh` - разные контуры
- [x] LLM работает в двух режимах:
  - [x] `monitoring mode`
  - [x] `deep review mode`
- [x] `Избранное` = активный контур сопровождения
- [x] Telegram = inbox и alert-канал, а не основной экран чтения анализа
- [x] live execution не входит в текущий production scope Pythia v2 и живет как отдельная цель Dual-Loop Phase 3

## Дорожная карта по фазам

### Phase 0 — Stabilize production base

- [x] не ломать forecast stack
- [x] не ломать dashboard auth и deploy
- [x] не ломать quality layer

### Phase 1 — Decision Journal + Shadow Trading Core

- [x] decision schema
- [x] signal generation
- [x] shadow positions
- [x] manual operator decisions
- [x] reviews
- [x] postmortems

### Phase 2 — Autonomous Watchlist Engine

- [x] dossier bootstrap
- [x] refresh runtime
- [x] monitoring mode
- [x] deep review triggers
- [x] watch updates
- [x] Telegram delivery
- [x] attention items foundation

### Phase 3 — Operator Workflow Redesign

- [x] `Сводка` как command-center baseline
- [x] `Избранное` как основной рабочий экран
- [x] оператор видит только:
  - [x] что изменилось
  - [x] почему это важно
  - [x] что делать
  - [x] когда review
- [x] тяжелые факты и источники по умолчанию спрятаны во второй слой
- [x] WCAG 2.1 AA compliance (2026-04-13):
  - [x] colorblind-friendly palette (blue/orange вместо red/green)
  - [x] visual icons для status indicators (✓ ⚠ ✕)
  - [x] data-ink ratio optimization в графиках
- [ ] еще сильнее сократить шум в `Разборе рынка`
- [ ] weekly workflow поверх command center

### Phase 4 — Attention Engine + Telegram Inbox

- [x] единый `attention_item`
- [x] приоритизация alerts
- [x] outbound Telegram inbox
- [x] inbound Telegram triage flow
- [ ] единый дневной digest

### Phase 5 — Decision Quality Layer

- [x] shadow performance dashboard
- [x] operator vs shadow comparison
- [x] quality API foundation
- [ ] discipline metrics
- [ ] post-mortem taxonomy
- [ ] weekly decision review

### Phase 6 — Adaptive Improvement Layer

- [ ] использовать накопленный dataset для улучшения ranking / prompts / heuristics
- [ ] только после валидного paper-track-record обсуждать следующую ступень автоматизации

## Что сейчас делать новому разработчику

Если вы входите в проект прямо сейчас, лучший порядок такой:

1. Прочитать [README.md](../README.md)
2. Прочитать [docs/ARCHITECTURE.md](./ARCHITECTURE.md)
3. Понять эти модули:
   - `src/main.ts`
   - `src/services/decision_core.ts`
   - `src/services/watchlist_engine.ts`
   - `src/watchlist_refresh.ts`
   - `python/operator_core.py`
   - `python/watchlist_store.py`
4. Потом уже смотреть:
   - `python/operator_actions.py`
   - `python/dashboard_api.py`
   - `dashboard/src/pages/*`
   - `gateway-api/src/*`
   - `services/sniper-ingest/*`
   - `services/sniper-risk/*`
   - `services/sniper-executor/*`

## Главный текущий фокус

Самая важная следующая задача сейчас:

- [ ] превратить доказанный paper contour в воспроизводимый release-контур без server-only drift и без слабых operational defaults

Практически это означает:

- зафиксировать текущий real-mempool progress в чистой Git history и канонических docs;
- закрыть protobuf-first control plane и producer-path для `market_specific_key`;
- прогнать soak/latency/replay gates для первого paper release;
- параллельно не потерять weekly quality/review loop в Slow Loop.

## Критерии того, что мы движемся правильно

- [ ] оператор перестает вручную перепроверять все watchlist-рынки;
- [ ] `Избранное` реально экономит внимание;
- [ ] Telegram поднимает только значимые изменения;
- [ ] paper/shadow контур копит валидный track record;
- [ ] улучшения промптов и алгоритмов можно проверять на реальных paper-результатах.
