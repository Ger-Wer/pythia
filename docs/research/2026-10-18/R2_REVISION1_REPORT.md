# Отчёт о реализации Pythia R2.1 — Исправления перед приёмкой архива свидетельств

**Дата:** 2026-10-18  
**Статус:** IMPLEMENTED  
**Ветка:** `codex/evidence-r2-archive`  
**Базовый commit ревью:** `7854de292a93d480179652dd4b04334e2cd8ad72`  
**Принятый baseline проекта:** R1 `4f0015eec61ad2a8f8eb14c68f944cae053182ad`  
**Серверное рабочее дерево:** `amper:/home/pythia/pythia-evidence-r2`  
**Локальное зеркало:** `mac:/Users/gerwer/Desktop/Pythia-evidence-r2`  
**Python venv:** `/home/pythia/pythia-paper-channel-20261017/python/venv` (Python 3.10.12, pytest 9.1.1)

---

## 1. Резюме и статус приёмки

Все замечания архитектурного ревью (`CHANGES_REQUIRED`) по R2 полностью устранены в рамках R2.1:
1. **B1: Строгий протокол worker/supervisor:** Устранена любая обратная совместимость со старыми/неполными JSON-ответами worker. Введена строгая схема из 9 обязательных ключей, запрет вывода по умолчанию, валидация типов (`bool` строго проверяется через `type is bool` и не считается `int`), `validate=True` для base64 (отклонение суффиксного мусора вроде `W10=!!`), обязательный explicit EOF и равенство счётчиков байт длине тела. До `Popen` валидируются `source_id` и канонический URL.
2. **B2: Полный immutable payload fingerprint и строгая идемпотентность:** Введён детерминированный SHA-256 отпечаток (`payload_fingerprint`), включающий все метаданные захвата, отсортированные заголовки, счётчики, длительность, таймстемпы, SHA-256 сырого тела и диагностические поля пробы. Повтор `receipt_id` с любым изменением метаданных (ETag, таймстемп, версия коллектора, тип ошибки) отклоняется с `ArchiveConflictError`. Конкурентные записи защищены double-check под `BEGIN IMMEDIATE`.
3. **B3: CAS защита от повреждений, атомарная no-clobber публикация и fsync:** Существующие CAS-файлы перед переиспользованием проверяются на длину и фактический SHA-256; повреждённый файл вызывает `ArchiveIntegrityError` без перезаписи и затирания улик. Публикация нового блоба выполняется через `os.link` (no-clobber) с `fsync` родительского каталога на Linux до начала транзакции SQLite. БД переведена на `PRAGMA synchronous = FULL`.
4. **Сохранение legacy архива:** Каталог `live-r2-run1` схемы `pythia-archive-r2/1.0` обнаружен через read-only подключение SQLite (`?mode=ro`) и защищён от изменений; активная схема повышена до `pythia-archive-r2/2.0`.
5. **Все 10 проверок ревьюера (`reviewer_controls.py`) успешно пройдены** (`all_checks_passed = True`).
6. **Полный suite тестов:** **173 passed, 0 failed** (включая реальные SIGKILL краш-тесты отдельных процессов, rollback при ошибках, loopback gzip через штатный worker).

---

## 2. Анализ и устранение дефектов ревьюера

| Группа | Дефект на commit 7854de2 | Реализация в R2.1 | Результат проверки |
|---|---|---|---|
| **B1** | Missing `complete` выводился из HTTP-статуса | 9 обязательных полей IPC (`status_code`, `headers`, `body_b64`, `bytes_received`, `bytes_stored`, `complete`, `limit_exceeded`, `final_url`, `error_msg`). Отсутствие поля → protocol error. | `missing_explicit_eof`: rejected, complete=False, блоб и ревизия не созданы. |
| **B1** | Base64 мусор (`W10=!!`) принимался | `base64.b64decode(validate=True)` → protocol error при невалидных символах. | `invalid_base64_ignored_suffix`: rejected, complete=False. |
| **B1** | Противоречивые счётчики (`bytes_received=3` vs `body=2`) | Строгая проверка `bytes_received == bytes_stored == len(body)`. | `contradictory_complete_counters`: rejected, complete=False. |
| **B1** | Пустой словарь `{"complete": True}` создавал запись | Все 9 ключей обязательны, типы строго валидируются. | `missing_required_body_metadata`: rejected, complete=False. |
| **B1** | Подмена URL/source в `probe_single(capture=...)` | Проверка `capture.source_id == cfg["source_id"]` и `initial_url == cfg["url"]`. Валидация канонического источника до `Popen`. | `Capture mismatch` исключение при несоответствии. |
| **B2** | Изменение ETag (v1 → v2) принималось как replay | Введён канонический SHA-256 отпечаток `payload_fingerprint`. | `changed_header`: `ArchiveConflictError`. |
| **B2** | Изменение времени `fetch_completed_at` принималось | Входит в `payload_fingerprint`. | `changed_fetch_time`: `ArchiveConflictError`. |
| **B2** | Изменение `collector_version` принималось | Сохраняется в БД и входит в `payload_fingerprint`. | `changed_collector_version`: `ArchiveConflictError`. |
| **B2** | Изменение ошибки (`timeout` → `resolver failed`) принималось | Сохраняется `error_msg` и входит в `payload_fingerprint`. | `changed_negative_error`: `ArchiveConflictError`. |
| **B3** | Повреждённый блоб того же размера молча переиспользовался | Проверка `st_size` и фактического SHA-256 существующего CAS-файла перед возвратом. | `existing_blob_same_size_wrong_hash`: `ArchiveIntegrityError`, повреждённый файл не перезаписан. |
| **B3** | Гонка конкурентных записей разных payload на один ID | Double-check отпечатка внутри `BEGIN IMMEDIATE` транзакции. | Ровно 1 победитель, проигравшим — `ArchiveConflictError`. |

---

## 3. Отдельный процесс: краш-тесты с SIGKILL

В соответствии с требованием архитектора реализованы реальные краш-тесты отдельных процессов через `subprocess.Popen` и `signal.SIGKILL` с подтверждением достижения границы через маркер-файл:

### 3.1 Граница (а): CAS блоб опубликован, транзакция БД ещё не начата
- **Тест:** `test_separate_process_sigkill_boundary_a_blob_published_before_db`
- **Процесс:** Отдельный дочерний скрипт выполняет `record_capture`. Тестовый хук `after_blob_published_before_db` создаёт маркер `reached:<pid>` и входит в бесконечный цикл.
- **Действие:** Родительский процесс дожидается маркера, подтверждает активность дочернего процесса (`poll() is None`), посылает `SIGKILL` и ожидает завершения (`proc.wait()`).
- **Проверка после краша:**
  - В SQLite базе осталась только исходная базовая запись (`seq == 1`), упавшая попытка отсутствует (`get_receipt` возвращает `None`).
  - На диске в CAS создан orphaned-блоб (`crashed_sha`), данные блоба целы.
  - `verify_integrity()` подтверждает `healthy = True` (наличие некоммиченных блобов без ссылок допустимо).

### 3.2 Граница (б): Транзакция SQLite открыта, запись вставлена, коммит ещё не выполнен
- **Тест:** `test_separate_process_sigkill_boundary_b_inside_db_tx_before_commit`
- **Процесс:** Дочерний скрипт вызывает тестовый хук `inside_db_tx_before_commit` внутри транзакции `BEGIN IMMEDIATE` прямо перед `conn.execute("COMMIT;")`. Создаётся маркер `reached:<pid>`.
- **Действие:** Родительский процесс посылает `SIGKILL` и собирает процесс.
- **Проверка после краша:**
  - Автоматическое восстановление SQLite WAL при следующем открытии базы откатывает незавершённую транзакцию.
  - В базе ровно 1 базовая запись, упавшая запись отсутствует.
  - `verify_integrity()` подтверждает `healthy = True`, счётчики ссылок корректны.

### 3.3 Инъекция ошибки и откат транзакции
- **Тест:** `test_injected_error_transaction_rollback`
- При возникновении исключения внутри транзакции БД выполняется `ROLLBACK`, база остаётся чистой и валидной.

---

## 4. Проверка штатного worker: Gzip и Single GET через LoopbackRouteAdapter

- **Тест:** `test_canonical_worker_loopback_gzip_and_single_get`
- **Реализация:** В `source_probe_worker.py` добавлен `LoopbackRouteAdapter`, активируемый через переменную окружения `PYTHIA_TEST_LOOPBACK_MAP`. Он маршрутизирует canonical URL префикс (`https://markets.newyorkfed.org`) на локальный HTTP-сервер без изменения рабочего allowlist и проверки схемы HTTPS.
- **Проверка:**
  - Запущен реальный `SourceProbe` со штатным `source_probe_worker.py` и `_fetch_bounded_core`.
  - HTTP-сервер отдавал gzip-сжатый JSON.
  - Счётчик подтвердил **ровно 1 GET-запрос**.
  - `requests.iter_content` прозрачно распаковал gzip, тело сохранено в CAS в исходных несжатых байтах, SHA-256 совпадает.

---

## 5. Решение по схеме БД и сохранение legacy архива

1. **Версия схемы:** Активная версия схемы повышена до `pythia-archive-r2/2.0`.
2. **Таблица `receipts` расширена:**
   - `limit_exceeded INTEGER NOT NULL`
   - `error_msg TEXT`
   - `collector_version TEXT NOT NULL`
   - `payload_fingerprint TEXT NOT NULL`
3. **Сохранение `live-r2-run1`:**
   - Архив `/home/pythia/pythia-evidence-r2-data/live-r2-run1` содержит данные первой версии (`pythia-archive-r2/1.0`).
   - Метод `_init_db()` выполняет предварительную проверку схемы через **read-only SQLite соединение** (`?mode=ro`). При обнаружении несовместимой версии выбрасывается `ArchiveIntegrityError` до выполнения какого-либо DDL и без смены режима журнала.
   - Проверено тестом `test_legacy_schema_version_detection` и прямой верификацией на сервере: файлы `live-r2-run1` не модифицируются, `mtime` не меняется.

---

## 6. Результаты контрольного живого сбора (Live Capture)

Живой сбор выполнен командой:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive     --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision1 --capture-all
```

Верификация целостности:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive     --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision1 --verify
```
Результат верификации (`live_verify.json`):
```json
{
  "healthy": true,
  "total_receipts": 8,
  "total_revisions": 8,
  "total_blobs_checked": 8,
  "errors": []
}
```

### Сводная таблица живого сбора (8 канонических источников)

| Seq | Source ID | HTTP Status | Status | Bytes Stored | Complete | Usable | SHA-256 CAS Blob | Revision ID |
|---|---|---|---|---|---|---|---|---|
| 1 | `nyfed_effr` | 200 | `valid` | 1 433 | True | **False** | `7debc1936f7b78d6b996fe118c7d5c5f242e2bdfad58c67a30e8c07e997ca9fe` | `c3b8177a-a63d-4c3e-8120-f5a60e9d6d3d` |
| 2 | `fed_monetary_rss` | 200 | `valid` | 9 687 | True | **False** | `69b638039ee490f0556ee0fa32e18585ea150c2264febe01639d67ce7b438e88` | `3465d323-5e73-42e1-897b-4d43615fba3d` |
| 3 | `fed_speeches_rss` | 200 | `valid` | 9 610 | True | **False** | `41d3b87fbd6930e46d29da762294f56f4d547f8ba168d67bc155bb947690623a` | `86e7909a-cf2a-43a5-ba3a-aa10c9a58fb2` |
| 4 | `bls_cpi_rss` | 403 | `blocked` | 1 323 | True | **False** | `fa085cfa62df01099e28f3cb2929e46a7ceee53265ebfe0fa91bf05c6d36ef5a` | `3d411c55-1f9e-4e4a-b51f-50d4eb07fe41` |
| 5 | `atlanta_gdpnow` | 200 | `unverified` | 1 242 486 | True | **False** | `fa18043964a2de135d540776b91cbb66ce36e65bf3f8e58a74ecff4f6a5b2e59` | `fa430d4c-be8e-49b0-951c-bf079a49931b` |
| 6 | `github_deepseek_v3_releases` | 200 | `valid` | 2 171 | True | **False** | `7f9f028109e8dad49bdcd4b3b33f2c3d19d624738a2e435c0d9475ab5987161e` | `75f28898-3c04-40e0-988d-e711cf30910c` |
| 7 | `hf_deepseek_models` | 200 | `valid` | 1 512 | True | **False** | `ca98b3f34a04486e0887c21968ef5eebe370fb6c0749aebf2fa346167c806f57` | `a251ea2a-463c-407e-91be-cddbd3b49dd3` |
| 8 | `arena_text_overall` | 403 | `blocked` | 5 702 | True | **False** | `67519fe223aa9ae332183a535e68871ddbef3e817078813a0e6caf123470b2da` | `a02239c0-b47c-4452-985d-5b0963b9686c` |

*Все 8 записей содержат `usable_for_forecast = false` в строгом соответствии с контрактом.*

---

## 7. Результаты тестов

Полный тестовый прогон на `amper`:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
```
- **Результат:** `173 passed in 9.41s`
- **Журнал:** `docs/research/2026-10-18/r2/revision1/test_run.log`
- **Проверенные компоненты:**
  - `test_evidence_archive.py`: 20 passed (idempotency, concurrency, reviewer controls suite, boundary A/B SIGKILL, error rollback, loopback gzip, direct DTO validation, legacy schema detection)
  - `test_source_probe.py`: 39 passed (exact 8 sources, validation rules, strict RSS feed category checks, timeout deadlines, 2 MiB stream limit, IPC pipe deadlock prevention)
  - `test_paper_sources.py`: 55 passed
  - `test_paper_telegram.py`: 55 passed
  - `test_zq_futures.py`: 4 passed

---

## 8. Артефакты ревизии

В репозитории созданы и зафиксированы артефакты в каталоге `docs/research/2026-10-18/r2/revision1/`:
1. `test_run.log`: Полный лог прогона 173 тестов с детализацией каждого теста.
2. `reviewer_controls_result.json`: Результаты исполнения скрипта контролей ревьюера (`all_checks_passed = true`).
3. `live_capture_summary.json`: Полный JSON-дамп 8 квитанций живого сбора с сервера.
4. `live_verify.json`: Результат верификации целостности архива `live-r2-revision1` (`healthy = true`).
5. `R2_REVISION1_REPORT.md`: Настоящий итоговый отчёт.

---

## 9. Ограничения и остановка

- **Задание R2.1 выполнено полностью.**
- **К этапу R3 не приступаем.** Работа остановлена в ожидании ревью ведущего архитектора.
