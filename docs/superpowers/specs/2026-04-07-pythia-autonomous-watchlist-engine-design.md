# Pythia Autonomous Watchlist Engine Design

Дата: 2026-04-07

## 1. Контекст

`Pythia v2` уже умеет:

- строить daily forecasts;
- создавать actionable `signals`;
- вести `decision journal`;
- открывать `shadow` и `operator` paper-позиции;
- показывать базовый workspace в dashboard.

Но этого недостаточно для целевой модели продукта.

Сейчас `watchlist` остаётся слишком пассивным:

- это почти просто список `market_id`;
- рынок из `Избранного` не получает отдельного контура сопровождения;
- оператор по-прежнему вынужден вручную перечитывать рынок, чтобы понять, изменилось ли что-то важное.

Это противоречит целевой роли `Pythia` как автономного аналитического комбайна.

## 2. Цель

Построить отдельный backend-контур `Autonomous Watchlist Engine`, который:

- автоматически сопровождает рынки из `Избранного`;
- живёт отдельно от daily forecast;
- использует реальную цену Polymarket, news API и LLM;
- создаёт короткие operator-facing updates;
- поднимает оператору только значимые изменения;
- становится базой для paper-validation и дальнейшего улучшения системы.

## 3. Не-цели

На этой фазе engine не должен:

- искать новые рынки на всём Polymarket;
- заменять daily forecast;
- ставить реальные ордера;
- показывать в UI длинные dumps источников и фактов по умолчанию;
- превращаться в полноценный `attention engine` для всех типов событий;
- перестраивать весь dashboard в один заход.

## 4. Главный принцип

`Forecast run` и `Watchlist refresh` — это разные моторы системы.

### 4.1. Daily forecast

Отвечает за:

- поиск новых сильных идей;
- первичный прогноз;
- первичный сигнал;
- обновление общего сигнального поля дня.

### 4.2. Watchlist refresh

Отвечает за:

- сопровождение уже выбранных рынков;
- проверку изменения тезиса;
- проверку появления новых рисков;
- обновление dossier;
- создание operator-facing updates;
- сигнализацию в Telegram и dashboard при значимых изменениях.

## 5. Архитектурный подход

Выбран путь:

- отдельный backend-контур `Watchlist Engine`;
- отдельные сущности хранения;
- отдельный runtime job;
- отдельная логика `monitoring mode` и `deep review mode`;
- dashboard и Telegram читают уже готовые результаты этого контура.

Почему не смешивать с `decision core`:

- поиск и сопровождение — разные ответственности;
- `signals/decisions/positions` не должны хранить весь follow-up слой тезиса;
- отдельный engine проще тестировать, улучшать и делать наблюдаемым.

## 6. Границы ответственности

### 6.1. Forecast stack

Хранит:

- `predictions`;
- первичные `signals`;
- контекст последнего daily run.

Не хранит:

- живой follow-up по watchlist;
- частые промежуточные refresh-снимки;
- операторские watch-updates.

### 6.2. Watchlist Engine

Хранит и считает:

- `watch dossier`;
- технические refresh-снимки;
- operator-facing updates;
- monitoring status;
- deep review transitions;
- thesis-strength delta;
- thesis-break detection.

### 6.3. Decision Core

Остаётся ответственным за:

- `decisions`;
- `positions`;
- `reviews`;
- `postmortems`.

Watchlist Engine может создавать `review` и сигнализировать о необходимости действия, но не должен подменять decision core.

### 6.4. Dashboard и Telegram

Не вычисляют бизнес-логику сами.

Они только читают:

- текущее состояние dossier;
- последний update;
- историю updates;
- связанные decisions / positions / reviews.

## 7. Новые сущности

### 7.1. `watch_dossiers`

Главная сущность сопровождения одного рынка.

Один рынок в `Избранном` = один активный `watch dossier`.

Минимальные поля:

- `id`
- `market_id`
- `status`
- `created_from_prediction_id`
- `created_from_signal_id`
- `created_at`
- `updated_at`
- `first_watched_at`
- `last_refresh_at`
- `last_deep_review_at`
- `next_review_due_at`
- `freshness_policy`
- `thesis_state`
- `current_market_price`
- `current_research_probability`
- `current_naked_probability`
- `current_confidence`
- `current_research_status`
- `current_thesis_summary`
- `current_primary_driver`
- `current_invalidation_signal`
- `operator_note`

Назначение:

- хранить текущее живое состояние рынка;
- быть точкой входа для dashboard и Telegram;
- не хранить подробную историю, а только текущее состояние.

### 7.2. `watch_refresh_runs`

Журнал запусков самого refresh-контура.

Минимальные поля:

- `id`
- `started_at`
- `finished_at`
- `status`
- `markets_total`
- `markets_checked`
- `markets_changed`
- `deep_reviews_triggered`
- `alerts_created`
- `error_summary`

Назначение:

- наблюдаемость;
- weekly review;
- диагностика деградации refresh engine.

### 7.3. `watch_refresh_snapshots`

Лёгкий технический снимок по одному рынку после каждого refresh.

Минимальные поля:

- `id`
- `watch_dossier_id`
- `refresh_run_id`
- `captured_at`
- `market_price`
- `research_probability`
- `naked_probability`
- `edge_vs_market`
- `price_delta_since_last`
- `thesis_state`
- `research_status`
- `monitoring_status`
- `trigger_flags_json`
- `news_digest_json`
- `monitoring_output_json`

Назначение:

- технический след;
- backfill аналитики;
- объяснение, почему engine принял решение о `deep review` или `no change`.

### 7.4. `watch_updates`

Операторский update, создаваемый только при значимом изменении.

Минимальные поля:

- `id`
- `watch_dossier_id`
- `refresh_run_id`
- `created_at`
- `severity`
- `status_label`
- `reason_summary`
- `action_summary`
- `market_price_before`
- `market_price_after`
- `research_probability_before`
- `research_probability_after`
- `change_summary_json`
- `delivered_to_dashboard_at`
- `delivered_to_telegram_at`

Назначение:

- быть человекочитаемой единицей сопровождения;
- питать `Избранное` и Telegram;
- стать будущим источником для `attention items`.

## 8. Создание dossier при добавлении в избранное

### 8.1. Базовое правило

Когда рынок попадает в `Избранное`, система не просто создаёт запись в watchlist.

Она обязана:

- создать `watch dossier`;
- записать `initial thesis snapshot`;
- определить policy свежести;
- поставить первый `review deadline`.

### 8.2. Источник initial thesis snapshot

Выбран гибридный путь:

- если у рынка есть свежий forecast snapshot, dossier создаётся из него;
- если snapshot устарел, рынок сразу ставится в очередь на `immediate deep review`.

### 8.3. Policy свежести

Свежесть snapshot зависит от горизонта рынка:

- краткосрочные рынки: `12 часов`;
- обычные рынки: `24 часа`;
- long-horizon рынки: `48 часов`.

Если snapshot старше этого окна, reuse запрещён.

### 8.4. Первый review deadline

Первый `review deadline` ставится автоматически:

- сильный сигнал или открыта paper-позиция: `24h`;
- просто наблюдение: `48h`;
- long-horizon рынок: `72h`.

## 9. Priority set refresh-контура

Engine обязан refresh-ить в следующем порядке:

1. все открытые paper-позиции;
2. все рынки из `Избранного`;
3. опционально свежие сильные сигналы без избранного как short-term buffer.

Это правило означает:

- позиции всегда выше watchlist;
- watchlist всегда выше обычного сигнального фона.

## 10. Два режима анализа

### 10.1. `Monitoring mode`

Это дешёвый проход по рынку.

Вход:

- текущий dossier;
- прошлый snapshot;
- свежая цена;
- свежие news hits;
- confirmation/invalidation criteria;
- данные о позиции, если позиция есть.

Выход:

- `no_change`
- `strengthened`
- `weakened`
- `review_required`
- `thesis_break`

Monitoring mode должен быть:

- быстрым;
- дешёвым;
- устойчивым;
- пригодным для частого запуска.

### 10.2. `Deep review mode`

Полноценный пересмотр тезиса.

Вход:

- последний thesis snapshot;
- свежая цена и дельты;
- news summary;
- предыдущие ключевые факты;
- связанные decisions / positions / reviews.

Выход:

- новая probability;
- обновлённый thesis summary;
- новый primary driver;
- обновлённый invalidation signal;
- новый review deadline;
- короткий operator-facing summary.

## 11. Триггеры deep review

`Deep review` запускается только по композитному триггеру.

Достаточно одного из условий:

- сильный сдвиг цены;
- заметное изменение monitoring-статуса;
- новый `disconfirming signal`;
- наступил `review due`;
- по рынку есть открытая paper-позиция и тезис ослаб.

Это защищает систему от:

- лишних LLM вызовов;
- информационного шума;
- дорогого re-analysis без причины.

## 12. Что сохраняется после refresh

Выбран двухслойный подход.

### 12.1. После каждого refresh

Сохраняется лёгкий технический snapshot.

Он нужен для:

- трассировки поведения engine;
- backtesting правил сопровождения;
- дальнейшего улучшения monitoring logic.

### 12.2. Только при значимом изменении

Создаётся полноценный operator-facing update.

Он обязан содержать:

- `что изменилось`;
- `почему`;
- `что делать`.

## 13. Формат operator-facing update

Каждый значимый update обязан иметь три части:

- `status`
- `reason`
- `action`

Пример:

- статус: `тезис ослаб`
- причина: `рынок ушёл против нас на 11 п.п. и появился новый риск травмы`
- действие: `открыть review`

Это минимальный операторский контракт.

## 14. Интеграция с Telegram и Dashboard

### 14.1. Dashboard

Dashboard хранит:

- полную историю сопровождения;
- текущее состояние dossier;
- последний update;
- динамику статуса по рынку;
- связанные reviews и позиции.

### 14.2. Telegram

Telegram не дублирует полную историю.

Он показывает:

- короткий alert;
- краткий статус;
- краткую причину;
- краткое действие;
- ссылку / переходную логику обратно в dashboard.

### 14.3. Разделение ролей

- dashboard = память и decision workspace;
- Telegram = прерывание и triage.

## 15. News API и LLM внутри engine

### 15.1. News API

`Exa` и `Tavily` используются как change-detection слой.

Их задача:

- найти новые значимые факты;
- найти признаки опровержения тезиса;
- подтвердить, что изменений нет;
- дать сжатое сырьё для monitoring и deep review.

Они не должны:

- формировать сырой длинный операторский экран;
- тянуть весь список материалов в UI по умолчанию.

### 15.2. LLM

LLM используется как:

- monitoring classifier;
- deep-review synthesizer.

LLM не должен:

- быть источником интерфейсной многословности;
- генерировать простыню фактов для оператора;
- заменять собой price/rule-based layer.

## 16. Runtime orchestration

### 16.1. Отдельный job

`Watchlist refresh` запускается отдельным job:

- каждые `4-6` часов;
- независимо от daily forecast;
- отдельно логируется;
- не должен блокировать `scheduler`.

### 16.2. Порядок одного run

Один refresh run делает:

1. собирает priority set;
2. создаёт запись в `watch_refresh_runs`;
3. по каждому рынку получает свежую цену;
4. получает свежие внешние материалы;
5. запускает `monitoring mode`;
6. при необходимости запускает `deep review mode`;
7. сохраняет snapshot;
8. создаёт update, если изменение значимо;
9. обновляет dossier;
10. отправляет alert при необходимости.

### 16.3. Fault tolerance

Если один рынок падает по ошибке:

- весь run не должен падать;
- ошибка записывается в run summary;
- dossier по этому рынку получает технический error state;
- остальные рынки продолжают refresh.

## 17. Dashboard UX-следствия

После внедрения engine:

- `Избранное` становится главным экраном сопровождения;
- `Разбор рынка` больше не обязан показывать весь raw context сразу;
- оператор должен видеть сначала:
  - текущий статус тезиса;
  - последнее изменение;
  - action now;
  - review due;
  - связь с позицией.

Сырой research уходит во второй слой.

## 18. Метрики успеха engine

### 18.1. Технические

- refresh runs проходят стабильно;
- watchlist не пропускает review due;
- deep review запускается только по значимым триггерам;
- alerts не дублируются хаотично.

### 18.2. Операторские

- оператор не перечитывает рынок вручную без нужды;
- `Избранное` реально становится рабочей доской сопровождения;
- Telegram поднимает только важные изменения;
- Pythia уменьшает когнитивную нагрузку.

### 18.3. Продуктовые

- paper-решения становятся более последовательными;
- можно сравнивать `shadow vs operator` уже не только на уровне входа, но и на уровне сопровождения;
- появляется новый валидный dataset по lifecycle тезиса.

## 19. Rollout по этапам

### Этап A

- таблицы `watch_dossiers`, `watch_refresh_runs`, `watch_refresh_snapshots`, `watch_updates`;
- создание dossier при добавлении в watchlist;
- read API для dossier/update history.

### Этап B

- отдельный refresh job;
- monitoring mode;
- snapshot persistence;
- basic operator-facing updates.

### Этап C

- deep review triggers;
- интеграция с reviews и positions;
- Telegram alerts.

### Этап D

- redesign экрана `Избранное`;
- redesign `Market Detail` вокруг dossier;
- integration в command center.

## 20. Основной вывод

`Autonomous Watchlist Engine` — это тот слой, который превращает `Pythia` из умной daily-forecast системы в живой decision desk.

Без него:

- `Избранное` остаётся списком;
- оператор всё ещё сам делает сопровождение;
- система не становится действительно автономной.

С ним:

- каждый выбранный рынок получает живой lifecycle;
- оператор начинает получать только значимые изменения;
- Pythia начинает выполнять ту роль, ради которой проект и строится.
