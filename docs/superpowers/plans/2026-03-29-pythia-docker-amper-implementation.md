# Pythia Docker Deployment on `amper` Implementation Plan

> Исторический план: документ отражает план на момент написания. Актуальная production-реализация уже не использует Dexter runtime; текущее состояние системы описано в `README.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md` и `docs/AUDIT.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести Pythia на Docker-first deployment на `amper`, изолировать её от `SproutSignal`, обновить документацию на русском и развернуть рабочий scheduler-контейнер на сервере.

**Architecture:** Pythia будет запускаться в отдельном Docker Compose project `pythia` под пользователем `pythia`, без опубликованных портов и без общих runtime-путей со `SproutSignal`. Один image содержит Bun, Python 3.11, собственный research-слой и `supercronic`; long-running контейнер `pythia-scheduler` выполняет задачи по расписанию, а ручные операции идут через `scripts/manual_run.sh`.

**Tech Stack:** Docker Compose, `python:3.11-slim`, Bun, SQLite, Bash, supercronic, GitHub, SSH, Oracle Cloud `amper`

---

## Карта файлов

### Создать

- `.dockerignore` — исключения для Docker build context
- `Dockerfile` — container image с Python 3.11, Bun, собственным research-слоем и supercronic
- `docker-compose.yml` — isolated Compose stack `pythia`
- `supercronic.crontab` — расписание batch-задач внутри контейнера
- `scripts/entrypoint.sh` — единый operational entrypoint
- `scripts/manual_run.sh` — ручной запуск и диагностика через Docker Compose
- `docs/DEPLOYMENT.md` — production runbook на русском
- `docs/superpowers/plans/2026-03-29-pythia-docker-amper-implementation.md` — этот план

### Изменить

- `.env.example` — контейнерные пути `/app/data` и `/app/logs`
- `README.md` — Docker-first quick start, operations, стоимость без отдельного VPS
- `docs/ARCHITECTURE.md` — deployment architecture на `amper`, изоляция от `SproutSignal`

### Удалить

- `scripts/setup.sh` — legacy Hetzner bare-metal setup
- `scripts/crontab.txt` — legacy host cron

### Не трогать без крайней необходимости

- `src/**`
- `python/**`
- `scripts/day0_check.sh`

---

### Task 1: Добавить Docker deployment scaffold

**Files:**
- Create: `.dockerignore`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `supercronic.crontab`
- Modify: `.env.example`

- [ ] **Step 1: Подтвердить, что Docker deployment-файлов ещё нет**

```bash
test ! -f Dockerfile
test ! -f docker-compose.yml
test ! -f supercronic.crontab
```

Expected: команды завершаются успешно без вывода.

- [ ] **Step 2: Создать `.dockerignore`**

```gitignore
.git
.github
.env
.env.local
.DS_Store
Thumbs.db

node_modules/
dist/
bun.lockb

python/venv/
python/__pycache__/
*.pyc

dexter/

data/*
!data/.gitkeep
logs/*
!logs/.gitkeep

*.db
*.sqlite
*.png
*.jpg
```

- [ ] **Step 3: Создать `Dockerfile`**

```dockerfile
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC
ENV BUN_INSTALL=/root/.bun
ENV PATH=/root/.bun/bin:$PATH

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    jq \
    pkg-config \
    procps \
    sqlite3 \
    unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://bun.sh/install | bash

ARG TARGETARCH
RUN case "${TARGETARCH}" in \
      amd64) SUPERCRONIC_ARCH="amd64" ;; \
      arm64) SUPERCRONIC_ARCH="arm64" ;; \
      *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-${SUPERCRONIC_ARCH}" -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

COPY package.json tsconfig.json bunfig.toml /app/
RUN bun install

COPY python/requirements.txt /app/python/requirements.txt
RUN python3 -m venv /app/python/venv \
    && /app/python/venv/bin/pip install --no-cache-dir -r /app/python/requirements.txt

RUN git clone --depth 1 https://github.com/virattt/dexter.git /app/dexter \
    && cd /app/dexter \
    && bun install

COPY src /app/src
COPY python /app/python
COPY scripts /app/scripts
COPY supercronic.crontab /app/supercronic.crontab
COPY .env.example /app/.env.example
COPY README.md LICENSE /app/

RUN chmod +x /app/scripts/*.sh \
    && mkdir -p /app/data /app/logs

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["scheduler"]
```

- [ ] **Step 4: Создать `docker-compose.yml` и `supercronic.crontab`, затем обновить `.env.example`**

`docker-compose.yml`

```yaml
name: pythia

services:
  pythia-scheduler:
    build:
      context: .
      dockerfile: Dockerfile
    image: pythia:latest
    container_name: pythia-scheduler
    restart: unless-stopped
    env_file:
      - .env
    environment:
      TZ: UTC
      DB_PATH: ${DB_PATH:-/app/data/pythia.db}
      LOG_DIR: ${LOG_DIR:-/app/logs}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    command: ["scheduler"]
    networks:
      - pythia_internal
    mem_limit: 2g
    cpus: 1.0
    pids_limit: 256
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD-SHELL", "pgrep -f supercronic >/dev/null || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3

networks:
  pythia_internal:
    name: pythia_internal
    driver: bridge
```

`supercronic.crontab`

```cron
0 6 * * * /app/scripts/entrypoint.sh forecast
0 18 * * * /app/scripts/entrypoint.sh resolve
0 20 * * 0 /app/scripts/entrypoint.sh weekly
```

`.env.example`

```env
# === LLM API ===
# DeepSeek напрямую:
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# ИЛИ через OpenRouter:
# OPENROUTER_API_KEY=sk-or-xxx
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# === Модели ===
MODEL_RESEARCH=deepseek-chat
MODEL_SYNTHESIS=deepseek-reasoner

# === News Search ===
EXA_API_KEY=xxx
# TAVILY_API_KEY=xxx  # fallback

# === Telegram ===
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# === Limits ===
DAILY_COST_LIMIT_USD=2.00
MONTHLY_COST_LIMIT_USD=50.00
MARKETS_PER_RUN=20

# === Paths ===
DB_PATH=/app/data/pythia.db
LOG_DIR=/app/logs
```

- [ ] **Step 5: Проверить синтаксис compose и контейнерные пути**

Run:

```bash
docker compose config
grep '^DB_PATH=/app/data/pythia.db$' .env.example
grep '^LOG_DIR=/app/logs$' .env.example
```

Expected:
- `docker compose config` печатает валидный merged config с сервисом `pythia-scheduler`
- оба `grep` возвращают по одной строке

- [ ] **Step 6: Commit**

```bash
git add .dockerignore Dockerfile docker-compose.yml supercronic.crontab .env.example
git commit -m "Add Docker deployment scaffold"
```

---

### Task 2: Добавить operational scripts и удалить legacy bare-metal scripts

**Files:**
- Create: `scripts/entrypoint.sh`
- Create: `scripts/manual_run.sh`
- Delete: `scripts/setup.sh`
- Delete: `scripts/crontab.txt`

- [ ] **Step 1: Подтвердить, что новых scripts ещё нет, а legacy scripts пока существуют**

```bash
test ! -f scripts/entrypoint.sh
test ! -f scripts/manual_run.sh
test -f scripts/setup.sh
test -f scripts/crontab.txt
```

Expected: все четыре команды завершаются успешно.

- [ ] **Step 2: Создать `scripts/entrypoint.sh`**

```bash
#!/bin/bash
set -euo pipefail

export PATH="/root/.bun/bin:$PATH"
export DB_PATH="${DB_PATH:-/app/data/pythia.db}"
export LOG_DIR="${LOG_DIR:-/app/logs}"

mkdir -p /app/data "$LOG_DIR"

COMMAND="${1:-scheduler}"
TODAY="$(date -u +%Y%m%d)"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

case "$COMMAND" in
  forecast)
    echo "[$STAMP] === Pythia Forecast Run ==="
    cd /app
    bun run src/main.ts 2>&1 | tee -a "$LOG_DIR/forecast_${TODAY}.log"
    ;;

  resolve)
    echo "[$STAMP] === Resolution Check ==="
    cd /app
    /app/python/venv/bin/python python/resolution_checker.py 2>&1 | tee -a "$LOG_DIR/resolution_${TODAY}.log"
    ;;

  weekly)
    echo "[$STAMP] === Weekly Report ==="
    cd /app
    {
      /app/python/venv/bin/python python/analyze.py
      /app/python/venv/bin/python python/telegram_bot.py --weekly-report
    } 2>&1 | tee -a "$LOG_DIR/weekly_${TODAY}.log"
    ;;

  scheduler)
    echo "[$STAMP] === Scheduler Starting ==="
    exec supercronic -json /app/supercronic.crontab
    ;;

  shell)
    exec /bin/bash
    ;;

  *)
    exec "$@"
    ;;
esac
```

- [ ] **Step 3: Создать `scripts/manual_run.sh`**

```bash
#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVICE="pythia-scheduler"
ENTRYPOINT="/app/scripts/entrypoint.sh"
COMMAND="${1:-help}"

case "$COMMAND" in
  forecast)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" forecast
    ;;

  resolve)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" resolve
    ;;

  weekly)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" weekly
    ;;

  edge)
    docker compose run --rm --entrypoint /app/python/venv/bin/python "$SERVICE" /app/python/edge_calculator.py
    ;;

  db)
    docker compose run --rm --entrypoint sqlite3 "$SERVICE" /app/data/pythia.db
    ;;

  logs)
    docker compose logs -f --tail=100 "$SERVICE"
    ;;

  shell)
    docker compose run --rm --entrypoint /bin/bash "$SERVICE"
    ;;

  status)
    docker compose ps
    echo ""
    echo "=== Forecast Summary ==="
    docker compose run --rm --entrypoint sqlite3 "$SERVICE" /app/data/pythia.db \
      "SELECT date(forecast_timestamp) AS day, COUNT(*) AS predictions, ROUND(COALESCE(SUM(api_cost_usd), 0), 4) AS cost FROM predictions GROUP BY day ORDER BY day DESC LIMIT 5;"
    echo ""
    echo "=== Resolution Summary ==="
    docker compose run --rm --entrypoint sqlite3 "$SERVICE" /app/data/pythia.db \
      "SELECT COUNT(*) AS total, SUM(CASE WHEN outcome IS NOT NULL AND outcome IN (0,1) THEN 1 ELSE 0 END) AS resolved FROM predictions;"
    ;;

  help|*)
    cat <<'EOF'
Pythia Manual Runner

Usage: bash scripts/manual_run.sh <command>

Commands:
  forecast  - run forecast manually
  resolve   - run resolution checker
  weekly    - generate weekly report
  edge      - calculate edge and Brier scores
  db        - open SQLite shell
  logs      - tail scheduler logs
  shell     - open shell in the container
  status    - show compose and database status
  help      - show this message
EOF
    ;;
esac
```

- [ ] **Step 4: Удалить legacy `setup.sh` и `crontab.txt`**

```bash
git rm scripts/setup.sh scripts/crontab.txt
chmod +x scripts/entrypoint.sh scripts/manual_run.sh
```

Expected: `git rm` помечает два файла как удалённые.

- [ ] **Step 5: Проверить shell scripts**

Run:

```bash
bash -n scripts/day0_check.sh
bash -n scripts/entrypoint.sh
bash -n scripts/manual_run.sh
```

Expected: все три команды завершаются успешно без вывода.

- [ ] **Step 6: Commit**

```bash
git add scripts/entrypoint.sh scripts/manual_run.sh
git commit -m "Add Docker operational scripts"
```

---

### Task 3: Переписать активную документацию на русский Docker-first путь

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Create: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Подтвердить, что в документации ещё есть legacy bare-metal references**

```bash
rg -n "setup.sh|crontab.txt|Hetzner|CX22" README.md docs/ARCHITECTURE.md || true
test ! -f docs/DEPLOYMENT.md
```

Expected:
- `rg` находит legacy refs
- `test ! -f docs/DEPLOYMENT.md` завершается успешно

- [ ] **Step 2: Переписать `README.md`**

```markdown
# Pythia

Автономная система прогнозной аналитики для prediction markets.

Pythia ежедневно забирает бинарные маркеты с Polymarket, строит прогнозы,
сохраняет результаты в SQLite, отслеживает resolved markets, считает Brier score
и отправляет отчёты в Telegram.

## Архитектура

```text
Polymarket Gamma API -> Market Selection -> [Research Layer | Naked R1 Baseline]
-> DeepSeek R1 Synthesis -> SQLite -> Brier Score Tracking -> Telegram Reports
```

## Быстрый старт на `amper`

```bash
# 1. Под пользователем pythia
cd /home/pythia
git clone git@github.com:Ger-Wer/pythia.git
cd pythia

# 2. Настройка окружения
cp .env.example .env
nano .env

# 3. Проверка доступности маркетов
bash scripts/day0_check.sh

# 4. Сборка image
docker compose build

# 5. Ручной smoke test
bash scripts/manual_run.sh forecast
bash scripts/manual_run.sh status

# 6. Запуск scheduler
docker compose up -d
docker compose ps
```

## Operations

```bash
# Статус
bash scripts/manual_run.sh status

# Логи
bash scripts/manual_run.sh logs

# Проверка edge
bash scripts/manual_run.sh edge

# Обновление
git pull
docker compose build
docker compose up -d
```

## Decision Framework

После 4 недель и примерно 50 resolved predictions:

```bash
bash scripts/manual_run.sh edge
```

- `GO` -> переход к Phase 2
- `PIVOT` -> доработка фильтров и промптов
- `STOP` -> система не показывает edge против market price

## Cost

Отдельный VPS не требуется: Pythia работает на существующем сервере `amper`.
Переменные расходы зависят от API-вызовов DeepSeek и search-провайдера.
```

- [ ] **Step 3: Обновить `docs/ARCHITECTURE.md` и создать `docs/DEPLOYMENT.md`**

`docs/ARCHITECTURE.md`

```markdown
# Архитектура Pythia

## Основные компоненты

- **Market Selector**: Gamma REST API -> фильтрация бинарных рынков
- **Research Layer**: сбор research brief по рынку
- **Naked R1**: базовый single-call прогноз без research
- **Synthesis**: финальная вероятностная оценка через DeepSeek R1
- **Tracker**: SQLite, Brier score, cost tracking
- **Alerts**: Telegram Bot API для ошибок, edge alerts и weekly report

## Deployment Architecture

- Хост: `amper`
- Отдельный пользователь: `pythia`
- Путь: `/home/pythia/pythia`
- Docker Compose project: `pythia`
- Сервис: `pythia-scheduler`
- Scheduler: `supercronic`
- Входящие порты: отсутствуют
- Постоянные данные: `./data`, `./logs`
- Сетевое разделение: отдельная bridge-network `pythia_internal`

## Изоляция от SproutSignal

Pythia и SproutSignal делят один physical host, один kernel и один Docker daemon,
но не делят:

- пользователя
- директорию проекта
- Compose project
- bind mounts
- docker network
- входящие порты

## Design Decisions

1. Docker-first deployment вместо bare-metal setup
2. Один scheduler-контейнер вместо host cron
3. SQLite вместо PostgreSQL из-за низкой write-конкуренции
4. Отдельный `pythia` user для операционной изоляции
5. Без web UI и без inbound services
```

`docs/DEPLOYMENT.md`

```markdown
# Деплой Pythia на `amper`

## 1. Подготовка хоста

Под root или sudo-пользователем:

```bash
docker --version
docker compose version
```

## 2. Создание пользователя `pythia`

```bash
sudo adduser --disabled-password --gecos "" pythia
sudo usermod -aG docker pythia
sudo install -d -m 700 -o pythia -g pythia /home/pythia/.ssh
sudo cp /home/ubuntu/.ssh/authorized_keys /home/pythia/.ssh/authorized_keys
sudo chown pythia:pythia /home/pythia/.ssh/authorized_keys
sudo chmod 600 /home/pythia/.ssh/authorized_keys
```

## 3. GitHub deploy key

Под пользователем `pythia`:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_github -N "" -C "pythia@amper"
cat ~/.ssh/id_ed25519_github.pub
```

Добавить публичный ключ как Deploy Key в репозиторий `Ger-Wer/pythia` с write access.

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_github
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com
```

## 4. Первый деплой

```bash
cd /home/pythia
git clone git@github.com:Ger-Wer/pythia.git
cd pythia
cp .env.example .env
nano .env
mkdir -p data logs

bash scripts/day0_check.sh
docker compose build
bash scripts/manual_run.sh forecast
bash scripts/manual_run.sh status
docker compose up -d
docker compose ps
```

## 5. Обновление

```bash
cd /home/pythia/pythia
git pull
docker compose build
docker compose up -d
bash scripts/manual_run.sh status
```

## 6. Безопасность эксплуатации

- Все команды выполнять под пользователем `pythia`
- Не запускать Pythia из `/home/ubuntu/SproutSignal`
- Не публиковать порты
- Не создавать общие volumes и networks со SproutSignal
- Не хранить секреты вне `/home/pythia/pythia/.env`
```

- [ ] **Step 4: Убедиться, что legacy refs удалены из активной документации**

Run:

```bash
rg -n "setup.sh|crontab.txt|Hetzner|CX22" README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md
```

Expected: пустой вывод.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md
git commit -m "Rewrite docs for Docker deployment on amper"
```

---

### Task 4: Проверить репозиторий и опубликовать Docker-first изменения в GitHub

**Files:**
- Modify if needed after verification: `Dockerfile`
- Modify if needed after verification: `docker-compose.yml`
- Modify if needed after verification: `scripts/entrypoint.sh`
- Modify if needed after verification: `scripts/manual_run.sh`
- Modify if needed after verification: `README.md`
- Modify if needed after verification: `docs/ARCHITECTURE.md`
- Modify if needed after verification: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Прогнать локальные статические проверки**

Run:

```bash
docker compose config
bash -n scripts/day0_check.sh
bash -n scripts/entrypoint.sh
bash -n scripts/manual_run.sh
npx tsc --noEmit
python3 -m py_compile python/*.py
```

Expected:
- `docker compose config` печатает merged config без ошибок
- shell scripts проходят `bash -n`
- TypeScript typecheck проходит
- Python scripts компилируются без traceback

- [ ] **Step 2: Проверить рабочее дерево и при необходимости сделать финальные правки**

Run:

```bash
git status --short
```

Expected: если verification ничего не сломал, вывод пустой. Если нет — исправить только те файлы, которые реально не прошли проверки.

- [ ] **Step 3: Опубликовать изменения в GitHub**

```bash
git push origin main
git status --short --branch
```

Expected:
- `git push` завершается успешно
- `git status --short --branch` показывает `## main...origin/main`

- [ ] **Step 4: Commit only if verification required fixes**

```bash
git add Dockerfile docker-compose.yml supercronic.crontab scripts/entrypoint.sh scripts/manual_run.sh README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md .env.example .dockerignore
git commit -m "Fix Docker deployment verification issues"
git push origin main
```

Expected: этот шаг выполнять только если Step 2 показал новые непушенные правки.

---

### Task 5: Подготовить изолированную среду `pythia` на `amper`

**Files:**
- Host create: `/home/pythia`
- Host create: `/home/pythia/.ssh`
- Host create: `/home/pythia/.ssh/config`
- Host create: `/home/pythia/.ssh/id_ed25519_github`
- Host create: `/home/pythia/.ssh/id_ed25519_github.pub`

- [ ] **Step 1: Проверить Docker на `amper` и создать пользователя `pythia`**

Run on host:

```bash
ssh ubuntu@amper
docker --version
docker compose version
sudo adduser --disabled-password --gecos "" pythia || true
sudo usermod -aG docker pythia
```

Expected:
- Docker и Compose доступны
- пользователь `pythia` существует и входит в группу `docker`

- [ ] **Step 2: Дать пользователю `pythia` SSH-доступ на сам хост**

Run on host:

```bash
sudo install -d -m 700 -o pythia -g pythia /home/pythia/.ssh
sudo cp /home/ubuntu/.ssh/authorized_keys /home/pythia/.ssh/authorized_keys
sudo chown pythia:pythia /home/pythia/.ssh/authorized_keys
sudo chmod 600 /home/pythia/.ssh/authorized_keys
```

Expected: пользователь `pythia` может войти на сервер по SSH тем же административным ключом.

- [ ] **Step 3: Создать отдельный GitHub deploy key для `pythia`**

Run on host:

```bash
sudo su - pythia
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_github -N "" -C "pythia@amper"
cat ~/.ssh/id_ed25519_github.pub
```

Expected: в терминале напечатан публичный ключ.

- [ ] **Step 4: Привязать deploy key к GitHub-репозиторию и настроить SSH config**

В GitHub открыть:
- `Ger-Wer/pythia`
- `Settings`
- `Deploy keys`
- `Add deploy key`
- вставить содержимое `~/.ssh/id_ed25519_github.pub`
- включить `Allow write access`

После этого на хосте выполнить:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_github
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com
```

Expected: GitHub отвечает сообщением об успешной аутентификации SSH.

- [ ] **Step 5: Подготовить рабочий каталог**

Run on host:

```bash
mkdir -p /home/pythia
cd /home/pythia
test ! -d /home/pythia/pythia
```

Expected: каталог `/home/pythia/pythia` ещё не существует перед первым clone.

---

### Task 6: Выполнить первый деплой Pythia на `amper` и включить scheduler

**Files:**
- Host create: `/home/pythia/pythia`
- Host create: `/home/pythia/pythia/.env`
- Host create: `/home/pythia/pythia/data`
- Host create: `/home/pythia/pythia/logs`

- [ ] **Step 1: Клонировать репозиторий и подготовить `.env`**

Run on host as `pythia`:

```bash
cd /home/pythia
git clone git@github.com:Ger-Wer/pythia.git
cd /home/pythia/pythia
cp .env.example .env
mkdir -p data logs
nano .env
```

Expected:
- репозиторий склонирован в `/home/pythia/pythia`
- `.env` создан и заполнен рабочими ключами

- [ ] **Step 2: Выполнить `day0_check` и зафиксировать результат**

Run on host:

```bash
cd /home/pythia/pythia
bash scripts/day0_check.sh
```

Expected:
- скрипт завершается без shell error
- если live market supply снова низкий, это фиксируется как внешний signal, а не как ошибка deployment-слоя

- [ ] **Step 3: Собрать image и проверить ручной forecast run**

Run on host:

```bash
cd /home/pythia/pythia
docker compose build
bash scripts/manual_run.sh forecast
bash scripts/manual_run.sh status
```

Expected:
- image собирается без ошибок
- ручной forecast run завершается
- `status` показывает строки из SQLite или как минимум созданную базу и summary-запросы без падения

- [ ] **Step 4: Включить scheduler**

Run on host:

```bash
cd /home/pythia/pythia
docker compose up -d
docker compose ps
docker compose logs --tail=50 pythia-scheduler
```

Expected:
- сервис `pythia-scheduler` в состоянии `Up`
- в логах виден запуск `supercronic`

- [ ] **Step 5: Проверить, что Pythia не мешает SproutSignal**

Run on host:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker network inspect pythia_internal --format '{{json .Containers}}'
```

Expected:
- `pythia-scheduler` виден как отдельный контейнер
- у него нет опубликованных портов
- network `pythia_internal` не содержит контейнеров `SproutSignal`

---

### Task 7: Выполнить post-deploy smoke test и задокументировать операционный baseline

**Files:**
- Modify if needed: `docs/DEPLOYMENT.md`
- Modify if needed: `README.md`

- [ ] **Step 1: Проверить ежедневные операционные команды**

Run on host:

```bash
cd /home/pythia/pythia
bash scripts/manual_run.sh logs
```

Expected: tail логов открывается без ошибок. Остановить просмотр вручную через `Ctrl+C`.

- [ ] **Step 2: Проверить resolution и weekly pipeline вручную**

Run on host:

```bash
cd /home/pythia/pythia
bash scripts/manual_run.sh resolve
bash scripts/manual_run.sh weekly
```

Expected:
- оба режима завершаются без падения shell wrapper
- если данных пока мало, Python-скрипты сообщают об этом корректно, без traceback

- [ ] **Step 3: Зафиксировать baseline в документации только если реальный запуск выявил расхождения**

If needed, update:

```markdown
- точные команды, которые использовались на `amper`
- фактическое имя пользователя
- подтверждённые проверки `docker compose ps`, `manual_run.sh status`, `manual_run.sh logs`
- observed caveat про текущую доступность маркетов в `day0_check`
```

Then run:

```bash
git add README.md docs/DEPLOYMENT.md
git commit -m "Update deployment docs after amper smoke test"
git push origin main
```

Expected: выполнять только если post-deploy smoke test выявил реальные расхождения между runbook и фактической процедурой.

---

## Self-Review Checklist

### Покрытие спецификации

- Docker-first deployment вместо Hetzner bare-metal: Task 1, Task 2, Task 3
- Отдельный пользователь `pythia` и отдельный путь `/home/pythia/pythia`: Task 5, Task 6
- Отдельный Compose project и network: Task 1, Task 6
- Без входящих портов: Task 1, Task 6
- `supercronic` как scheduler: Task 1, Task 2, Task 6
- Документация на русском: Task 3
- Удаление active docs про `setup.sh` и host cron: Task 2, Task 3
- Push в GitHub: Task 4
- Реальный deploy на `amper`: Task 5, Task 6, Task 7

### Placeholder scan

- В плане нет служебных заглушек или ссылок вида “сделать как в задаче выше”
- Все команды указаны явно
- Для создаваемых ключевых файлов дано точное содержимое

### Consistency check

- `docker-compose.yml` использует `ENTRYPOINT ["/app/scripts/entrypoint.sh"]` и `command: ["scheduler"]`
- `scripts/manual_run.sh` использует `--entrypoint "$ENTRYPOINT"` для ручных subcommands
- `.env.example` согласована с container paths `/app/data` и `/app/logs`
