# Pythia R2: Отчёт об исполнении — Неизменяемый архив ответов источников

**Дата:** 18.10.2026  
**Статус:** **IMPLEMENTED** (R2 полностью реализован, все тесты и проверки на amper пройдены, артефакты сохранены).  
**Baseline:** `4f0015eec61ad2a8f8eb14c68f944cae053182ad` (ветка `codex/evidence-r1-sources`, R1 ACCEPTED).  
**Ветка:** `codex/evidence-r2-archive`.  
**Среда выполнения:** Сервер `amper` (Ubuntu 22.04 LTS, aarch64, Python 3.10.12 в `/home/pythia/pythia-paper-channel-20261017/python/venv`).  
**Серверный worktree:** `/home/pythia/pythia-evidence-r2`.  
**Локальный worktree:** `/Users/gerwer/Desktop/Pythia-evidence-r2`.  

---

## 1. Контроль неизменности рабочей системы (Production Parity)

Перед началом и по завершении работ проверены read-only параметры production и базовых репозиториев:
- Main/production checkout (`/home/pythia/pythia`): на коммите `ee6460f31149917e29463ebb6f54c1d80e6c4562` (не изменялся).
- Paper parent checkout (`/home/pythia/pythia-paper-channel-20261017`): на коммите `c21967f651eae899ccad1da29d2c9aa6133875cb` (не изменялся).
- Docker-контейнер `pythia-v2`: статус `running`, образ `sha256:8957620732c68dfd8c7bbf65241486691b7c89158ea41b060f1b2bd7251c82ec`, `StartedAt: 2026-08-29T08:36:28.679362795Z` (не перезапускался).
- Никаких изменений cron, .env, рабочих БД, Telegram-канала или торговых сделок не производилось.

---

## 2. Реализованный объем R2

### 2.1. Единый DTO `CaptureResult` и протокол worker
1. **Типизированный DTO `CaptureResult`:**
   - Поля: `source_id`, `initial_url`, `final_url` (nullable), `body` (точные raw bytes content-decoded body), `status_code` (nullable), `headers` (dict lowercase), `fetch_started_at`, `fetch_completed_at`, `duration_ms` (parent wall time), `bytes_received`, `bytes_stored`, `complete` (строго `bool`), `limit_exceeded` (`bool`), `error_msg` (nullable), `collector_version`.
   - Добавлен вспомогательный property `content_type`.
2. **Один fetch на попытку:**
   - `SourceProbe.capture_source(source_id, url)` выполняет сетевой запрос через изолированный worker под контролем parent deadline и возвращает `CaptureResult`.
   - `SourceProbe.probe_single(cfg, capture=capture)` строит диагностику Layer 0 из готового capture.
   - `EvidenceArchive.record_capture(capture=capture, probe_result=probe_res)` сохраняет тот же capture в CAS и SQLite.
   - Повторные сетевые запросы исключены (подтверждено fixture с loopback-счетчиком запросов).
3. **Три архитектурных уточнения R1.3:**
   - **Post-Decode Deadline Check:** Заключительная проверка дедлайна (`elapsed <= timeout`) и измерение `duration_ms` выполняются родительским процессом строго **после** декодирования JSON, base64 и валидации схемы worker, непосредственно перед возвратом `CaptureResult`.
   - **Строгая схема worker:** Валидируются типы (`complete` строго `bool`, не int 0/1; `status_code` int/null; `headers` dict; `bytes_stored == len(body)`; `bytes_stored <= bytes_received`). Ошибка схемы приводит к `complete=False` и отказу в создании ревизии документа.
   - **Nullable `final_url`:** При падении worker, обрыве сети или таймауте до ответа `final_url` устанавливается в `None`, а не подменяется `initial_url`.
4. **Определение raw body:**
   - Точные **content-decoded байты** HTTP body, возвращаемые `requests.iter_content` (после прозрачной распаковки gzip/deflate), до декодирования charset (UTF-8 и др.) и парсинга.
   - Подтверждено тестом `test_loopback_gzip_content_decoded_raw_contract`: сжатый loopback-ответ распаковывается requests, тело сохраняется в CAS с SHA-256 распакованного содержимого, а `Content-Encoding: gzip` и `Content-Length` сохраняются в метаданных заголовков.

### 2.2. Модуль архива: Filesystem CAS + SQLite (`evidence_archive.py`)
1. **Каталог состояния (`state_dir`):**
   - Полностью изолирован от production/paper БД, не коммитится в git.
   - `blobs/ab/cd/abcd...bin`: файловый CAS с двухуровневой иерархией префиксов SHA-256.
   - `tmp/`: временный каталог на той же файловой системе для атомарной публикации через `NamedTemporaryFile` -> `fsync` -> `os.replace`.
   - `archive.db`: база данных SQLite в режиме WAL (`journal_mode = WAL`, `synchronous = NORMAL`, `foreign_keys = ON`, `busy_timeout = 10000`).
2. **Таблица `receipts` (Append-Only):**
   - Фиксирует каждую попытку с монотонным номером `seq` (1, 2, 3...), уникальным `receipt_id`, датами `fetch_started_at`, `fetch_completed_at`, `recorded_at` (назначается архивом в UTC), статусом, `complete`, ссылками на `blob_sha256` и `revision_id`.
   - Строгий инвариант: `usable_for_forecast = False` у всех записей R2.
3. **Таблица `revisions` (История ревизий):**
   - Ключ `(source_id, initial_url)`. Хранит `revision_id`, `blob_sha256`, `byte_size`, `first_seen_at`, `first_seen_seq` и `prev_revision_id`.
   - Новая попытка с неизменными байтами добавляет receipt, ссылающийся на существующую revision.
   - Изменение байтов создает новую revision с ссылкой на предыдущую (`prev_revision_id`).
   - Последовательность $A \to B \to A$ создает **три revisions и два CAS blobs** (blob A переиспользуется).
   - Неполные попытки (`complete = False`), таймауты и ошибки не создают ревизий документа.
4. **Идемпотентность и разрешение конфликтов:**
   - Повторная запись с тем же `receipt_id` и совпадающим payload возвращает существующий `ArchiveReceipt` без дублирования.
   - Повторное использование `receipt_id` с конфликтующим payload вызывает исключение `ArchiveConflictError`.
5. **Аварийная устойчивость (Failpoint & Rollback):**
   - CAS blob записывается и синхронизируется на диск (`fsync`) **до** транзакции SQLite.
   - При аварии писателя между `fsync` и коммитом SQLite база данных откатывается; ни один committed receipt или revision не может ссылаться на отсутствующий blob.
   - Предотвращение мутаций в хранилище: SQLite-триггеры `trg_receipts_no_update`, `trg_receipts_no_delete`, `trg_revisions_no_update`, `trg_revisions_no_delete` запрещают операции `UPDATE` и `DELETE` оператором `ABORT`.
6. **Point-in-Time Cutoff API (`as_of_seq`):**
   - Все методы чтения (`get_receipt`, `get_revision`, `get_latest_revision`, `query_receipts`, `query_revisions`) принимают параметр `as_of_seq`.
   - Записи и ревизии с `seq > as_of_seq` скрыты; `first_seen_at` не утекает из будущего.
7. **Ручной CLI:**
   - Команды: `--verify`, `--capture-source <id>`, `--capture-all`, `--list-receipts`, `--latest-revision`, `--read-blob <sha256>` с поддержкой `--as-of-seq` и `--source`.

---

## 3. Результаты тестов на amper

Команда полного запуска тестового набора:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
```

Результат: **165 passed in 7.16s** (exit code 0).  
Лог сохранен: `docs/research/2026-10-18/r2/test_run.log`.

Состав тестов:
- **153/153** базовых тестов R1/R1.3 (реестр, валидаторы контента, парсеры RSS/EFFR/HTML, IPC communicate, дедлайны, cleanup, allowlist, cookies/netrc) продолжают успешно проходить.
- **12/12** новых архивных тестов в `test_evidence_archive.py`:
  1. `test_exact_bytes_round_trip_utf8_chinese_and_binary`: exact bytes equality для китайского текста и произвольного бинарного содержимого.
  2. `test_loopback_gzip_content_decoded_raw_contract`: подтверждение content-decoded контракта через реальный loopback gzip сервер.
  3. `test_identical_body_and_aba_revision_lineage`: проверка переиспользования ревизий и последовательности $A \to B \to A$ (4 receipts, 3 revisions, 2 blobs).
  4. `test_different_sources_do_not_merge_identities`: разные источники с одинаковыми байтами не сливают ревизии.
  5. `test_idempotency_and_conflict_rejection`: идемпотентный повтор и выброс `ArchiveConflictError` при конфликте.
  6. `test_concurrent_writers_safety`: конкурентная запись в режиме SQLite WAL (8 потоков, 20 задач) без повреждения базы и CAS.
  7. `test_failpoint_crash_recovery`: failpoint после `fsync` до SQLite commit; целостность архива и отсутствие committed ссылок на битые файлы.
  8. `test_append_only_triggers_prevent_mutation`: триггеры SQLite блокируют `UPDATE` и `DELETE`.
  9. `test_diagnostic_snapshots_for_403_and_unverified`: полные 403 (blocked) и unverified HTML сохраняются как CAS-снапшоты с `usable=False`; неполные потоки не получают blob/revision.
  10. `test_as_of_seq_cutoff_isolation`: воспроизводимый срез состояния по номеру `seq`.
  11. `test_single_http_get_per_capture_and_no_offline_network`: строго один HTTP GET на попытку; offline-запросы не обращаются к сети.
  12. `test_strict_worker_schema_validation`: отклонение не-bool `complete` и расхождения счетчиков.

---

## 4. Результаты Live-прогона 8 канонических источников

Выполнен ограниченный live capture восьми канонических источников в новый каталог данных вне checkout:
`/home/pythia/pythia-evidence-r2-data/live-r2-run1`

Команда:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-run1 --capture-all
```

Результаты по источникам:
| Source ID | HTTP Status | Complete | Bytes | Status | Blob SHA-256 | Revision ID |
|---|---|---|---|---|---|---|
| `nyfed_effr` | 200 | True | 1 433 | `valid` | `7debc1936f7b...` | `300f8189...` |
| `fed_monetary_rss` | 200 | True | 9 687 | `valid` | `69b638039ee4...` | `1f0f73d9...` |
| `fed_speeches_rss` | 200 | True | 9 610 | `valid` | `41d3b87fbd69...` | `be194858...` |
| `bls_cpi_rss` | 403 | True | 1 323 | `blocked` | `71130c6d3bd1...` | `83ef1d13...` |
| `atlanta_gdpnow` | 200 | True | 1 242 486 | `unverified` | `fa18043964a2...` | `308a2fa7...` |
| `github_deepseek_v3_releases` | 200 | True | 2 171 | `valid` | `7f9f028109e8...` | `b6634dec...` |
| `hf_deepseek_models` | 200 | True | 1 508 | `valid` | `220c5da54dce...` | `c7a644a6...` |
| `arena_text_overall` | 403 | True | 5 702 | `blocked` | `8883197f35d7...` | `a1d10012...` |

**Итоги live-прогона:**
- Все 8 ответов сохранены в CAS (`blobs/ab/cd/...bin`) со 100% подтвержденным SHA-256.
- Полные 403 (BLS, Arena) и unverified HTML (GDPNow) сохранены как диагностические снапшоты.
- У всех 8 записей: `usable_for_forecast = False`.
- Никаких запросов по ссылкам внутри RSS/API не выполнялось.
- Проверка целостности (`--verify`):
  ```json
  {
    "healthy": true,
    "total_receipts": 8,
    "total_revisions": 8,
    "total_blobs_checked": 8,
    "errors": []
  }
  ```
- Чтение сохраненного blob через CLI (`--read-blob 7debc1...`) вернуло дословный JSON EFFR ставок NY Fed.
- Полные артефакты live-прогона сохранены в `docs/research/2026-10-18/r2/live_capture_summary.json` и `live_verify.json`.

---

## 5. Документация и сохраненные файлы

1. **`python/evaluation/source_probe.py`:** добавлен DTO `CaptureResult`, захват `headers` и `complete`, метод `capture_source`, post-decode deadline check, обновлен `probe_single`.
2. **`python/evaluation/source_probe_worker.py`:** вывод `headers` и `complete: bool`, nullable `final_url`.
3. **`python/evaluation/evidence_archive.py`:** модуль неизменяемого архива (CAS + SQLite, триггеры, идемпотентность, ревизии, `as_of_seq`, CLI).
4. **`python/evaluation/tests/test_evidence_archive.py`:** 12 тестов архива.
5. **`docs/EVIDENCE_DATA_CONTRACT.md`:** обновлен до R2 (CaptureResult, Archive contracts, nullable final_url).
6. **`docs/EVIDENCE_ARCHIVE.md`:** новое руководство по архитектуре архива, схеме, CAS и CLI.
7. **`docs/research/2026-10-18/R2_ARCHIVE_REPORT.md`:** данный отчет.
8. **`docs/research/2026-10-18/r2/`:** `live_capture_summary.json`, `live_verify.json`, `test_run.log`.

---

## 6. Ограничения и дальнейшие шаги

- В рамках R2 реализовано только неизменяемое хранилище Layer 1 (CAS + SQLite receipts/revisions).
- `usable_for_forecast` строго `false` на всех записях.
- Парсинг статей по ссылкам из RSS/API, извлечение утверждений (claims), семантическое связывание, JEV, торговые модели и публикация в канал относятся к R3+ и в соответствии с заданием **не реализовывались**.
- Исполнитель завершает работу и останавливается. Следующий этап R3 ожидает ревью и решения ведущего архитектора.
