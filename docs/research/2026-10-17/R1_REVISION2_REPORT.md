# Pythia R1.2: Отчёт об устранении замечаний B1 и B2

**Дата:** 2026-10-17  
**Ветка:** `codex/evidence-r1-sources`  
**Базовый commit ревью:** `b0c5fa2f86b22a9753d6ad8cfa6be7c659c106b9`  
**Серверный checkout:** `/home/pythia/pythia-evidence-r1` на хосте `amper` (Ubuntu 22.04 LTS, aarch64)  
**Статус исполнителя:** **IMPLEMENTED** (R1.2 выполнен полностью; R2 не начат)

---

## 1. Резюме выполнения

В рамках задания R1.2 полностью закрыты два оставшихся архитектурных замечания ревью R1.1:

1. **B1 (Нужная RSS-категория и корректный XML):**
   - Внедрена строгая двухэтапная валидация XML: стандартный строгий парсер `xml.etree.ElementTree.fromstring` вызывается **до** толерантного `feedparser`.
   - Проверяется валидность документа и допустимый корневой элемент (`<rss>` или `<feed>`). Обрезанный документ (например, без закрывающих тегов `</channel></rss>`) отклоняется как `invalid_content` на уровне `envelope`.
   - Устранены широкие совпадения по общим пресс-релизам:
     - Для `fed_monetary_rss` запрещены общие категории ведомства (Banking Applications, Orders, Speeches). Элементы обязаны содержать признак `monetary` в путях; банковские приказы (`/pressreleases/orders...`) отбрасываются.
     - Для `bls_cpi_rss` запрещён общий заголовок «Bureau of Labor Statistics» без подтверждённой принадлежности к CPI; элементы типа Employment Situation (`/news.release/empsit.nr0.htm`) отклоняются.
   - Смешанные фиды, содержащие наряду с валидным материалом элементы чужих категорий, отклоняются (`valid_items == len(entries)`).

2. **B2 (Отдельный процесс с deadline под контролем родителя):**
   - Сетевое получение каждого источника вынесено в изолированный короткоживущий дочерний процесс (`multiprocessing.get_context("fork")` на Linux), общий deadline которого контролируется родителем (`time.monotonic() + budget` рассчитывается **до** запуска процесса).
   - Изолированный IPC Pipe (`duplex=False`) разделяет структурированную передачу данных и логи, исключая смешивание с stdout/stderr.
   - Защита от дедлока: родитель слушает `parent_conn.poll(timeout=remaining)` и читает входящее тело до 2 MiB по мере поступления, не блокируясь в ожидании завершения процесса на заполненной трубе.
   - Принудительное прерывание: при исчислении дедлайна родитель вызывает `proc.terminate()`, ожидает короткий grace (0.15s), при необходимости посылает `proc.kill()` (SIGKILL) и выполняет обязательный `proc.join()` (reap) с закрытием IPC. Блокирующие системные вызовы чтения из сокета, циклы `iter_content` и зависшие резолверы прерываются ядром немедленно.
   - Ограниченная погрешность очистки: задокументирован предел очистки `<= 0.50 с` сверх рабочего бюджета (на тестах с бюджетом 0.20 с фактическое время возврата составило 0.203–0.208 с, погрешность 0.003–0.008 с).
   - Честная семантика null-метрик: при принудительном завершении процесса до передачи результата поля `bytes_received`, `bytes_stored` и `http_status` возвращают `null` (`None`) с пояснением в `reason`. Фиктивные нули не подставляются.

---

## 2. Архитектура и изменения в компонентах

### 2.1 Модуль `python/evaluation/source_probe.py`

- **Строгая проверка XML (B1):**
  - Метод `_validate_rss_feed` сначала выполняет `ET.fromstring(body)`. При `ET.ParseError` возвращает `invalid_content` на уровне `envelope` (за исключением HTML-страниц блокировок BLS, классифицируемых как `blocked`).
  - Проверяется `xml_root.tag` на принадлежность к `rss` или `feed`.
  - Введены строгие наборы ключевых слов категории:
    - `fed_monetary_rss`: `required_title_keywords={"monetary", "fomc"}`, `forbidden_title_keywords={"banking", "orders", "speeches"}`, `required_entry_path_markers={"monetary"}`, `forbidden_entry_path_markers={"/orders", "/banking"}`.
    - `fed_speeches_rss`: `required_title_keywords={"speeches", "remarks", "speech"}`, `forbidden_title_keywords={"monetary policy"}`, `required_entry_path_markers={"speech", "speeches"}`, `forbidden_entry_path_markers={"/monetary"}`.
    - `bls_cpi_rss`: `required_title_keywords={"cpi", "consumer price index"}`, `forbidden_title_keywords={"employment situation", "producer price index", "ppi"}`, `required_entry_path_markers={"cpi"}`, `forbidden_entry_path_markers={"empsit", "ppi"}`, `entry_identity_keywords={"cpi", "consumer price index"}`.

- **Процессный воркер и управление дедлайном (B2):**
  - Выделена внутренняя функция `_fetch_bounded_core(sess, source_id, initial_url, timeout, max_redirects, max_bytes, user_agent)`.
  - Воркер-функция `_worker_fetch_entry(conn, source_id, initial_url, timeout, max_redirects, max_bytes, user_agent, session)` выполняется в отдельном процессе.
  - Метод `_fetch_bounded` создаёт канал `Pipe(duplex=False)`, стартует `ctx.Process`, закрывает дескриптор потомка в родителе и входит в `poll(timeout=remaining)`.
  - Блок `finally` гарантирует закрытие дескрипторов и reap процесса (`proc.join()`), предотвращая утечки файловых дескрипторов и появление процессов-зомби.
  - При `deadline_exceeded` возвращаются `bytes_received = None`, `bytes_stored = None`, `http_status = None`, статус `network_error`, уровень `transport`.

### 2.2 Спецификация `docs/EVIDENCE_DATA_CONTRACT.md`

- В разделе 1.4 обновлены требования к ограниченному транспорту: зафиксирован запуск отдельного воркера под контролем дедлайна родителя, процедура `terminate -> grace (0.15s) -> kill -> reap` и предельный cleanup bound `<= 0.50 с`.
- В таблице схемы §2.1 поля `bytes_received` и `bytes_stored` объявлены nullable (`Yes`), документируя значение `null` в случае принудительного убийства процесса по дедлайну.
- В §2.2 и §2.3 уточнены критерии отклонения неверных RSS-категорий и повреждённого XML.

### 2.3 Тестовый набор `python/evaluation/tests/test_source_probe.py`

Добавлено 8 новых специализированных тестов приёмки:
1. `test_rss_rejects_truncated_xml`: проверка отклонения XML с обрезанными тегами `</channel></rss>`.
2. `test_rss_rejects_fed_banking_orders_for_monetary`: отклонение фида Banking Applications для `fed_monetary_rss`.
3. `test_rss_rejects_bls_employment_for_cpi`: отклонение Employment Situation для `bls_cpi_rss`.
4. `test_rss_rejects_mixed_category_entries`: отклонение фида с частью валидных и частью неверных записей.
5. `test_real_requests_loopback_drip_interrupted_by_deadline`: реальный медленный локальный HTTP-сервер (поток 3.0 с), прерывание воркера по дедлайну 0.25 с, проверка фактического времени `elapsed <= budget + 0.35s`, подтверждение приёма запроса сервером и отправки байт до прерывания.
6. `test_blocking_resolver_interrupted_by_deadline`: синтетический блокирующий `socket.getaddrinfo` (задержка 1.0 с), прерывание по дедлайну 0.20 с, подтверждение входа в блокирующий вызов через разделяемую память (`mp.Value`), возврат родителем вовремя.
7. `test_timeout_on_first_source_does_not_block_subsequent_source`: подтверждение того, что таймаут первого источника не мешает последующему и не оставляет зомби-процессов.
8. `test_real_oversized_stream_through_fetch_path`: реальное получение через сетевой адаптер потока более 2.2 МиБ, подтверждение усечения буфера ровно на 2 МиБ (2 097 152 байт), `limit_exceeded = True`, `body_sha256 = null`.

---

## 3. Результаты контролей и тестов на amper

Все команды выполнены исключительно на сервере `amper` в окружении `/home/pythia/pythia-paper-channel-20261017/python/venv`.

### 3.1 Review Controls R1.2 (`review_controls.py`)

Запуск контролей ведущего архитектора:
```bash
ssh pythia@amper "cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -" < review_controls.py
```

Результат: **5/5 checks passed (`meets_expectation: true`)**

| Контроль | Бюджет | Факт. время | Статус | Уровень | meets_expectation |
|---|---|---|---|---|---|
| `bls_employment_feed_is_not_cpi` | — | — | `invalid_content` | `content` | **true** |
| `fed_bank_orders_are_not_monetary_policy` | — | — | `invalid_content` | `content` | **true** |
| `truncated_rss_xml` | — | — | `invalid_content` | `envelope` | **true** |
| `real_requests_loopback_drip_deadline` | 0.20 с | 0.2087 с | `network_error` | `transport` | **true** |
| `blocking_resolver_deadline` | 0.20 с | 0.2031 с | `network_error` | `transport` | **true** |

Артефакт сохранён в `docs/research/2026-10-17/r1/revision2/review_controls.json`.

### 3.2 Прежние 17 контролей R1.1

Повторный прогон контрольного набора из 17 проверок (F1–F4):
Результат: **17/17 checks passed (`meets_expectation: true`)**. Регрессий нет.

### 3.3 Полный тестовый сьют

Команда:
```bash
ssh pythia@amper "cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v"
```

Результат: **150 passed in 4.47s** (код возврата `0`).  
Лог зафиксирован в `docs/research/2026-10-17/r1/revision2/test_run.log`.

### 3.4 Ограниченный публичный live-прогон

Команда:
```bash
ssh pythia@amper "cd /home/pythia/pythia-evidence-r1 && /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.source_probe --output docs/research/2026-10-17/r1/revision2/probe_results.json"
```

Сводка:
- Всего источников: 8
- Статусы: `valid: 5`, `blocked: 2` (BLS 403, Arena 403), `unverified: 1` (Atlanta Fed GDPNow HTML), `invalid_content: 0`, `network_error: 0`
- `summary_status: PARTIAL_REACHABLE` (`all_healthy=False`)
- `usable_for_forecast_all: False`

Таблица результатов:

| source_id | HTTP | Recv | Stored | ms | Status | Level | Items | Reason |
|---|---|---|---|---|---|---|---|---|
| `nyfed_effr` | 200 | 1433 | 1433 | 307 | valid | content | 5 | Successfully validated 5 EFFR reference … |
| `fed_monetary_rss` | 200 | 9687 | 9687 | 92 | valid | content | 15 | Valid Federal Reserve Monetary Policy fe… |
| `fed_speeches_rss` | 200 | 9610 | 9610 | 196 | valid | content | 15 | Valid Federal Reserve Speeches feed with… |
| `bls_cpi_rss` | 403 | 1323 | 1323 | 329 | blocked | transport | — | BLS Consumer Price Index returned HTTP 4… |
| `atlanta_gdpnow` | 200 | 1242487 | 1242487 | 588 | unverified | envelope | — | HTML document received (transport and en… |
| `github_deepseek_v3_releases` | 200 | 2171 | 2171 | 303 | valid | content | 1 | Valid GitHub releases array for deepseek… |
| `hf_deepseek_models` | 200 | 1507 | 1507 | 185 | valid | content | 3 | Valid Hugging Face models array with 3 m… |
| `arena_text_overall` | 403 | 5702 | 5702 | 242 | blocked | transport | — | LMSYS Chatbot Arena returned HTTP 403 Fo… |

Артефакты сохранены в `docs/research/2026-10-17/r1/revision2/probe_results.json` и `probe_run.log`.

---

## 4. Ссылки на артефакты

- `docs/research/2026-10-17/r1/revision2/review_controls.json` — результаты контролей R1.2 (5/5 true).
- `docs/research/2026-10-17/r1/revision2/review_controls.log` — консольный вывод контролей R1.2.
- `docs/research/2026-10-17/r1/revision2/test_run.log` — протокол прогона pytest (150 passed in 4.47s).
- `docs/research/2026-10-17/r1/revision2/probe_results.json` — структурированные данные живого прогона 8 источников.
- `docs/research/2026-10-17/r1/revision2/probe_run.log` — лог и сводная таблица живого прогона.

---

## 5. Границы и ограничения

- Никаких фоновых сервисов, демонов или баз данных не развёрнуто.
- Все операции происходят строго в изолированном воркере на каждый источник.
- Производственные контейнеры и родительский paper-чекаут не затрагивались.
- Этап R2 самостоятельно не открывался; работа остановлена в ожидании ревью архитектора.
