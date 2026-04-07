# Pythia v2: Decision Desk Design

Дата: 2026-04-07

## 1. Контекст

`Pythia` больше не должна мыслиться как просто система прогнозов для Polymarket.

Целевая роль продукта:

- приватный аналитический центр оператора;
- decision desk для работы с рынками Polymarket;
- журнал решений, paper-позиций и ошибок мышления;
- система, которая помогает не только строить вероятности, но и принимать дисциплинированные решения.

Иными словами, в `v2` главная единица ценности — не сам прогноз, а полный цикл:

`сигнал -> разбор -> решение -> вход/не вход -> сопровождение -> выход -> post-mortem`

## 2. Цель

Построить `Pythia v2` как операторскую decision system, которая:

- превращает прогнозы в actionable-сигналы;
- помогает оператору принимать решения по входу и сопровождению;
- ведёт paper/shadow trading на реальных рыночных данных Polymarket;
- фиксирует реальные решения оператора и их причины;
- накапливает валидный decision dataset для последующего улучшения модели и workflow.

## 3. Не-цели

В `v2` система не должна:

- автоматически ставить реальные ордера на Polymarket;
- хранить или использовать приватные trading-ключи на сервере;
- становиться автотрейдером;
- обещать прибыль как основную product claim;
- заменять оператора в принятии окончательного live-решения.

`Live execution` — отдельная будущая фаза, не часть этого дизайна.

## 4. Главный продуктовый принцип

Успех `Pythia v2` оценивается в двух слоях одновременно:

### 4.1. Внутренний слой

Качество аналитики:

- Brier score;
- calibration;
- edge vs market;
- стабильность reasoning и устойчивость сигналов.

### 4.2. Внешний слой

Качество decision support:

- помогла ли система найти хорошие входы;
- помогла ли избежать плохих входов;
- насколько дисциплинированно оператор следовал процессу;
- где именно сломалось решение: прогноз, цена входа, тайминг, сопровождение или дисциплина.

## 5. Роли интерфейсов

### 5.1. Dashboard

Главное рабочее место оператора.

Он должен быть:

- командным центром;
- местом разбора рынка;
- workspace для принятия решения;
- местом ведения paper/operator lifecycle;
- местом чтения quality и post-mortem.

### 5.2. Telegram

Основной inbox и канал прерываний.

Telegram нужен для:

- срочных attention items;
- triage по важности;
- быстрых операторских реакций;
- перехода в dashboard по важным случаям.

### 5.3. Scheduler

Фоновый мотор системы:

- daily forecast;
- resolve checks;
- weekly quality;
- watchlist/shadow refresh;
- review jobs;
- alert generation.

## 6. Главные сущности v2

### 6.1. Market

Аналитический объект.

Содержит:

- сам рынок Polymarket;
- историю прогнозов;
- источники и reasoning;
- текущие сигналы;
- связанные решения;
- связанные позиции.

### 6.2. Signal

Actionable-представление прогноза.

Один `signal` создаётся из конкретного прогноза и содержит:

- силу сигнала;
- рекомендуемое действие;
- допустимую зону входа;
- краткий тезис;
- главный драйвер;
- опровергающий сигнал;
- confidence;
- research status.

### 6.3. Decision

Структурированная запись решения.

Решение может быть создано:

- оператором;
- shadow-движком.

Решение хранит:

- тип действия;
- причину;
- уверенность;
- что должно подтвердить идею;
- что отменит идею;
- review-window.

### 6.4. Position

Исполнительный объект.

Позиция отличается от рынка:

- рынок — это объект анализа;
- позиция — это конкретный thesis cycle с входом, сопровождением и выходом.

По одному рынку может быть:

- несколько решений;
- несколько закрытых позиций;
- несколько thesis cycles.

### 6.5. Shadow Trade

Paper-позиция на реальных рыночных данных Polymarket.

Shadow trade:

- использует реальные цены и timestamps;
- не использует реальные деньги;
- не размещает реальные ордера;
- нужен для валидной оценки decision quality.

### 6.6. Attention Item

Единица внимания оператора.

Любое важное событие в системе становится `attention item`:

- новый сильный сигнал;
- риск по позиции;
- thesis break;
- overdue review;
- системная проблема.

### 6.7. Review

Запланированная или внеплановая точка пересмотра позиции или сигнала.

### 6.8. Post-Mortem

Разбор завершённого thesis cycle.

Главная цель — понять не только итог, но и место ошибки:

- ошибка прогноза;
- ошибка входа;
- ошибка тайминга;
- ошибка сопровождения;
- ошибка дисциплины.

## 7. Модель данных первого ядра

`predictions` остаётся существующим аналитическим слоем.

Поверх него добавляются отдельные таблицы decision-ядра:

- `signals`
- `books`
- `decisions`
- `positions`
- `position_events`
- `reviews`
- `postmortems`

### 7.1. signals

Назначение:

- сохранить actionable-сигнал, созданный из конкретного прогноза.

Минимальные поля:

- `id`
- `prediction_id`
- `market_id`
- `question`
- `forecast_timestamp`
- `signal_strength`
- `recommended_action`
- `entry_zone_min`
- `entry_zone_max`
- `thesis_summary`
- `primary_driver`
- `disconfirming_signal`
- `confidence`
- `research_status`
- `status`
- `created_at`

### 7.2. books

Назначение:

- отделить paper/shadow-книги и будущие operator-книги.

Минимальные поля:

- `id`
- `name`
- `mode`
- `currency`
- `starting_balance`
- `current_balance`
- `max_risk_per_trade_pct`
- `max_total_exposure_pct`
- `status`

### 7.3. decisions

Назначение:

- хранить решения оператора и shadow-движка в структурированном виде.

Минимальные поля:

- `id`
- `signal_id`
- `market_id`
- `actor_type`
- `decision_type`
- `decision_reason`
- `operator_confidence`
- `confirmation_criteria`
- `invalidation_criteria`
- `review_due_at`
- `created_at`

### 7.4. positions

Назначение:

- хранить lifecycle paper/operator позиции.

Минимальные поля:

- `id`
- `book_id`
- `market_id`
- `signal_id`
- `opened_by_decision_id`
- `mode`
- `side`
- `status`
- `entry_price_target`
- `entry_price_fill`
- `size_contracts`
- `notional_usd`
- `opened_at`
- `closed_at`
- `close_reason`
- `thesis_cycle_number`

### 7.5. position_events

Назначение:

- хранить полную историю событий по позиции.

Минимальные поля:

- `id`
- `position_id`
- `decision_id`
- `event_type`
- `event_note`
- `market_price`
- `research_probability`
- `created_at`
- `payload_json`

### 7.6. reviews

Назначение:

- хранить review-window и внеплановые ревью.

Минимальные поля:

- `id`
- `position_id`
- `market_id`
- `review_type`
- `due_at`
- `status`
- `review_note`
- `resolved_at`

### 7.7. postmortems

Назначение:

- фиксировать разбор завершённого цикла.

Минимальные поля:

- `id`
- `position_id`
- `market_id`
- `outcome_label`
- `pnl_percent`
- `forecast_error`
- `entry_error`
- `timing_error`
- `management_error`
- `discipline_error`
- `operator_note`
- `system_note`
- `created_at`

## 8. Lifecycle Shadow Trading

### 8.1. Общий поток

`signal created -> decision created -> paper entry candidate -> paper open -> review due -> thesis confirmed/thesis break -> paper exit -> post-mortem`

### 8.2. Когда создаётся shadow trade

Shadow trade открывается только если одновременно выполнены три условия:

- сигнал сильный;
- есть явное рекомендуемое действие (`enter_yes` или `enter_no`);
- цена находится в допустимой зоне входа.

### 8.3. Что считается входом

Вход — это не просто мнение модели, а paper-запись с:

- стороной `YES` или `NO`;
- целевой зоной цены;
- фактической рыночной ценой в момент открытия;
- виртуальным размером;
- ссылкой на signal и decision.

### 8.4. Как живёт позиция

После открытия позиция получает:

- `review_due_at`
- `confirmation_criteria`
- `invalidation_criteria`

### 8.5. Когда позиция уходит на review

Review возникает, если:

- наступил review-window;
- новый прогноз по этому рынку изменил тезис;
- появился `disconfirming_signal`;
- рынок ушёл слишком далеко против позиции.

### 8.6. Thesis break

`thesis break` срабатывает, если произошло хотя бы одно:

- новый reasoning Pythia существенно ослабил тезис;
- сработал сохранённый `disconfirming_signal`;
- рынок ушёл против позиции за предел допустимого порога;
- review-window истёк, а подтверждения идеи нет.

### 8.7. Варианты выхода

Причины выхода:

- `resolved`
- `thesis_break`
- `time_review`
- `take_profit`
- `stop_loss`
- `manual_shadow_close`

## 9. Operator Mode и Shadow Mode

### 9.1. Operator Mode

В этом режиме решение подтверждает человек.

Pythia:

- показывает сигнал;
- предлагает входную зону;
- предлагает risk framework;
- помогает вести позицию;
- пишет журнал действий.

### 9.2. Shadow Mode

В этом режиме система сама открывает paper-сделки по правилам.

Shadow Mode нужен для:

- накопления валидного decision dataset;
- сравнения оператора и системы;
- тестирования правил без реальных денег.

### 9.3. Почему нужны оба режима

Это даёт два параллельных канала:

- что сделала бы система;
- что сделал оператор.

Именно на сравнении этих контуров строится обучение и улучшение продукта.

## 10. Decision Journal

Главный объект журнала — не просто сделка, а полный lifecycle решения.

Минимальный цикл:

- сигнал увиден;
- рынок разобран;
- решение принято;
- вход совершен или отклонён;
- review выполнен;
- позиция закрыта;
- post-mortem записан.

### 10.1. Что оператор обязан фиксировать

Для каждого важного ручного действия оператор фиксирует:

- действие (`вошёл / не вошёл / сократил / вышел / передумал`);
- причину;
- уверенность;
- что подтвердит идею;
- что отменит идею.

## 11. Risk Framework

Pythia не должна рекомендовать жёсткий денежный размер позиции в долларах.

Она должна рекомендовать:

- класс сигнала;
- допустимый риск;
- максимальную экспозицию на идею;
- условия, при которых нельзя добирать позицию.

Это делает систему risk-aware, но не превращает её в execution bot.

## 12. Attention Engine

`Attention item` — единица внимания оператора.

Статусы:

- `new`
- `urgent`
- `today`
- `later`
- `in_progress`
- `closed`
- `cancelled`

### 12.1. Что может стать attention item

- новый сильный сигнал;
- thesis break;
- review overdue;
- риск по позиции;
- системная проблема.

### 12.2. Telegram как inbox

Telegram в `v2` — не резервный бот, а inbox оператора.

Он нужен для:

- triage по приоритету;
- эскалации срочных событий;
- быстрых действий;
- перехода в dashboard.

## 13. Главный экран v2

Главная страница dashboard — `Командный центр`.

Порядок сверху вниз:

- срочные attention items;
- активные позиции;
- review today;
- thesis break alerts;
- новые кандидаты на вход;
- системное здоровье;
- качество модели.

Это должен быть не экран “что спрогнозировано”, а экран “что оператору делать сейчас”.

## 14. Decision Workspace

Главный режим одной карточки рынка/позиции — `decision workspace`.

Внутри него оператор должен уметь:

- читать тезис;
- видеть цену и сигнал;
- принять решение;
- открыть или не открыть paper-позицию;
- зафиксировать причину;
- поставить review;
- закрыть цикл;
- оформить post-mortem.

## 15. Roadmap

### Phase 0. Stabilize current production

Цель:

- не ломать текущий forecast-stack;
- сохранить dashboard и quality-слой;
- не потерять текущую валидацию сигналов.

### Phase 1. Decision Journal + Shadow Trading Core

Сделать:

- новые таблицы decision-ядра;
- создание `signal` из последнего прогноза;
- ручное создание `decision`;
- автоматическое создание `shadow decision`;
- создание `shadow position`;
- review-window;
- `position_events`;
- базовый post-mortem.

Это первый и главный этап.

### Phase 2. Watchlist Refresh и Persistent Desk

Сделать:

- если рынок в `Избранном`, он обязан перепроверяться на каждом прогоне;
- отдельный `watchlist refresh`;
- thesis-break logic;
- review scheduler;
- lifecycle обновления позиций.

### Phase 3. Attention Engine + Telegram Inbox

Сделать:

- `attention_items`;
- приоритеты `urgent/today/later`;
- Telegram triage;
- быстрые действия по alert item;
- связку alert -> dashboard workspace.

### Phase 4. Dashboard v2: Command Center + Decision Workspace

Сделать:

- новый главный экран командного центра;
- экран активных позиций;
- экран reviews;
- decision workspace рынка/позиции;
- позиционный и shadow lifecycle в UI.

### Phase 5. Post-Mortem и Quality of Decisions

Сделать:

- классификацию ошибок;
- метрики не только прогнозов, но и решений;
- сравнение operator mode vs shadow mode;
- decision-quality dashboard.

### Phase 6. Local Model / Policy Improvement

Только после накопления decision dataset.

Цель:

- улучшать ranking сигналов;
- улучшать decision layer;
- обучать компактный локальный policy/head на накопленной истории решений.

Не цель этой фазы:

- переобучать “большой базовый LLM” без достаточного корпуса и инфраструктуры.

## 16. Основной вывод

`Pythia v2` — это private operator intelligence system.

Её задача:

- находить actionable-сигналы;
- дисциплинировать решение;
- вести paper/shadow journal;
- помогать оператору работать с рынком системно;
- постепенно превращать личный процесс оператора в валидный и масштабируемый продукт.

