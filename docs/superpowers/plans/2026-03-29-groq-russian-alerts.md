# Groq And Russian Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести Pythia на Groq через OpenAI-compatible endpoint, сделать Telegram-уведомления русскими и проверить работу на `amper`.

**Architecture:** Конфигурация LLM меняется только через `.env`, без смены прикладной архитектуры. Пользовательские сообщения в Telegram переводятся в существующих entrypoints TypeScript и Python, после чего система проверяется тестовым уведомлением и ручным запуском на сервере.

**Tech Stack:** Bun, TypeScript, Python, Docker Compose, Groq OpenAI-compatible API, Telegram Bot API

---

### Task 1: Проверить доступные модели Groq и обновить `.env`

**Files:**
- Modify: `.env`

- [ ] Запросить `/models` у Groq по текущему ключу и выбрать реальные model IDs для `MODEL_RESEARCH` и `MODEL_SYNTHESIS`.
- [ ] Обновить локальный `.env`: `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL=https://api.groq.com/openai/v1`, новые `MODEL_RESEARCH` и `MODEL_SYNTHESIS`.
- [ ] Обновить `/home/pythia/pythia/.env` на `amper` теми же значениями.

### Task 2: Перевести Telegram-уведомления на русский

**Files:**
- Modify: `src/main.ts`
- Modify: `python/telegram_bot.py`
- Modify: `python/resolution_checker.py`

- [ ] Перевести alert-сообщения оркестратора в `src/main.ts`.
- [ ] Перевести weekly report и fallback-сообщения в `python/telegram_bot.py`.
- [ ] Перевести summary resolution checker в `python/resolution_checker.py`.

### Task 3: Проверить изменения и отправить реальные уведомления

**Files:**
- No code changes expected

- [ ] Прогнать локальные проверки: `npx tsc --noEmit`, `python3 -m py_compile python/*.py`, shell syntax checks.
- [ ] Отправить тестовое Telegram-сообщение на русском.
- [ ] На `amper`: `git pull`, `docker compose up -d --build`, ручной `forecast` и `status`.
- [ ] Подтвердить, что сообщение/ошибка в Telegram приходит на русском.
