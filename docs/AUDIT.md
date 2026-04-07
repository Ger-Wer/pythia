# Аудит Pythia

Дата актуализации: 2026-04-07

## 1. Назначение системы

Pythia — это автономная forecast + decision система для prediction markets на Polymarket.

Система:

- ежедневно выбирает подходящие бинарные рынки;
- каждые 6 часов обновляет рынки из `Избранного` отдельным `watchlist refresh`-контуром;
- строит по ним два прогноза:
  - `Naked R1` как baseline без полноценного research;
  - `Research+R1` как основной прогнозный контур с поиском новостей и финальным synthesis;
- сохраняет прогнозы и метаданные в SQLite;
- ведёт paper/shadow lifecycle через `decision core`;
- отслеживает resolved-маркеты;
- считает Brier score и edge;
- отдаёт операторские данные в dashboard и отправляет алерты/отчёты в Telegram.

Система не делает следующее:

- не торгует;
- не хранит криптоключи;
- не открывает публичный backend API без авторизации;
- не требует прямого внешнего входа в контейнеры.

## 2. Текущее production-размещение

Pythia размещена на хосте `amper` в Docker-first конфигурации.

Изоляция:

- отдельный Linux-пользователь: `pythia`;
- отдельный каталог: `/home/pythia/pythia`;
- отдельный compose-проект: `pythia`;
- отдельная bridge-network: `pythia_internal`;
- собственные bind-mounted каталоги:
  - `/home/pythia/pythia/data`
  - `/home/pythia/pythia/logs`
- dashboard публикуется только на loopback хоста: `127.0.0.1:8787 -> pythia-dashboard:8080`.

На том же хосте работает `SproutSignal`, но Pythia не делит с ним сети, volumes и runtime-пути. Жёсткой изоляции на уровне отдельной машины нет, потому что общий kernel и общий Docker daemon остаются общими.

## 3. Реальная архитектура runtime

### Контейнеры

- `pythia-scheduler`
  - основной long-running контейнер;
  - запускает `supercronic`;
  - выполняет `forecast`, `watchlist refresh`, `watch alerts`, `resolution checker` и weekly jobs по расписанию.
- `pythia-telegram`
  - polling-бот;
  - принимает операторские команды только от одного разрешённого Telegram user ID;
  - читает ту же SQLite и те же логи;
  - использует read-only доступ к `/var/run/docker.sock` для health/status.
- `pythia-dashboard`
  - web-консоль оператора (React SPA + FastAPI API);
  - использует cookie-session auth;
  - выполняет whitelisted operator actions (`forecast`, `resolve`, `weekly`);
  - доступен извне только через reverse proxy на `b1w.duckdns.org`.

### Технологический стек

- TypeScript / Bun — оркестрация прогноза и отдельного `watchlist engine`;
- Python 3.11 — операторский слой, аналитика, resolution checker;
- SQLite — локальное хранилище;
- Groq OpenAI-compatible API — LLM backend на текущем production;
- Exa — primary news search provider;
- Tavily — fallback news search provider;
- Telegram Bot API — операторские команды и алерты.
- FastAPI + React/Vite — dashboard-слой оператора.

### Дополнительные runtime-контуры

- `decision core`
  - отдельный слой `signals / decisions / positions / reviews / postmortems`;
  - питает paper/shadow lifecycle и workspace рынка.
- `watchlist engine`
  - отдельный слой `watch_dossiers / watch_refresh_runs / watch_refresh_snapshots / watch_updates`;
  - отвечает за сопровождение рынков из `Избранного`;
  - делает дешёвую monitoring-проверку каждые 6 часов и запускает `deep review` только по триггерам.

### Production-модели на 2026-03-29

- Research LLM: `llama-3.3-70b-versatile` (Groq)
- Synthesis LLM: `openai/gpt-oss-120b` (Groq)
- Naked baseline LLM: `openai/gpt-oss-120b` (Groq)

## 4. Что происходит с Dexter

### Текущее состояние

Нативный runtime `Dexter` в production не используется.

Это означает:

- в Docker-образ не клонируется репозиторий Dexter;
- внутри running container нет `/app/dexter`;
- никакой `Dexter Agent`, `Dexter CLI` или programmatic API в runtime не запускается.

### Почему в базе и коде ещё есть `dexter_*`

В схеме SQLite и части аналитических расчётов по историческим причинам сохранены поля:

- `dexter_p`
- `dexter_confidence`
- `dexter_reasoning`
- `dexter_key_risk`
- `dexter_base_rate`
- `brier_dexter`

На текущем этапе это не означает использование реального Dexter. Это legacy-названия для основного research-контура.

Правильная интерпретация сейчас:

- `dexter_*` в БД = результат контура `Research+R1`;
- `Naked R1` = baseline-контур без полноценного research;
- пользовательский слой и отчёты уже показывают честное название `Research+R1`.

Такой подход выбран ради обратной совместимости с уже накопленными данными и без миграции исторической БД.

## 5. Поток данных: ежедневный forecast

### Шаг 1. Выбор рынков

Источник: Polymarket Gamma REST API.

Система:

- получает активные бинарные рынки;
- применяет ступенчатые volume/deadline фильтры;
- дедуплицирует рынки по событию;
- ограничивает diversity:
  - максимум `1` рынок на событие;
  - максимум `3` рынка на категорию.

Текущие ступени отбора:

- `1-21` дней, `$1K-$100K`
- `1-30` дней, `$500-$250K`
- `1-60` дней, `$500-$250K`

Если после потолка `60` дней система набирает мало уникальных событий, она логирует предупреждение, но не расширяется на полугодовые рынки. Цель — не брать десятки подрынков одного и того же события, не забивать прогон тривиальными `0.4%` рынками и не ждать месяцы до первых resolved-наблюдений.

### Шаг 2. News search

Для каждого рынка запускается multi-search:

- как минимум два поисковых запроса;
- `Exa` используется первым;
- при ошибке или лимите — `Tavily` как fallback;
- если оба search provider недоступны, система не падает, а деградирует в `LLM-only` режим.

### Шаг 3. Baseline-контур

`Naked R1` получает:

- текст вопроса;
- дедлайн;
- рыночную цену;
- короткие news snippets, если они есть.

Результат:

- вероятность `0-100`;
- короткое reasoning;
- стоимость вызова.

### Шаг 4. Основной research-контур

Контур `Research+R1` работает так:

1. multi-search собирает новости и контекст;
2. research-слой делает один структурированный LLM-вызов и строит research brief;
3. финальный synthesis prompt выдаёт итоговую probability estimate.

Research brief включает:

- summary;
- key facts;
- аргументы за YES;
- аргументы против YES;
- что толпа может упускать;
- тип катализатора (`catalyst_type`);
- главный драйвер вероятности (`primary_driver`);
- вторичные подтверждающие сигналы (`second_order_effects`);
- быстрый опровергающий сигнал (`disconfirming_signal`);
- список источников.

Это `Catalyst + Spillover v1`: reasoning-enrichment слой, который улучшает интерпретацию новостей и операторский UX, но пока не используется как отдельный scoring feature.

### Шаг 5. Сохранение и алерты

В SQLite сохраняются:

- рыночная цена;
- baseline-прогноз;
- основной `Research+R1` прогноз;
- reasoning / confidence / base rate / key risk;
- search-derived research brief;
- timestamp;
- API cost;
- `is_valid`.

Если отклонение от рынка по любой ветке превышает порог, Pythia отправляет Telegram alert.

## 5A. Поток данных: Autonomous Watchlist Engine

После добавления рынка в `Избранное` система:

- создаёт `watch dossier`;
- пытается переиспользовать свежий forecast snapshot;
- если snapshot устарел, помечает рынок как `pending_deep_review`;
- ставит первый `review deadline`.

Каждый `watchlist refresh`:

- берёт рынки из `Избранного` и открытые paper-позиции;
- подтягивает текущую цену Polymarket;
- делает дешёвую monitoring-проверку через news API + LLM monitoring mode;
- пишет технический `watch_refresh_snapshot`;
- при значимом изменении создаёт краткий `watch_update`:
  - что изменилось;
  - почему;
  - что делать оператору.

Telegram получает только `watch_updates`, а не raw research dump.

## 6. Resolution flow

Python-скрипт `resolution_checker.py`:

- находит unresolved markets;
- повторно спрашивает Gamma API;
- определяет исход `YES/NO/N/A`;
- записывает outcome и resolved timestamp;
- пересчитывает Brier score:
  - для `Research+R1`;
  - для `Naked R1`;
  - для рыночной цены.

Если рынок отменён или не имеет валидного бинарного исхода, он не используется как полноценное наблюдение для метрик.

Если Gamma API возвращает `HTTP 422` для одного и того же `market_id` три раза подряд, такой рынок помечается как `is_valid = 0` и исключается из будущих resolution-check прогонов.

## 7. Метрики и качество данных

### Brier и edge

Для статистической оценки используются:

- `brier_dexter` как метрика основного `Research+R1` контура;
- `brier_naked` как baseline;
- `brier_market` как сравнение с толпой.

`edge_calculator.py` считает:

- средние Brier score;
- paired t-tests;
- edge против рынка;
- решение `GO / PIVOT / STOP`.

### Поле `is_valid`

После ранних прогонов, где отбор рынков был некачественным и давал слишком много коррелированных подрынков, в таблицу было добавлено поле `is_valid`.

Назначение:

- отделить старые мусорные наблюдения от валидной выборки;
- не пускать некачественные данные в `/status`, `/last_predictions`, weekly report, Brier и edge.

Текущие операторские и аналитические запросы фильтруют данные по `is_valid = 1`, если колонка присутствует.

Отдельно из базы удаляется техническая заглушка `market-init`, чтобы она не попадала в операционные выборки и не засоряла аудит.

## 8. Операторский интерфейс

Основной операторский интерфейс — web-dashboard.

Telegram-бот остаётся как fallback-канал и канал алертов.

Текущая модель dashboard:

- `Сводка` - decision-first главный экран с `Что делать сейчас`, `Что требует внимания`, кратким health и быстрыми safe actions;
- `Качество` - отдельный trust-экран: resolved-выборка, Brier, edge vs market, breakdown по категориям и glossary для интерпретации метрик;
- `Главные сигналы дня` - commentary-layer поверх последнего прогона, который помогает начать с 3 наиболее важных рынков, а не с сырого списка;
- `Сигналы` - операторский список рынков последнего прогона, отсортированный по полезности для разбора;
- `Разбор рынка` - dossier-first workspace: сначала состояние тезиса, последнее изменение и следующее действие, а тяжёлый анализ и источники скрыты во второй слой;
- `Избранное` - board по `watch dossier` и `watch updates`, а не список `market_id`;
- `Действия` - запуск whitelisted сценариев с human-summary-first результатом и отдельным доступом к техническому логу.

Telegram-бот теперь играет две роли:

- fallback-канал команд;
- delivery-канал для `watch_updates`.

## 8A. Текущее ограничение

`Watchlist engine` уже создаёт dossier, snapshots и updates, но ещё не является полноценным `attention engine`.

Это означает:

- единая сущность `attention_item` ещё не заведена;
- triage в Telegram пока простой, а не полноценный inbox;
- `watch_updates` пока являются предшественником будущего alert/attention-слоя.

На уровне UX это означает:

- сырые логи не показываются по умолчанию;
- сложные блоки и метрики снабжены hover/tap-подсказками формата `Что это / Почему важно / Как читать`;
- безопасные действия находятся рядом с контекстом, а не в отдельной shell-модели;
- оператор может работать через названия рынков и кнопки интерфейса, а не через Telegram-команды и внутренние идентификаторы.

### Trust-layer dashboard

К весне 2026 операторский dashboard получил отдельный trust-layer, вдохновлённый практиками публичных prediction-интерфейсов, но адаптированный под закрытую private-console модель Pythia.

Сейчас trust-layer состоит из:

- страницы `Качество`, которая честно отвечает, достаточно ли `resolved`-данных для оценки edge;
- `Главных сигналов дня`, которые превращают последний прогон в короткий операторский briefing;
- `Истории оценки` в `Разборе рынка`, где видно динамику `Research+R1 / Naked R1 / рынок`;
- `Прозрачности вывода`, где оператор видит факты, источники, опровергающий сигнал и время последнего обновления;
- единого tooltip/glossary-слоя в формате `Что это / Почему важно / Как читать`.

Поддерживаемые команды:

- `/help`
- `/status`
- `/forecast`
- `/resolve`
- `/weekly`
- `/day0`
- `/edge`
- `/logs`
- `/health`
- `/last_predictions`
- `/details <номер|market_id>`

Также поддерживаются русские текстовые алиасы.

Принципы UX:

- heavy-команды не возвращают raw stdout/stderr;
- обычные команды отвечают человекочитаемыми отчётами на русском;
- сырой лог отдаётся только через `/logs`;
- доступ ограничен одним Telegram user ID в личном чате.

## 9. Health model

Команда `/health` и dashboard health-блок сейчас проверяют:

- доступность SQLite и таблицы прогнозов;
- наличие runtime-конфигурации LLM backend;
- наличие search-ключей Exa и Tavily;
- наличие scheduler-артефактов (`logs/`, status file);
- наличие Telegram bot credentials;
- базовый ожидаемый статус публичного Gamma API как внешней зависимости pipeline.

Health-ответ агрегирует проблемы и показывает human-readable статус:

- `ON`
- `DEGRADED`
- `OFF`

В dashboard эти же статусы выводятся как человеко-понятные индикаторы и поясняются встроенными tooltip-подсказками. Это configuration-and-runtime health, а не глубокий synthetic monitoring всех внешних API.

## 10. Стоимость и cost tracking

На текущем production LLM backend работает через Groq.

Практический смысл:

- в операторском слое API показывается как `бесплатно (Groq)`;
- трекер стоимости не должен симулировать DeepSeek-расходы для Groq-моделей;
- поиск новостей по Exa/Tavily остаётся отдельным лимитируемым ресурсом.

## 11. Удалённый и локальный cleanup

На момент этой актуализации выполнено следующее:

- из runtime-образа исключён Dexter;
- legacy-надписи `Dexter+R1` заменены на `Research+R1` в пользовательском слое;
- локальный неиспользуемый каталог `dexter/` подлежит удалению как мусор от раннего этапа интеграции;
- Docker-очистка на `amper` ограничивается только Pythia-артефактами и глобальным dangling-мусором, без воздействия на `SproutSignal`.

Принцип очистки:

- не удалять ничего, что может относиться к `SproutSignal`;
- не делать blanket-prune для volumes/networks;
- удалять только безопасно идентифицируемый Pythia-мусор и dangling images.

## 12. Ограничения и честные оговорки

- Dashboard зависит от reverse proxy и корректной HTTPS-конфигурации.
- На одном хосте с `SproutSignal` нельзя добиться абсолютной изоляции уровня отдельной машины.
- Исторические поля `dexter_*` в БД сохраняются для обратной совместимости, хотя реального Dexter runtime нет.
- Качество research зависит от внешних search provider и LLM backend.
- Статистическая значимость edge появится только после накопления достаточного числа resolved observations.

## 13. Что считать текущей “истиной” по проекту

Если между файлами есть расхождение, источники правды в таком порядке:

1. `docs/AUDIT.md`
2. `README.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DEPLOYMENT.md`
5. исторические design/plan-документы в `docs/superpowers/**`

## 14. Промпты

Промпты, которые определяют текущее поведение системы, зафиксированы в коде:

- Research prompt: [src/services/research_runner.ts](../src/services/research_runner.ts)
- Synthesis prompt: [src/prompts/synthesis.ts](../src/prompts/synthesis.ts)
- Naked baseline prompt: [src/services/naked_r1.ts](../src/services/naked_r1.ts)

Изменение этих промптов означает изменение поведения системы. Любые такие изменения нужно фиксировать в [docs/DECISION_LOG.md](DECISION_LOG.md).

## 15. Catalyst + Spillover v1

Начиная с актуальной версии production, `Research+R1` хранит в `research_brief_json` не только краткую сводку и факты, но и дополнительные поля:

- `catalyst_type`
- `primary_driver`
- `second_order_effects`
- `disconfirming_signal`

Назначение этого слоя:

- объяснить оператору, что сейчас двигает вероятность;
- показать, какие внешние сигналы должны подтверждать прогноз;
- показать, какой сигнал быстрее всего его опровергнет;
- подготовить данные для будущего анализа по типам катализаторов.

В `v1` эти поля используются:

- в research brief;
- в synthesis context;
- в Telegram `/details`.

В `v1` эти поля не используются:

- как отдельные численные факторы в scoring;
- как отдельные колонки SQLite;
- как критерий автоматического отбора рынков.

## 16. Краткий вывод для внешнего аудитора

Pythia на текущем этапе — это не торговая система и не LLM-агент общего назначения, а изолированная Docker-first система прогнозной аналитики и сопровождения тезисов.

Её production-ядро состоит из:

- рыночного отбора с дедупликацией и diversity-контролем;
- baseline-прогноза;
- отдельного research-контура `Research+R1`;
- `decision core` для paper/shadow lifecycle;
- `watchlist engine` для follow-up по рынкам из `Избранного`;
- SQLite-хранилища;
- Python-аналитики для resolved markets;
- web-dashboard как основного интерфейса;
- Telegram-оператора как канала алертов, `watch updates` и fallback-управления.

Dexter как внешний фреймворк в production не используется. Любые упоминания `dexter_*` в БД и коде относятся к legacy-схеме имени основного research-контура, а не к живому Dexter runtime.
