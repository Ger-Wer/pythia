# Pythia v2 — Workstream и дорожная карта

Дата актуализации: 2026-04-07

## Назначение

Этот документ — главный рабочий source of truth по текущему курсу `Pythia v2`.

Если в старых design-docs, dashboard-экранах или экспериментальном коде есть расхождения, опираться нужно в таком порядке:

1. этот документ;
2. [2026-04-07-pythia-v2-decision-desk-design.md](/Users/gerwer/Desktop/Pythia/docs/superpowers/specs/2026-04-07-pythia-v2-decision-desk-design.md);
3. актуальные implementation plans;
4. остальная документация.

## Что такое Pythia v2

`Pythia v2` — это не просто система прогнозов для Polymarket.

Это:

- автономный аналитический комбайн;
- operator decision desk;
- paper/shadow trading среда;
- система сопровождения тезисов;
- журнал решений, review и post-mortem;
- база для дальнейшего улучшения алгоритмов, промптов и decision layer.

## Финальная цель

Построить живую и обучаемую систему, которая:

- находит новые сильные сигналы;
- сама сопровождает выбранные рынки;
- помогает оператору принимать дисциплинированные решения;
- даёт стабильный положительный paper-track-record;
- накапливает валидный decision dataset для будущего улучшения моделей и правил.

## Что НЕ является целью сейчас

- [ ] Автоматическое размещение реальных ставок через Polymarket API
- [ ] Хранение trading-ключей на сервере
- [ ] Автотрейдинг без оператора
- [ ] Перегрузка интерфейса reasoning-текстами и сырыми фактами
- [ ] Превращение LLM в “магический источник alpha”

## Уже принятые продуктовые решения

### Базовая модель продукта

- [x] Pythia = decision desk, а не просто forecast dashboard
- [x] Главная единица ценности = lifecycle решения, а не отдельный прогноз
- [x] Успех системы измеряется в двух слоях:
  - [x] качество прогнозов
  - [x] качество решений оператора
- [x] Pythia должна давать не только тезис, но и ценовую дисциплину

### Контуры работы

- [x] Есть `operator mode`
- [x] Есть `shadow mode`
- [x] Один рынок и позиция — разные сущности
- [x] По одному рынку могут быть несколько thesis cycles
- [x] Нужен полный `decision journal`
- [x] Нужен `post-mortem` по ошибкам решения

### Бумажная торговля и валидация

- [x] Paper/shadow trading обязателен
- [x] Shadow trading должен идти по реальным данным Polymarket
- [x] Объект журнала = полный lifecycle:
  - [x] сигнал
  - [x] разбор
  - [x] решение
  - [x] вход / не вход
  - [x] сопровождение
  - [x] выход
  - [x] post-mortem
- [x] Авто-вход в `shadow mode` допускается только по правилам
- [x] Выход из paper-позиции должен быть гибридным
- [x] `thesis break` должен быть комбинированным триггером

### Интерфейсы

- [x] Dashboard = основное рабочее место оператора
- [x] Telegram = inbox, triage и alert-канал
- [x] Главный экран будущей системы = `командный центр`
- [x] Карточка рынка/позиции = `decision workspace`

### Watchlist и автономность

- [x] `Избранное` должно быть активным контуром наблюдения
- [x] После добавления в `Избранное` рынок получает:
  - [x] `watch dossier`
  - [x] `initial thesis snapshot`
  - [x] `review deadline`
- [x] `Forecast run` и `watchlist refresh` — разные контуры
- [x] `watchlist refresh` должен идти отдельно каждые `4-6` часов
- [x] Для watchlist сначала делается дешёвая проверка изменений
- [x] `deep review` запускается только по триггерам

### Роль News API и LLM

- [x] News API нужны для поиска новых значимых изменений, а не для вывода списков ссылок
- [x] LLM должен работать в двух режимах:
  - [x] `monitoring mode`
  - [x] `deep review mode`
- [x] Источники и raw facts должны быть скрыты по умолчанию
- [x] Оператор должен видеть только actionable-summary

## Текущая проблема продукта

Сейчас Pythia уже имеет:

- forecast pipeline;
- decision journal / shadow core;
- dashboard;
- quality layer;
- Telegram-оператор;
- базовые screen-экраны.

Но текущий UI и lifecycle ещё не соответствуют целевой модели.

Главный разрыв сейчас:

- dashboard всё ещё показывает слишком много вторичной информации;
- `Избранное` ещё не стало полноценным autonomous watchlist engine;
- оператор всё ещё вынужден вручную собирать смысл из экранов, вместо того чтобы получать готовое состояние рынка.

## Текущий фокус

### Focus now

- [x] Decision Journal + Shadow Trading Core внедрён
- [x] Workspace и базовые decision-сущности уже заведены в систему
- [ ] Превратить `Избранное` в автономный контур сопровождения
- [x] Вынести `watchlist refresh` в отдельный runtime-цикл
- [x] Сделать `monitoring mode` для watchlist-рынков
- [x] Включить `deep review` только по триггерам
- [x] Перестроить `Разбор рынка` из fact-dump в dossier-first workspace
- [ ] Сделать `Сводку` и `Избранное` главными экранами работы оператора

## Дорожная карта по фазам

### Phase 0 — Stabilize existing production

- [x] Не ломать текущий forecast stack
- [x] Не ломать quality layer
- [x] Не ломать dashboard auth и docker deploy
- [x] Завести базовый decision core в production

### Phase 1 — Decision Journal + Shadow Trading Core

- [x] Shared SQL schema decision-ядра
- [x] Таблицы `signals`, `books`, `decisions`, `positions`, `position_events`, `reviews`, `postmortems`
- [x] Автосоздание `signal` после forecast
- [x] Авто-shadow decision / shadow position по правилам
- [x] Manual decision API
- [x] Manual close / review completion API
- [x] Базовый workspace на странице рынка
- [x] Resolution flow -> close positions -> postmortem

### Phase 2 — Autonomous Watchlist Engine

- [x] `watch dossier`
- [x] `initial thesis snapshot`
- [x] автоматический `review deadline`
- [x] отдельный `watchlist refresh` каждые `4-6` часов
- [x] мониторинг цены по watchlist-рынкам
- [x] мониторинг новых внешних фактов
- [x] monitoring LLM mode
- [x] deep review triggers
- [x] thesis-strength delta
- [x] thesis-break detection
- [ ] watchlist attention items

### Phase 3 — Operator Workflow Redesign

- [ ] `Сводка` как command center, а не как dump
- [ ] `Избранное` как главная доска сопровождения
- [ ] `Разбор рынка` как short decision workspace
- [ ] источники и raw facts перевести во второй слой
- [ ] скрыть post-mortem и тяжёлые блоки до реальной необходимости
- [ ] показывать только:
  - [ ] что делать сейчас
  - [ ] почему это важно
  - [ ] что сломает идею
  - [ ] когда review
  - [ ] есть ли изменение с последней проверки

### Phase 4 — Attention Engine + Telegram Inbox

- [ ] сущность `attention_item`
- [ ] статусы `urgent / today / later / in_progress / closed`
- [ ] Telegram triage inbox
- [ ] быстрые действия из Telegram
- [ ] связка `attention item -> dashboard workspace`
- [ ] единый дневной digest как операционная картина дня

### Phase 5 — Paper Validation и Decision Quality

- [ ] метрики shadow performance
- [ ] сравнение operator mode vs shadow mode
- [ ] review discipline metrics
- [ ] post-mortem taxonomy
- [ ] weekly decision review
- [ ] quality dashboard не только по Brier, но и по решениям

### Phase 6 — Adaptive Improvement Layer

- [ ] использовать накопленный dataset для улучшения:
  - [ ] signal ranking
  - [ ] review triggers
  - [ ] prompt quality
  - [ ] decision heuristics
  - [ ] paper execution rules
- [ ] только после этого думать о локальной модели / policy head

## Как должна работать система в идеале

### Daily forecast

- ищет новые сильные идеи на всём рынке;
- предлагает оператору только несколько действительно важных сигналов.

### Watchlist refresh

- перепроверяет только рынки из `Избранного`;
- сам понимает, изменилось ли что-то важное;
- не требует ручного чтения полного разбора;
- создаёт alert только при значимом изменении.

### Dashboard

- показывает, что делать сейчас;
- не перегружает оператора фактами;
- даёт быстрый доступ к решению, review и позиции.

### Telegram

- сообщает только важное;
- помогает triage;
- возвращает оператора в dashboard только тогда, когда реально нужно действие.

## Критерии того, что мы движемся правильно

- [ ] Оператор больше не читает длинные dump-страницы для каждого рынка
- [ ] Добавление в `Избранное` реально включает автосопровождение
- [ ] Watchlist-рынки автоматически пересматриваются без ручного контроля
- [ ] Telegram поднимает только важные изменения
- [ ] Paper/shadow контур накапливает валидный track record
- [ ] Улучшения промптов и алгоритмов можно проверять на реальных paper-результатах
- [ ] Pythia становится системой, которая уменьшает когнитивную нагрузку, а не увеличивает её

## Ближайшая следующая задача

Следующий главный workstream:

- [ ] довести `Autonomous Watchlist Engine` до production rollout и связать его с дальнейшим `attention engine`

Именно он переведёт Pythia из состояния `умный прогнозный интерфейс` в состояние `живой аналитический операторский комбайн`.
