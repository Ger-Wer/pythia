# Отчёт об исполнении задания Pythia R1: Реестр источников и ограниченная ручная проверка

- **Task:** `PYTHIA-R1`
- **Статус выполнения:** `IMPLEMENTED` (передаётся на архитектурное ревью ведущему архитектору)
- **Исходный baseline:** `c21967f651eae899ccad1da29d2c9aa6133875cb`
- **Рабочая ветка:** `codex/evidence-r1-sources`
- **Коммит сдачи:** HEAD ветки `codex/evidence-r1-sources`
- **URL ветки:** `https://github.com/Ger-Wer/pythia/tree/codex/evidence-r1-sources`
- **Серверный рабочий каталог:** `pythia@amper:/home/pythia/pythia-evidence-r1`
- **Серверная среда:** Linux amper (Ubuntu 22.04 LTS, aarch64), Python 3.10.12 (изолированный venv `/home/pythia/pythia-paper-channel-20261017/python/venv`)

---

## 1. Состав изменений и файлы

В рамках задания R1 созданы только целевые файлы; существующие модули торгового цикла, базы данных, docker-конфигурации и production не изменялись.

| Файл | Назначение |
|---|---|
| `python/evaluation/source_registry.json` | Канонический versioned JSON-реестр 8 источников со строгой схемой, политикой свежести и правилами валидации |
| `docs/EVIDENCE_DATA_CONTRACT.md` | Спецификация контракта данных: разделение диагностической probe (L0), архивного документа (L1) и досье свидетельств (L2) |
| `python/evaluation/source_probe.py` | Ограниченный однократный проверяющий: streaming 2 MiB, таймаут 15 с, до 3 редиректов в allowlist, честные статусы |
| `python/evaluation/tests/test_source_probe.py` | Офлайн-набор из 15 pytest-проверок (валидные форматы, битые схемы, HTTP-200 блокировки, лимиты, редиректы, null-даты, сбои) |
| `docs/research/2026-10-17/r1/probe_results.json` | Артефакт живого запуска ограниченной пробы с сервера amper |
| `docs/research/2026-10-17/r1/test_run.log` | Лог прохождения полного набора тестов на сервере amper |
| `docs/research/2026-10-17/R1_SOURCE_REGISTRY_REPORT.md` | Настоящий итоговый отчёт инженера-исполнителя |

### Зависимости и отклонения
- **Новые зависимости:** 0 (использованы стандартная библиотека Python и существующие в проекте `requests` и `feedparser`).
- **Отклонения от задания:** отсутствуют. Все 8 источников зафиксированы с точными URL; обход блокировок и фальсификация статусов не производились.

---

## 2. Результаты серверных проверок на amper

Все тесты и живой прогон выполнены исключительно на сервере `amper` в изолированном каталоге `/home/pythia/pythia-evidence-r1`.

### 2.1 Офлайн unit-тесты (pytest)
- **Команда:**
  ```bash
  cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
  ```
- **Время выполнения:** 2026-10-17 15:12:24 UTC
- **Exit code:** `0`
- **Результат:** `129 passed in 3.68s` (114 существовавших регрессионных тестов + 15 новых тестов `test_source_probe.py`).
- **Лог сохранён:** `docs/research/2026-10-17/r1/test_run.log`.

### 2.2 Живой запуск пробы источников (Live Probe)
- **Команда:**
  ```bash
  cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.source_probe --output docs/research/2026-10-17/r1/probe_results.json
  ```
- **Время запуска:** 2026-10-17 15:12:29 UTC — 15:12:32 UTC (длительность ~2.35 с)
- **Exit code:** `0`
- **Итоговая сводка пробы:**
  - `total_sources`: 8
  - `status_counts`: `{'valid': 5, 'blocked': 2, 'invalid_content': 0, 'network_error': 0, 'unverified': 1}`
  - `summary_status`: `PARTIAL_REACHABLE` (`all_healthy=False`)
  - `usable_for_forecast_all`: `False` (все 8 записей помечены `usable_for_forecast: false`)

---

## 3. Таблица восьми живых результатов с amper

| source_id | URL | HTTP | Bytes | ms | SHA-256 тела | Status | Level | Items | Причина / Описание |
|---|---|---:|---:|---:|---|---|---|---:|---|
| `nyfed_effr` | `https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json` | 200 | 1433 | 422 | `7debc1936f7b78d6d43ac93a4695fe13269f74e960a31726b80769de80fd1760` | `valid` | `content` | 5 | Валидирован массив 5 записей EFFR с корректными датами и ставками |
| `fed_monetary_rss` | `https://www.federalreserve.gov/feeds/press_monetary.xml` | 200 | 9687 | 85 | `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1` | `valid` | `content` | 15 | Валидирована RSS-лента заявлений FOMC, 15 элементов с title и link |
| `fed_speeches_rss` | `https://www.federalreserve.gov/feeds/speeches.xml` | 200 | 9610 | 124 | `41d3b87fbd6930e4285ef366f574d20ee3a8db2ab295fceb5d8fa68448055930` | `valid` | `content` | 15 | Валидирована RSS-лента речей руководства ФРС, 15 элементов |
| `bls_cpi_rss` | `https://www.bls.gov/feed/cpi.rss` | 403 | 1323 | 426 | `3cbb2e3862808f45fa3f096a82106d3c6856bf4fbd4c09495f9667006fb35572` | `blocked` | `transport` | — | HTTP 403 Forbidden: автоматический доступ заблокирован защитой BLS |
| `atlanta_gdpnow` | `https://www.atlantafed.org/research-and-data/data/gdpnow` | 200 | 1242487 | 536 | `84b0c8a1927e0fd08d2ed25023d6af45ccfb0fecc3aa9423ce3a29e71d01ffb1` | `unverified` | `envelope` | — | HTML-документ получен (transport/envelope ok); содержательное извлечение таблицы модели не верифицировано в R1 |
| `github_deepseek_v3_releases` | `https://api.github.com/repos/deepseek-ai/DeepSeek-V3/releases?per_page=2` | 200 | 2171 | 283 | `7f9f028109e8dad49bdcd4b3b33f2c3d19d624738a2e435c0d9475ab5987161e` | `valid` | `content` | 1 | Валидирован массив релизов deepseek-ai/DeepSeek-V3 (1 релиз) |
| `hf_deepseek_models` | `https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=3` | 200 | 1507 | 184 | `6676d5f699948b674a93a3279d31ca638f7a5908855936d37bac25c3c37a12c6` | `valid` | `content` | 3 | Валидирован массив из 3 моделей с префиксом deepseek-ai/ |
| `arena_text_overall` | `https://arena.ai/leaderboard/text/overall-no-style-control` | 403 | 5702 | 241 | `93ee0cc5f0de43e282680dd9552c6aecfd2dc2ba6b7134ca25fa97ab868bfac5` | `blocked` | `transport` | — | HTTP 403 Forbidden: доступ заблокирован защитой Cloudflare LMSYS Arena |

---

## 4. Что осталось `unverified` и `blocked` и почему

1. **`atlanta_gdpnow` (`unverified`):**
   - Страница успешно отдаётся сервером (HTTP 200, 1.24 МБ, HTML-конверт валиден).
   - В соответствии с принципами задания, наличие одного заголовка или HTML 200 **не доказывает** корректного извлечения числовой оценки роста ВВП. В R1 парсер числовых таблиц не реализовывался, поэтому статус честно оставлен `unverified` (уровень проверки `envelope`). Полноценный экстрактор планируется на этапе R3.
2. **`bls_cpi_rss` (`blocked`):**
   - Сервер BLS возвращает HTTP 403 Forbidden для автоматических HTTP-клиентов. В соответствии с конституцией и заданием, обход блокировок (ротация прокси, headless-браузер с имитацией человека, платные API) не применялся.
3. **`arena_text_overall` (`blocked`):**
   - Сервер LMSYS Arena возвращает HTTP 403 Forbidden под защитой Cloudflare. Обход WAF в R1 запрещён.
4. **Утверждения для проверки архитектором:**
   - Ведущему архитектору предлагается подтвердить сохранение канонических URL для `bls_cpi_rss` и `arena_text_overall` в реестре (как целевых официальных точек с честным статусом `blocked`), а также выделение специализированного экстрактора для `atlanta_gdpnow` в задачу R3.

---

## 5. Подтверждение неизменности production, main и внешних сервисов

- Репозиторий `pythia` на ветке `main` и серверный checkout `/home/pythia/pythia` не изменялись: хэш `ee6460f31149917e29463ebb6f54c1d80e6c4562` сверен и сохранён.
- Development-ветка `codex/paper-channel-20261017` и checkout `/home/pythia/pythia-paper-channel-20261017` не изменялись: хэш `c21967f651eae899ccad1da29d2c9aa6133875cb` сохранён.
- Контейнер `pythia-v2` остаётся запущенным без перезапусков и изменений на образе `sha256:8957620732c68dfd8c7bbf65241486691b7c89158ea41b060f1b2bd7251c82ec`.
- Базы данных, cron-задачи, переменные окружения (`.env`) не изменялись.
- Публикации в Telegram-канал не выполнялись, реальные и тестовые ордера не выставлялись.
- Все операции R1 проводились строго в выделенном каталоге `/home/pythia/pythia-evidence-r1`.
