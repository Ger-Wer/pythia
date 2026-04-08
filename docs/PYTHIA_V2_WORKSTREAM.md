# Pythia v2 — Workstream и дорожная карта

Дата актуализации: 2026-04-08

## Роль этого документа

Это главный рабочий `source of truth` по текущему состоянию `Pythia v2`.

Если между кодом, dashboard, старыми spec-docs и заметками есть расхождения, ориентироваться нужно в таком порядке:

1. этот документ;
2. [README.md](/Users/gerwer/Desktop/Pythia/README.md);
3. [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md);
4. [docs/DEPLOYMENT.md](/Users/gerwer/Desktop/Pythia/docs/DEPLOYMENT.md);
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

- `Phase 0` завершена
- `Phase 1` завершена
- `Phase 2` реализована в baseline-варианте и уже в production
- `Phase 3` начата частично через dossier-first UX, но не завершена
- `Phase 4+` еще впереди

Коротко:

- forecast работает;
- decision core работает;
- autonomous watchlist engine уже в production;
- operator UX еще нужно упростить и довести до command-center модели;
- attention engine и decision-quality layer еще не построены.

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
- [x] `Главные сигналы дня`
- [x] dossier-first `Разбор рынка`
- [x] `Избранное` как board по `watch dossier`
- [x] tooltip/glossary слой

## Что еще не закончено

### Product / UX

- [ ] `Сводка` как полноценный command center
- [ ] `Избранное` как главный рабочий экран сопровождения
- [ ] еще сильнее сократить шум в `Разборе рынка`
- [ ] отделить "сейчас важно" от "полезно для глубокой проверки"

### Alerting / attention

- [ ] сущность `attention_item`
- [ ] статусы `urgent / today / later / in_progress / closed`
- [ ] Telegram triage inbox
- [ ] связка `attention item -> dashboard workspace`

### Quality / validation

- [ ] метрики shadow performance
- [ ] сравнение `operator mode` vs `shadow mode`
- [ ] review discipline metrics
- [ ] weekly decision review
- [ ] decision-quality dashboard

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
- [x] live execution через Polymarket API сейчас не входит в scope

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
- [ ] attention items

### Phase 3 — Operator Workflow Redesign

- [ ] `Сводка` как command center
- [ ] `Избранное` как основной рабочий экран
- [ ] оператор видит только:
  - [ ] что изменилось
  - [ ] почему это важно
  - [ ] что делать
  - [ ] когда review
- [ ] тяжелые факты и источники по умолчанию спрятаны во второй слой

### Phase 4 — Attention Engine + Telegram Inbox

- [ ] единый `attention_item`
- [ ] приоритизация alerts
- [ ] Telegram triage flow
- [ ] единый дневной digest

### Phase 5 — Decision Quality Layer

- [ ] shadow performance dashboard
- [ ] operator vs shadow comparison
- [ ] discipline metrics
- [ ] post-mortem taxonomy
- [ ] weekly decision review

### Phase 6 — Adaptive Improvement Layer

- [ ] использовать накопленный dataset для улучшения ranking / prompts / heuristics
- [ ] только после валидного paper-track-record обсуждать следующую ступень автоматизации

## Что сейчас делать новому разработчику

Если вы входите в проект прямо сейчас, лучший порядок такой:

1. Прочитать [README.md](/Users/gerwer/Desktop/Pythia/README.md)
2. Прочитать [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md)
3. Понять эти модули:
   - `src/main.ts`
   - `src/services/decision_core.ts`
   - `src/services/watchlist_engine.ts`
   - `src/watchlist_refresh.ts`
   - `python/operator_core.py`
   - `python/watchlist_store.py`
4. Потом уже смотреть dashboard pages и Telegram delivery

## Главный текущий фокус

Самая важная следующая задача сейчас:

- [ ] довести operator UX от "умной панели" до "command center"

Практически это означает:

- `Сводка` должна стать экраном принятия решений;
- `Избранное` должно стать рабочей доской сопровождения;
- operator должен тратить меньше времени на чтение и больше - на осмысленные решения;
- attention layer должен поднять только действительно важные изменения.

## Критерии того, что мы движемся правильно

- [ ] оператор перестает вручную перепроверять все watchlist-рынки;
- [ ] `Избранное` реально экономит внимание;
- [ ] Telegram поднимает только значимые изменения;
- [ ] paper/shadow контур копит валидный track record;
- [ ] улучшения промптов и алгоритмов можно проверять на реальных paper-результатах.
