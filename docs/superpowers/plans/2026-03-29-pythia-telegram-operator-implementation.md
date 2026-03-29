# Pythia Telegram Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в Pythia отдельного Telegram-бота-оператора, который в личном чате запускает задачи, показывает состояние системы и отдаёт результаты на русском языке.

**Architecture:** Отдельный сервис `pythia-telegram` работает в Docker Compose рядом с `pythia-scheduler`, использует тот же image `pythia:latest`, читает те же bind-mounted `data` и `logs`, а команды выполняет через существующие entrypoints и read-only доступ к SQLite. Для просмотра health контейнеров бот читает Docker Engine API через bind-mounted `/var/run/docker.sock`, не открывая внешние порты.

**Tech Stack:** Python 3.11, requests, sqlite3, subprocess, Docker Compose, Telegram Bot API, Bash

---

## Карта файлов

### Создать

- `python/__init__.py` — делает каталог `python/` импортируемым для `unittest`
- `python/operator_actions.py` — чистые функции для команд бота: запуск задач, чтение SQLite, чтение логов, health/status
- `python/telegram_operator.py` — polling loop, авторизация, routing команд, `setMyCommands`, отправка сообщений
- `python/test_telegram_operator.py` — unit tests на нормализацию команд, авторизацию и форматирование ответов
- `docs/superpowers/plans/2026-03-29-pythia-telegram-operator-implementation.md` — этот план

### Изменить

- `.env.example` — добавить `TELEGRAM_OPERATOR_USER_ID=405690972`
- `docker-compose.yml` — добавить сервис `pythia-telegram`
- `scripts/entrypoint.sh` — добавить режим `telegram`
- `scripts/manual_run.sh` — расширить `logs` и `status` на оба сервиса
- `README.md` — описать Telegram-оператора и команды
- `docs/ARCHITECTURE.md` — добавить контейнер `pythia-telegram`
- `docs/DEPLOYMENT.md` — добавить запуск, проверку и использование operator bot

### Не трогать без необходимости

- `src/**`
- `python/resolution_checker.py`
- `python/telegram_bot.py`
- `python/edge_calculator.py`

---

### Task 1: Подготовить тесты и каркас operator action helpers

**Files:**
- Create: `python/__init__.py`
- Create: `python/test_telegram_operator.py`
- Create: `python/operator_actions.py`

- [ ] **Step 1: Создать пакет `python/` для импортов в `unittest`**

```python
# python/__init__.py
"""Pythia Python helpers."""
```

- [ ] **Step 2: Написать падающие unit tests на нормализацию команд, авторизацию и обрезку длинных сообщений**

```python
# python/test_telegram_operator.py
import unittest

from python.operator_actions import (
    TELEGRAM_MESSAGE_LIMIT,
    is_authorized_update,
    normalize_command,
    trim_message,
)


class TelegramOperatorTests(unittest.TestCase):
    def test_normalize_slash_and_russian_aliases(self):
        self.assertEqual(normalize_command("/status"), "status")
        self.assertEqual(normalize_command("/status@pythia_bot"), "status")
        self.assertEqual(normalize_command("статус"), "status")
        self.assertEqual(normalize_command("прогноз"), "forecast")
        self.assertEqual(normalize_command("/last_predictions"), "last_predictions")
        self.assertEqual(normalize_command("недельный отчет"), "weekly")

    def test_authorization_allows_only_private_chat_for_single_user(self):
        self.assertTrue(
            is_authorized_update(
                {"message": {"from": {"id": 405690972}, "chat": {"type": "private"}}},
                allowed_user_id=405690972,
            )
        )
        self.assertFalse(
            is_authorized_update(
                {"message": {"from": {"id": 111}, "chat": {"type": "private"}}},
                allowed_user_id=405690972,
            )
        )
        self.assertFalse(
            is_authorized_update(
                {"message": {"from": {"id": 405690972}, "chat": {"type": "group"}}},
                allowed_user_id=405690972,
            )
        )

    def test_trim_message_keeps_telegram_safe_length(self):
        raw = "x" * (TELEGRAM_MESSAGE_LIMIT + 200)
        trimmed = trim_message(raw)
        self.assertLessEqual(len(trimmed), TELEGRAM_MESSAGE_LIMIT)
        self.assertTrue(trimmed.endswith("..."))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Запустить тесты и убедиться, что они падают**

Run:

```bash
python3 -m unittest python/test_telegram_operator.py -v
```

Expected: `ImportError` или `cannot import name ...` из ещё не реализованного `python/operator_actions.py`.

- [ ] **Step 4: Реализовать минимальный helper-модуль для прохождения тестов**

```python
# python/operator_actions.py
from __future__ import annotations

import re
from typing import Any

TELEGRAM_MESSAGE_LIMIT = 4096

COMMAND_ALIASES = {
    "help": "help",
    "помощь": "help",
    "status": "status",
    "статус": "status",
    "forecast": "forecast",
    "прогноз": "forecast",
    "resolve": "resolve",
    "резолв": "resolve",
    "weekly": "weekly",
    "недельный отчет": "weekly",
    "day0": "day0",
    "проверка рынка": "day0",
    "edge": "edge",
    "logs": "logs",
    "логи": "logs",
    "health": "health",
    "здоровье": "health",
    "last_predictions": "last_predictions",
    "последние": "last_predictions",
}


def normalize_command(text: str | None) -> str | None:
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if normalized.startswith("/"):
        normalized = normalized[1:].split("@", 1)[0]
    return COMMAND_ALIASES.get(normalized)


def is_authorized_update(update: dict[str, Any], allowed_user_id: int) -> bool:
    message = update.get("message") or {}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    return sender.get("id") == allowed_user_id and chat.get("type") == "private"


def trim_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
```

- [ ] **Step 5: Снова запустить unit tests**

Run:

```bash
python3 -m unittest python/test_telegram_operator.py -v
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add python/__init__.py python/operator_actions.py python/test_telegram_operator.py
git commit -m "Add Telegram operator command helpers"
```

---

### Task 2: Реализовать действия бота и polling loop

**Files:**
- Modify: `python/operator_actions.py`
- Create: `python/telegram_operator.py`
- Test: `python/test_telegram_operator.py`

- [ ] **Step 1: Добавить новые падающие тесты на формат help/status**

```python
from python.operator_actions import build_help_message, format_health_summary

    def test_help_message_mentions_russian_aliases(self):
        help_text = build_help_message()
        self.assertIn("/status", help_text)
        self.assertIn("статус", help_text)
        self.assertIn("/forecast", help_text)
        self.assertIn("прогноз", help_text)

    def test_health_summary_formats_service_statuses(self):
        rendered = format_health_summary(
            [
                {"name": "pythia-scheduler", "status": "healthy"},
                {"name": "pythia-telegram", "status": "running"},
            ]
        )
        self.assertIn("pythia-scheduler", rendered)
        self.assertIn("healthy", rendered)
        self.assertIn("pythia-telegram", rendered)
```

- [ ] **Step 2: Запустить тесты и увидеть падение**

Run:

```bash
python3 -m unittest python/test_telegram_operator.py -v
```

Expected: `ImportError` или `AttributeError` на `build_help_message` / `format_health_summary`.

- [ ] **Step 3: Расширить `python/operator_actions.py` реальными action helpers**

```python
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs")))
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
PROJECT_NAME = "pythia"

BOT_COMMANDS = [
    ("help", "Список команд и инструкция"),
    ("status", "Сводный статус системы"),
    ("forecast", "Ручной запуск дневного прогона"),
    ("resolve", "Проверка resolved маркетов"),
    ("weekly", "Недельный отчёт и анализ"),
    ("day0", "Быстрая проверка доступности рынков"),
    ("edge", "Проверка Brier score и edge"),
    ("logs", "Последние строки логов"),
    ("health", "Техническое состояние сервисов"),
    ("last_predictions", "Последние прогнозы из базы"),
]


def build_help_message() -> str:
    return (
        "*Команды Pythia*\\n"
        "/help или помощь — список команд\\n"
        "/status или статус — сводный статус\\n"
        "/forecast или прогноз — ручной прогнозный прогон\\n"
        "/resolve или резолв — проверка resolved маркетов\\n"
        "/weekly или недельный отчет — недельный отчёт\\n"
        "/day0 или проверка рынка — availability check\\n"
        "/edge — edge и Brier score\\n"
        "/logs или логи — последние логи\\n"
        "/health или здоровье — состояние контейнеров\\n"
        "/last_predictions или последние — последние прогнозы"
    )


def format_health_summary(services: list[dict[str, str]]) -> str:
    lines = ["*Состояние сервисов Pythia*"]
    for service in services:
        lines.append(f"- {service['name']}: {service['status']}")
    return "\\n".join(lines)


def run_command(command: list[str], timeout: int = 600) -> tuple[int, str, str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=BASE_DIR)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_operator_command(command_key: str) -> str:
    if command_key == "forecast":
        return _run_entrypoint("forecast")
    if command_key == "resolve":
        return _run_entrypoint("resolve")
    if command_key == "weekly":
        return _run_entrypoint("weekly")
    if command_key == "day0":
        return _run_day0()
    if command_key == "edge":
        return _run_edge()
    if command_key == "logs":
        return render_logs()
    if command_key == "health":
        return render_health()
    if command_key == "status":
        return render_status()
    if command_key == "last_predictions":
        return render_last_predictions()
    return build_help_message()
```

- [ ] **Step 4: Создать `python/telegram_operator.py` с polling loop, `setMyCommands` и single-user guard**

```python
# python/telegram_operator.py
from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any

import requests

from python.operator_actions import (
    BOT_COMMANDS,
    build_help_message,
    is_authorized_update,
    normalize_command,
    run_operator_command,
    trim_message,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_OPERATOR_USER_ID", "405690972"))
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_TIMEOUT = 30

CURRENT_JOB: str | None = None


def api_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{API_BASE}/{method}", json=payload, timeout=POLL_TIMEOUT + 10)
    response.raise_for_status()
    return response.json()


def set_commands() -> None:
    commands = [{"command": name, "description": desc} for name, desc in BOT_COMMANDS]
    api_call("setMyCommands", {"commands": commands})


def send_message(chat_id: int, text: str) -> None:
    api_call("sendMessage", {"chat_id": chat_id, "text": trim_message(text), "parse_mode": "Markdown"})


def get_updates(offset: int | None) -> list[dict[str, Any]]:
    params = {"timeout": POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    response = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=POLL_TIMEOUT + 10)
    response.raise_for_status()
    data = response.json()
    return data.get("result", [])


def handle_update(update: dict[str, Any]) -> None:
    global CURRENT_JOB
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = message.get("text")
    if not chat_id or not is_authorized_update(update, ALLOWED_USER_ID):
        return
    command_key = normalize_command(text)
    if not command_key:
        send_message(chat_id, "Неизвестная команда. Напиши /help")
        return
    if command_key in {"forecast", "resolve", "weekly", "day0", "edge"} and CURRENT_JOB:
        send_message(chat_id, f"Команда уже выполняется: {CURRENT_JOB}")
        return
    try:
        if command_key in {"forecast", "resolve", "weekly", "day0", "edge"}:
            CURRENT_JOB = command_key
        send_message(chat_id, f"Принял команду: {command_key}")
        result = run_operator_command(command_key)
        send_message(chat_id, result)
    except Exception as exc:
        traceback.print_exc()
        send_message(chat_id, f"Команда завершилась с ошибкой: {exc}")
    finally:
        if CURRENT_JOB == command_key:
            CURRENT_JOB = None


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    set_commands()
    offset = None
    while True:
        try:
            for update in get_updates(offset):
                offset = update["update_id"] + 1
                handle_update(update)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Снова прогнать unit tests**

Run:

```bash
python3 -m unittest python/test_telegram_operator.py -v
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add python/operator_actions.py python/telegram_operator.py python/test_telegram_operator.py
git commit -m "Add Telegram operator polling bot"
```

---

### Task 3: Подключить telegram-сервис в Docker и host helpers

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `scripts/entrypoint.sh`
- Modify: `scripts/manual_run.sh`

- [ ] **Step 1: Добавить переменную авторизованного оператора в `.env.example`**

```env
# === Telegram Operator ===
TELEGRAM_OPERATOR_USER_ID=405690972
```

- [ ] **Step 2: Добавить режим `telegram` в `scripts/entrypoint.sh`**

```bash
  telegram)
    cd /app
    exec /app/python/venv/bin/python python/telegram_operator.py
    ;;
```

- [ ] **Step 3: Добавить сервис `pythia-telegram` в `docker-compose.yml`**

```yaml
  pythia-telegram:
    image: pythia:latest
    container_name: pythia-telegram
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
      - /var/run/docker.sock:/var/run/docker.sock
    command: ["telegram"]
    networks:
      - pythia_internal
    mem_limit: 512m
    cpus: 0.5
    pids_limit: 128
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    healthcheck:
      test: ["CMD-SHELL", "pgrep -af 'telegram_operator.py' >/dev/null || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3
```

- [ ] **Step 4: Расширить `scripts/manual_run.sh`, чтобы `logs` показывал оба сервиса**

```bash
  logs)
    docker compose logs -f --tail=100 pythia-scheduler pythia-telegram
    ;;
```

И чтобы `status` через `docker compose ps` показывал оба сервиса без дополнительной фильтрации.

- [ ] **Step 5: Проверить shell syntax и структуру compose**

Run:

```bash
bash -n scripts/entrypoint.sh
bash -n scripts/manual_run.sh
docker compose config
```

Expected:

- shell-команды завершаются без ошибок
- `docker compose config` показывает оба сервиса: `pythia-scheduler` и `pythia-telegram`

- [ ] **Step 6: Commit**

```bash
git add .env.example docker-compose.yml scripts/entrypoint.sh scripts/manual_run.sh
git commit -m "Wire Telegram operator into Docker stack"
```

---

### Task 4: Обновить русскую документацию

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Добавить в README раздел про Telegram-оператора**

```markdown
## Telegram Operator

Pythia поддерживает операторский Telegram-бот без веб-дашборда.

Доступные slash-команды:

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

Бот также понимает русские алиасы: `помощь`, `статус`, `прогноз`, `логи`, `последние`.
```

- [ ] **Step 2: Добавить `pythia-telegram` в `docs/ARCHITECTURE.md`**

```markdown
- `pythia-scheduler` — cron-задачи через `supercronic`
- `pythia-telegram` — polling-бот, который принимает команды оператора и запускает существующие сценарии
```

- [ ] **Step 3: Добавить в `docs/DEPLOYMENT.md` раздел проверки operator bot**

```markdown
## Проверка Telegram-оператора

После `docker compose up -d --build` проверьте:

- `docker compose ps`
- `docker compose logs --tail=50 pythia-telegram`

Затем в личном чате с ботом выполните:

- `/help`
- `/health`
- `/status`
```

- [ ] **Step 4: Проверить, что документация не возвращает старые bare-metal сценарии**

Run:

```bash
rg -n "telegram operator|pythia-telegram|/status|/forecast|помощь|статус" README.md docs
```

Expected: упоминания новых команд и сервиса есть в активной документации.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/DEPLOYMENT.md
git commit -m "Document Telegram operator workflow"
```

---

### Task 5: Верификация и деплой на `amper`

**Files:**
- No new code expected

- [ ] **Step 1: Выполнить локальную проверку**

Run:

```bash
python3 -m unittest python/test_telegram_operator.py -v
python3 -m py_compile python/*.py
npx tsc --noEmit
bash -n scripts/entrypoint.sh
bash -n scripts/manual_run.sh
git diff --check
```

Expected:

- unit tests зелёные
- Python компилируется
- TypeScript typecheck зелёный
- shell syntax зелёный

- [ ] **Step 2: Задеплоить на `amper`**

Run:

```bash
ssh amper "sudo -u pythia bash -lc 'cd /home/pythia/pythia && git pull --ff-only origin main && docker compose up -d --build && docker compose ps'"
```

Expected: оба сервиса `pythia-scheduler` и `pythia-telegram` находятся в `Up` или `healthy`.

- [ ] **Step 3: Проверить логи telegram-сервиса**

Run:

```bash
ssh amper "sudo -u pythia bash -lc 'cd /home/pythia/pythia && docker compose logs --tail=50 pythia-telegram'"
```

Expected:

- бот стартовал без traceback
- `setMyCommands` прошло
- polling loop работает

- [ ] **Step 4: Проверить команды в реальном Telegram-чате**

Проверить вручную в Telegram:

- `/help`
- `/health`
- `/status`
- `/logs`
- `/day0`
- `/last_predictions`

Expected:

- ответы приходят на русском
- русские текстовые алиасы (`статус`, `помощь`, `прогноз`) тоже работают

- [ ] **Step 5: Проверить ручной запуск тяжёлой команды**

Проверить вручную в Telegram:

- `/forecast`

Expected:

- бот отвечает `Принял команду: forecast`
- после завершения присылает итог
- если маркетов по текущим фильтрам нет, это явно отражено в тексте ответа

- [ ] **Step 6: Commit итоговых operational изменений, если появились**

```bash
git status --short
```

Expected: чистое рабочее дерево. Если появились только ожидаемые документационные или конфигурационные правки, закоммитить их отдельным commit.

---

## Self-Review

- Spec coverage: покрыты контейнер `pythia-telegram`, single-user auth, polling, русские ответы, slash-команды, алиасы, health/status/logs, docs и деплой.
- Placeholder scan: `TODO/TBD` не используются.
- Type consistency: во всех задачах используется единый набор имён `pythia-telegram`, `telegram_operator.py`, `TELEGRAM_OPERATOR_USER_ID`, `last_predictions`.
