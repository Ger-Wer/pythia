# Дизайн Docker-деплоя Pythia на `amper`

> Историческая спецификация: документ фиксирует дизайн на момент 2026-03-29. Актуальное состояние production-системы описано в `README.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md` и `docs/AUDIT.md`. Текущий production-образ не включает Dexter runtime.

Дата: 2026-03-29
Статус: одобрено для перехода к планированию
Область: только deployment-архитектура и операционная модель. Основная логика прогнозирования Pythia сохраняется без изменений, если только требования контейнерного запуска не потребуют узкой совместимой правки.

## Цель

Перевести Pythia с исходного плана bare-metal деплоя на Hetzner на Docker-first deployment-модель на существующем Oracle-сервере `amper`, сохранив при этом максимально возможную операционную изоляцию от `SproutSignal`.

Этот дизайн заменяет `setup.sh + host cron` как основной способ деплоя. Docker-путь становится единственным активным deployment-сценарием в документации и повседневной эксплуатации.

## Язык документации

Активная документация проекта Pythia ведётся на русском языке.

Это правило относится к:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/DEPLOYMENT.md`
- новым operational runbook-файлам
- новым decision/spec-документам

Допускаются английские технические термины, имена команд, названия файлов, имена переменных окружения и названия внешних продуктов, если их перевод ухудшает точность.

## Ограничения

- Pythia и SproutSignal работают на одном хосте, поэтому полная изоляция на уровне kernel или Docker daemon невозможна.
- В пределах этого ограничения Pythia должна быть изолирована операционно:
  - отдельный Linux user
  - отдельный путь к репозиторию
  - отдельный Docker Compose project
  - отдельная network
  - отдельные data и logs
  - отсутствие входящих портов
  - отсутствие общих volumes и runtime-путей
- Pythia остаётся batch-системой с cron-логикой:
  - ежедневный forecast run
  - ежедневная проверка resolved markets
  - еженедельная аналитика и Telegram-reporting
- Бизнес-логика в `src/**` и `python/**` должна по возможности остаться стабильной и переиспользуемой.

## Текущий контекст хоста

- Хост: `amper`
- Уже работающий проект на хосте: `SproutSignal`
- По CPU, RAM и диску у хоста есть достаточный запас для Pythia.
- Pythia не требует публичного HTTP ingress.
- Pythia нужны только исходящие HTTPS-запросы к DeepSeek, Polymarket, Exa или Tavily и Telegram.

## Рассмотренные варианты

### Вариант A: отдельный VPS

Развернуть Pythia на отдельном сервере, как планировалось изначально.

Плюсы:
- самая сильная граница изоляции
- отсутствие shared-host риска со SproutSignal

Минусы:
- дополнительные ежемесячные расходы
- ещё один хост в обслуживании
- избыточно для текущих resource-требований Pythia

### Вариант B: shared host, нативный runtime на хосте

Установить Bun, Python и cron напрямую на `amper`.

Плюсы:
- простой старт
- нет container-overhead

Минусы:
- более слабая операционная изоляция
- выше риск пересечений по путям, пользователям, cron и runtime
- сложнее обеспечить воспроизводимость зависимостей

### Вариант C: shared host, Docker-first deployment

Запустить Pythia на `amper` в собственном Docker Compose stack под выделенным пользователем.

Плюсы:
- сильная операционная изоляция на существующем хосте
- воспроизводимый runtime
- простой цикл rebuild/update
- не требуется открывать порты наружу
- нет зависимости от runtime-пакетов хоста

Минусы:
- всё ещё общий kernel и общий Docker daemon со SproutSignal
- container image должен корректно собирать Bun, Python и зависимости Pythia на `amd64` и `arm64`

## Выбранный вариант

Утверждён вариант C.

Pythia будет развёрнута на `amper` под отдельным Linux user `pythia`, в каталоге `/home/pythia/pythia`, со своим Docker Compose project и внутренним scheduler на базе `supercronic`.

## Архитектура деплоя

### Изоляция на уровне хоста

- Linux user: `pythia`
- путь к репозиторию: `/home/pythia/pythia`
- все команды выполняются только из этого каталога и только от этого пользователя
- Pythia нельзя эксплуатировать из `/home/ubuntu/SproutSignal`

### Compose project

- имя Compose project: `pythia`
- основной long-running service: `pythia-scheduler`
- отдельная bridge-network: `pythia_internal`
- без опубликованных портов
- без общих networks со SproutSignal

### Runtime-модель

- Один application image содержит:
  - Bun runtime
  - Python 3.11 runtime
  - SQLite tools
  - код приложения
  - собственный research-слой Pythia
  - `supercronic`
- Scheduler-контейнер работает постоянно и запускает batch-задачи по расписанию.
- Ручные операции используют тот же image, чтобы не было расхождения между host-runtime и production-runtime.

## Дизайн сборки контейнера

### Базовый image

В качестве базового Docker image используется `python:3.11-slim`, а не `ubuntu:22.04`.

Причины:

- гарантированный Python 3.11 в multi-arch image
- более простой package management
- меньший размер image
- меньше движущихся частей при сборке

### Необходимые системные пакеты

Image должен устанавливать минимальный набор пакетов, необходимых Pythia и её build-chain:

- `curl`
- `git`
- `jq`
- `sqlite3`
- `ca-certificates`
- `unzip`
- `procps`
- `build-essential`
- `pkg-config`

Причины:

- установщик Bun требует `unzip`
- healthcheck использует `pgrep` из `procps`
- `better-sqlite3` может потребовать нативную сборку, особенно на ARM

### Установка Bun

- Bun устанавливается внутри image
- бинарник Bun должен быть доступен через `PATH`
- host Bun для активного runtime не требуется

### Supercronic

- `supercronic` устанавливается внутрь image
- используется architecture-aware binary, выбранный во время Docker build
- `supercronic` становится scheduler-процессом в long-running контейнере

### Research layer

- production-образ не включает Dexter runtime
- внутри image живёт только собственный research-слой Pythia
- обновление research-логики привязано к обычной пересборке image

## Дизайн Compose service

### Service: `pythia-scheduler`

Задачи сервиса:

- запускать `supercronic`
- выполнять forecast, resolution и weekly-задачи по расписанию
- писать логи в stdout и в bind-mounted log-файлы там, где это требуется

Ключевые свойства:

- `restart: unless-stopped`
- `env_file: .env`
- `TZ=UTC`
- bind mounts:
  - `./data:/app/data`
  - `./logs:/app/logs`
- без bind-mount для `.env`
- без опубликованных портов
- подключён только к `pythia_internal`

### Ресурсные ограничения

Для реального enforcement используются service-level runtime limits Docker Compose:

- `mem_limit: 2g`
- `cpus: 1.0`
- `pids_limit: 256`

Причины:

- защита SproutSignal от случайного resource starvation
- соответствие низкому ожидаемому resource-профилю Pythia

### Логирование

- Docker JSON logging driver с ротацией
- application logs продолжают писаться в `/app/logs`
- хранение логов должно быть ограничено

### Healthcheck

- healthcheck scheduler-контейнера должен проверять, что `supercronic` жив
- используется простой `pgrep`-based check

## Модель расписания

`supercronic.crontab` становится единственным активным определением расписания.

Планируемые задачи:

- `06:00 UTC` -> forecast run
- `18:00 UTC` -> resolution checker
- `Sunday 20:00 UTC` -> analysis + weekly Telegram report

Host cron для Pythia в активной deployment-модели отсутствует.

## Entrypoint и ручные операции

### `scripts/entrypoint.sh`

Единый operational entrypoint для режимов:

- `forecast`
- `resolve`
- `weekly`
- `scheduler`
- `shell`

Ответственность:

- экспортировать container-default paths
- направлять subcommands в нужные Bun или Python entrypoints
- добавлять output задач в log-файлы с датой

### `scripts/manual_run.sh`

Host helper для оператора. Он оборачивает `docker compose run` или `docker compose logs` для таких сценариев:

- ручной forecast
- ручной resolution run
- weekly report
- edge calculation
- DB shell
- logs
- shell внутри контейнера
- status summary

Этот helper становится основным интерфейсом для рутинной эксплуатации.

## Данные и секреты

### Постоянные данные

Постоянные каталоги:

- `/home/pythia/pythia/data`
- `/home/pythia/pythia/logs`

Пути внутри контейнера:

- `/app/data`
- `/app/logs`

Значение по умолчанию для SQLite database path:

- `DB_PATH=/app/data/pythia.db`

Значение по умолчанию для log directory:

- `LOG_DIR=/app/logs`

### Секреты

- `.env` хранится только в `/home/pythia/pythia/.env`
- секреты передаются через `env_file`
- `.env` не bind-mountится в контейнер как обычный файл

Причины:

- более простой runtime
- меньше риск случайного file-level exposure секрета внутри контейнера

## Изменения в документации

### README

Активный quick start переписывается под Docker-first deployment на `amper`:

- создать user `pythia`
- клонировать repo в `/home/pythia/pythia`
- скопировать `.env.example` в `.env`
- выполнить `scripts/day0_check.sh`
- выполнить `docker compose build`
- выполнить `bash scripts/manual_run.sh forecast`
- выполнить `docker compose up -d`

Также добавляется раздел `Operations` с командами:

- `bash scripts/manual_run.sh status`
- `bash scripts/manual_run.sh logs`
- `bash scripts/manual_run.sh edge`
- `git pull && docker compose build && docker compose up -d`

### `docs/ARCHITECTURE.md`

Добавляется deployment-раздел с описанием:

- shared host `amper`
- отдельный user `pythia`
- изолированный Compose project и network
- отсутствие входящих портов
- bind-mounted data и logs
- операционная изоляция от SproutSignal с оговоркой про shared host

### `docs/DEPLOYMENT.md`

Создаётся Docker deployment runbook, который покрывает:

- подготовку хоста
- настройку пользователя
- clone и конфигурацию
- первую сборку
- smoke test
- запуск scheduler
- процесс обновления
- operational safety и правила изоляции

### Legacy deployment references

Старый Hetzner bare-metal путь удаляется из активной документации.

Это означает:

- в активном `README.md` больше нет инструкций через `scripts/setup.sh`
- в активном `README.md` больше нет инструкций через host `crontab`
- в проекте не остаётся двусмысленной истории с двумя равноправными deployment-путями

Если legacy-файлы временно остаются в репозитории, они должны быть явно деактивированы или удалены на этапе реализации.

## Файлы в объёме реализации

### Новые файлы

- `Dockerfile`
- `docker-compose.yml`
- `supercronic.crontab`
- `.dockerignore`
- `scripts/entrypoint.sh`
- `scripts/manual_run.sh`
- `docs/DEPLOYMENT.md`

### Обновляемые файлы

- `README.md`
- `docs/ARCHITECTURE.md`
- `.env.example`

### Вне объёма, если только не потребуется для container-compatibility

- `src/**`
- `python/**`

## Правила операционной безопасности

- Вся работа с Pythia выполняется под пользователем `pythia`
- Все команды Pythia запускаются из `/home/pythia/pythia`
- У Pythia нет общей Docker network со SproutSignal
- У Pythia нет общих bind mounts со SproutSignal
- Pythia не публикует HTTP-порты
- Активный runtime не должен зависеть от host Bun или host Python

## Критерии готовности deployment-слоя

Docker deployment layer считается готовым к реализации, если он поддерживает такой операторский поток:

1. Клонировать Pythia в `/home/pythia/pythia`
2. Создать `.env`
3. Собрать image через `docker compose build`
4. Выполнить ручной forecast через `bash scripts/manual_run.sh forecast`
5. Подтвердить, что записи появляются в SQLite в каталоге `data/`
6. Запустить scheduler через `docker compose up -d`
7. Наблюдать ежедневные и еженедельные задачи через container logs и log-файлы

## Что не входит в этот дизайн

- web UI
- входящий Telegram webhook service
- host-based cron для Pythia
- shared runtime со SproutSignal
- изменение продуктового scope прогнозной системы
