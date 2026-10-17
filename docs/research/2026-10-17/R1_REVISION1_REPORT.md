# Отчёт об исполнении задания Pythia R1.1: Исправления F1–F4

- **Task:** `PYTHIA-R1.1`
- **Статус выполнения:** `IMPLEMENTED` (передаётся на повторное архитектурное ревью ведущему архитектору)
- **Базовый commit ревью:** `f523fee1440b8d73e43026c2743d454b2ac6448d` (ветка `codex/evidence-r1-sources`)
- **Итоговый commit сдачи:** HEAD ветки `codex/evidence-r1-sources`
- **Серверный рабочий каталог:** `pythia@amper:/home/pythia/pythia-evidence-r1`
- **Серверная среда:** Linux amper (Ubuntu 22.04 LTS, aarch64), Python 3.10.12 (изолированный venv `/home/pythia/pythia-paper-channel-20261017/python/venv`)

---

## 1. Сводка исправлений F1–F4

Все замечания ревью [REVIEW_RU.md](../../../project/pythia/reviews/2026-10-17/r1-f523fee/REVIEW_RU.md) устранены в коде, тестах и контракте данных:

### F1 — Содержательная валидация без ложного valid
1. **NYFed EFFR:**
   - Поле `type` строго проверяется на точное совпадение со строкой `"EFFR"` (ранее допускалось `NOT_EFFR` через подстроку).
   - Поле `effectiveDate` валидируется через строгий календарный разбор `datetime.strptime(d, "%Y-%m-%d").date()` с проверкой каноничности строки. Несуществующие даты (например, `2026-02-30`, `2026-13-01`) отклоняются.
   - Поле `percentRate` проверяется на тип `(int, float)` с явным исключением типа `bool` (так как в Python `isinstance(True, int) == True`), а также с проверкой `math.isfinite(rate)`. Значения `True`, `False`, `NaN`, `+Infinity`, `-Infinity` и строки отклоняются со статусом `invalid_content`.
2. **Fed monetary / speeches / BLS CPI (RSS-ленты):**
   - Реализована проверка идентичности источника: проверяется домен ссылки канала (`channel_link`), ключевые маркеры в названии канала (`title`) и ссылки элементов (`entries[].link`).
   - Исключена взаимная подмена: `fed_monetary_rss` отклоняет заголовки речей (`speeches`), а `fed_speeches_rss` отклоняет заявления по монетарной политике (`monetary policy`).
   - Посторонняя лента (включая контрпример ревьюера `Sports scores` с `example.org`) отклоняется на всех трёх валидаторах со статусом `invalid_content` (`Feed identity mismatch`).
   - Элементы ленты проверяются на наличие непустых строковых полей `title` и `link`. Счётчики `valid_items` и `len(entries)` сравниваются строго: частичное повреждение ленты не получает `valid`.
3. **GitHub DeepSeek-V3 releases:**
   - Поле `html_url` разбирается через `urllib.parse`: проверяется хост (`github.com`) и точный путь `/deepseek-ai/DeepSeek-V3/releases/...`. Подстрочные совпадения вроде `deepseek-ai/DeepSeek-V3-other` отклоняются.
   - Поле `tag_name` строго проверяется на непустую строку (списки вроде `["v1"]` отклоняются).
   - Пустой корректный массив `[]` возвращает `valid`, `content`, `item_count=0`.
4. **Hugging Face DeepSeek models:**
   - Идентификатор модели `id` проверяется на непустое имя модели после префикса `deepseek-ai/` (значение `deepseek-ai/` отклоняется).
   - Если указан автор `author`, он обязан совпадать с `"deepseek-ai"`.
   - Пустой массив `[]` возвращает `valid`, `content`, `item_count=0`.

### F2 — Целостность ответа и реальное ограничение чтения
1. **Хэширование полного тела:**
   - `body_sha256` вычисляется строго после подтверждённого завершения потока (EOF) без ошибок, без таймаута и без превышения лимита.
   - При ошибке потока (например, `requests.ConnectionError` после передачи префикса байтов), превышении лимита 2 MiB или прерывании по deadline `body_sha256` безусловно устанавливается в `null`.
2. **Счётчики байтов:**
   - Разделены понятия `bytes_received` (суммарно прочитано из сокета до границы отсечения) и `bytes_stored` (сохранено в буфер памяти, строго <= 2 MiB / 2,097,152 байт).
   - Согласован контракт данных и пример отрицательного результата при превышении стрим-лимита.
3. **Закрытие дескрипторов:**
   - На всех шагах ручной обработки редиректов вызывается `resp.close()`, что устраняет открытые дескрипторы при переходах 3xx.
   - Класс `SourceProbe` реализует контекстный менеджер (`__enter__` / `__exit__`) и метод `close()` для освобождения сессии.

### F3 — Фиксированный каталог и честное покрытие
1. **Проверка реестра до HTTP:**
   - До выполнения сетевых запросов вызывается `validate_registry()`. Проверяется наличие ровно 8 канонических источников (`CANONICAL_SOURCES`), уникальность ID и точное совпадение URL.
   - Неполный, избыточный или модифицированный реестр (включая контрпример ревьюера с 1 источником) отклоняется до сетевых вызовов: `run()` возвращает `summary_status = "INVALID_REGISTRY"`, `all_healthy = False`, без HTTP-обращений.
2. **Устранение произвольного CLI:**
   - Параметр CLI `--registry` удалён из `source_probe.py`; используется единый канонический конфигурационный реестр проекта.

### F4 — Конечное время и анонимный транспорт
1. **Общий deadline источника:**
   - Зафиксирован общий бюджет 15.0 секунд на один источник, включающий DNS, соединение, цепочку редиректов и потоковое чтение.
   - Превышение общего лимита времени (включая симуляцию часов с `duration_ms >= 15000`) прерывает обработку с установкой `status = "network_error"`, `validation_level = "transport"` и `body_sha256 = null`.
2. **Анонимный транспорт:**
   - `session.trust_env = False` исключает чтение `.netrc` и ambient прокси (синтетический `.netrc` с canary-данными не приводит к утечке `Authorization`).
   - Использован `NullCookieJar`, полностью блокирующий сохранение и отправку любых cookies.
   - Проверка сертификатов TLS (`verify = True`) сохранена.

---

## 2. Результаты проверок на amper

Все проверки выполнены на сервере `amper` в `/home/pythia/pythia-evidence-r1` с использованием окружения `/home/pythia/pythia-paper-channel-20261017/python/venv`.

### 2.1 Офлайн unit-тесты (pytest)
- **Команда:**
  ```bash
  cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
  ```
- **Exit code:** `0`
- **Фактический результат:** **`142 passed in 3.38s`** (114 существовавших регрессионных тестов + 28 тестов в `test_source_probe.py`).
- **Лог сохранён:** `docs/research/2026-10-17/r1/revision1/test_run.log`.

### 2.2 Воспроизведение синтетических контролей ревьюера (`review_controls.py`)
- **Команда:** передача `review_controls.py` на stdin Python на сервере `amper`.
- **Exit code:** `0`
- **Результат:** **17 из 17 проверок успешно прошли (`meets_expectation: true` по всем 17 пунктам)**:
  1. `nyfed_wrong_series`: `meets_expectation: true` (`observed_status: invalid_content`)
  2. `nyfed_invalid_calendar_date`: `meets_expectation: true` (`observed_status: invalid_content`)
  3. `nyfed_boolean_rate`: `meets_expectation: true` (`observed_status: invalid_content`)
  4. `nyfed_nan_rate`: `meets_expectation: true` (`observed_status: invalid_content`)
  5. `nyfed_infinite_rate`: `meets_expectation: true` (`observed_status: invalid_content`)
  6. `fed_monetary_rss_unrelated_feed`: `meets_expectation: true` (`observed_status: invalid_content`)
  7. `fed_speeches_rss_unrelated_feed`: `meets_expectation: true` (`observed_status: invalid_content`)
  8. `bls_cpi_rss_unrelated_feed`: `meets_expectation: true` (`observed_status: invalid_content`)
  9. `github_wrong_repo`: `meets_expectation: true` (`observed_status: invalid_content`)
  10. `github_non_string_tag`: `meets_expectation: true` (`observed_status: invalid_content`)
  11. `hf_empty_model_name`: `meets_expectation: true` (`observed_status: invalid_content`)
  12. `partial_stream_hash`: `meets_expectation: true` (`body_sha256: null`)
  13. `redirect_response_closed`: `meets_expectation: true` (`redirect_closed: true`)
  14. `incomplete_registry_reports_healthy`: `meets_expectation: true` (`all_healthy: false`, `summary_status: INVALID_REGISTRY`)
  15. `synthetic_netrc_implicitly_attaches_authorization`: `meets_expectation: true` (`authorization_present: false`)
  16. `redirect_body_exceeds_2mib_in_real_requests_session`: `meets_expectation: true`
  17. `simulated_20s_elapsed_with_15s_timeout`: `meets_expectation: true` (`observed_status: network_error`)

### 2.3 Новый ограниченный live-прогон с amper
- **Команда:**
  ```bash
  cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.source_probe --output docs/research/2026-10-17/r1/revision1/probe_results.json
  ```
- **Время запуска:** 2026-10-17 15:42:05 UTC — 15:42:07 UTC (длительность 2.16 с).
- **Exit code:** `0`
- **Итоговая сводка:**
  - `total_sources`: 8
  - `status_counts`: `{'valid': 5, 'blocked': 2, 'invalid_content': 0, 'network_error': 0, 'unverified': 1}`
  - `summary_status`: `PARTIAL_REACHABLE` (`all_healthy=False`)
  - `usable_for_forecast_all`: `False` (все 8 источников помечены `usable_for_forecast: false`)
- **Лог и артефакты:** `docs/research/2026-10-17/r1/revision1/probe_run.log` и `docs/research/2026-10-17/r1/revision1/probe_results.json`.

---

## 3. Таблица восьми живых результатов пробы (Revision 1, amper)

| source_id | URL | HTTP | Recv | Stored | ms | SHA-256 тела | Status | Level | Items | Причина / Описание |
|---|---|---:|---:|---:|---:|---|---|---|---:|---|
| `nyfed_effr` | `https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json` | 200 | 1433 | 1433 | 478 | `7debc1936f7b78d6d43ac93a4695fe13269f74e960a31726b80769de80fd1760` | `valid` | `content` | 5 | Валидирован массив из 5 записей EFFR с точным типом, датами и ставками |
| `fed_monetary_rss` | `https://www.federalreserve.gov/feeds/press_monetary.xml` | 200 | 9687 | 9687 | 103 | `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1` | `valid` | `content` | 15 | Валидирована лента FOMC, проверена идентичность канала и 15 элементов |
| `fed_speeches_rss` | `https://www.federalreserve.gov/feeds/speeches.xml` | 200 | 9610 | 9610 | 21 | `41d3b87fbd6930e4285ef366f574d20ee3a8db2ab295fceb5d8fa68448055930` | `valid` | `content` | 15 | Валидирована лента речей руководства ФРС, 15 проверенных элементов |
| `bls_cpi_rss` | `https://www.bls.gov/feed/cpi.rss` | 403 | 1325 | 1325 | 332 | `null` | `blocked` | `transport` | — | HTTP 403: доступ блокируется автоматической защитой BLS |
| `atlanta_gdpnow` | `https://www.atlantafed.org/research-and-data/data/gdpnow` | 200 | 1242487 | 1242487 | 470 | `null` | `unverified` | `envelope` | — | HTML получен; содержательное извлечение таблицы модели не верифицировано в R1 |
| `github_deepseek_v3_releases` | `https://api.github.com/repos/deepseek-ai/DeepSeek-V3/releases?per_page=2` | 200 | 2171 | 2171 | 291 | `7f9f028109e8dad49bdcd4b3b33f2c3d19d624738a2e435c0d9475ab5987161e` | `valid` | `content` | 1 | Валидирован релиз DeepSeek-V3 (проверен путь репозитория и строковый тег) |
| `hf_deepseek_models` | `https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=3` | 200 | 1507 | 1507 | 169 | `71aedfe123801f3556f96fc35931ed3cefe5c17a18d5babe2e583b70500e99e3` | `valid` | `content` | 3 | Валидирован массив из 3 моделей, namespace и имя модели проверены |
| `arena_text_overall` | `https://arena.ai/leaderboard/text/overall-no-style-control` | 403 | 5702 | 5702 | 259 | `null` | `blocked` | `transport` | — | HTTP 403: доступ блокируется Cloudflare LMSYS Arena |

---

## 4. Что осталось `unverified` и `blocked`

- **`atlanta_gdpnow` (`unverified`):** транспортный и HTML-уровень успешно пройдены, однако `body_sha256 = null` и статус оставлен `unverified`, так как валидатор таблицы модели будет реализован на этапе R3.
- **`bls_cpi_rss` и `arena_text_overall` (`blocked`):** оба эндпоинта возвращают HTTP 403. Обход блокировок не применялся, `body_sha256 = null`.

---

## 5. Подтверждение неизменности production, main и внешних сервисов

- Репозиторий `pythia` на ветке `main` и серверный checkout `/home/pythia/pythia` остаются на `ee6460f31149917e29463ebb6f54c1d80e6c4562`.
- Development-ветка `codex/paper-channel-20261017` и checkout `/home/pythia/pythia-paper-channel-20261017` остаются на `c21967f651eae899ccad1da29d2c9aa6133875cb`.
- Контейнер `pythia-v2` остаётся запущенным на образе `sha256:8957620732c68dfd8c7bbf65241486691b7c89158ea41b060f1b2bd7251c82ec`.
- Базы данных, cron, `.env`, Telegram-канал и торговые ордера не затрагивались.
- Все операции R1.1 выполнялись строго в ветке `codex/evidence-r1-sources` и каталоге `/home/pythia/pythia-evidence-r1`.
