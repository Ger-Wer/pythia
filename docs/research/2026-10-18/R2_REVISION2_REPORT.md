# Pythia R2.2 — Итоговый отчёт по закрытию оставшихся границ архива

- **Статус:** IMPLEMENTED (R2.2 полностью выполнен)
- **Baseline ревизии:** `829a2b17e3f0077fb4e860cc27865b044dbe467c`
- **Проектный baseline:** `4f0015eec61ad2a8f8eb14c68f944cae053182ad` (R1)
- **Ветка:** `codex/evidence-r2-archive`
- **Рабочий хост:** `amper` (`/home/pythia/pythia-evidence-r2`)
- **Python:** Python 3.10.12 (`/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3`)
- **Дата:** 2026-10-18

---

## 1. Сводка выполненных работ (Дефекты C1, C2, C3)

В ревизии R2.2 устранены три ключевых дефекта, выявленные при архитектурном ревью `829a2b1`:

### C1. Тестовая переадресация перенесена исключительно в тестовую fixture
- **Проблема в R2.1:** Штатный `source_probe_worker.py` считывал переменную окружения `PYTHIA_TEST_LOOPBACK_MAP` и монтировал `LoopbackRouteAdapter`, подменяя реальные HTTPS URL на тестовые targets даже при вызове обычного `SourceProbe()` вне тестов.
- **Решение R2.2:**
  - Полностью удалены чтение `PYTHIA_TEST_LOOPBACK_MAP` и класс `LoopbackRouteAdapter` из `source_probe_worker.py`. Штатный worker и CLI теперь не имеют механизмов тестовой подмены URL.
  - Вся логика тестовой адаптации перенесена в тестовую фикстуру `fixture_worker` (`python/evaluation/tests/test_evidence_archive.py`), которая запускается через штатный параметр `worker_cmd` класса `SourceProbe`.
  - Сохранены проверки gzip-распаковки и single-GET через fixture worker.
  - Добавлен тест, подтверждающий, что default worker полностью игнорирует переменную `PYTHIA_TEST_LOOPBACK_MAP` и не отправляет запросы на loopback.

### C2. Единый строгий контракт CaptureResult без неявного str-приведения
- **Проблема в R2.1:** Прямой API архива `record_capture` принимал `headers={"etag": 1}`, а `_compute_payload_fingerprint` выполнял `str()` к ключам и значениям заголовков. В результате `headers={"etag": "1"}` ошибочно принимался как replay. Кроме того, `complete=True, final_url=123` создавал полную ревизию без валидации типов.
- **Решение R2.2:**
  - Реализована строгая валидация контракта `validate_capture_result` в `python/evaluation/source_probe.py` и интегрирована в `_validate_capture` в `python/evaluation/evidence_archive.py`. Валидация выполняется **до** вычисления fingerprint и обращения к базе.
  - Запрещено приведение типов через `str()`, `int()`, `bool()`. Неверные типы в полях DTO (`headers` с нестроковыми ключами/значениями, целочисленный `final_url`, `bool` в `duration_ms` и т.д.) немедленно выбрасывают `ValueError`.
  - Заголовки приводятся к каноническому нижнему регистру без потери различий; ключи сортируются детерминированно.
  - При ошибках протокола worker формируются честные отрицательные квитанции со строгим соответствием контракту.

### C3. Барьер устойчивости CAS для всех путей до фиксации транзакции БД
- **Проблема в R2.1:** Fsync вызывался только для leaf-файла `blobs/aa/bb.bin`, но родительские каталоги `blobs/aa` и `blobs` не синхронизировались. Кроме того, ветви повторного использования (existing blob) и проигравшей гонки публикации (concurrent race loser) вообще не выполняли fsync. Второй поток мог зафиксировать квитанцию в БД при нуле fsync каталогов.
- **Решение R2.2:**
  - В `EvidenceArchive` реализован централизованный барьер `_fsync_cas_barrier(dest_path, sync_file)`. Он синхронизирует сам файл (если требуется) и все необходимые родительские каталоги пути CAS: `dest_path.parent` (`blobs/aa/bb`), `dest_path.parent.parent` (`blobs/aa`) и `self.blobs_dir` (`blobs`).
  - Барьер вызывается во всех трёх сценариях метода `_store_blob`:
    1. Новая публикация (new publish): sync файла и каталогов.
    2. Повторное использование (existing blob reuse): проверка размера и SHA, затем sync каталогов.
    3. Проигравшая гонка (concurrent race loser): проверка размера и SHA созданного конкурентом файла, затем sync каталогов.
  - Добавлены детерминированные тесты: трассировка `os.fsync` подтверждает порядок барьеров до `conn.commit()`; задержка первого писателя после `os.link` заставляет второго писателя выполнить барьер каталогов; ошибка fsync предотвращает фиксацию квитанции в БД.

---

## 2. Результаты контролей ревьюера (15/15 passed)

Скрипт контролей: `docs/research/2026-10-18/r2/revision2/reviewer_controls.py`  
Результат исполнения: `docs/research/2026-10-18/r2/revision2/reviewer_controls_result.json`

- **Предыдущие контроли R2.0 (10/10):** `all_previous_requirements_met = true`
  1. `identical_payload_positive`: replay успешен, receipt_id сохранён.
  2. `changed_header`: `ArchiveConflictError` (конфликт метаданных).
  3. `changed_fetch_time`: `ArchiveConflictError`.
  4. `changed_collector_version`: `ArchiveConflictError`.
  5. `changed_negative_error`: `ArchiveConflictError`.
  6. `existing_blob_same_size_wrong_hash`: `ArchiveIntegrityError`, повреждённый блоб не перезаписывается.
  7. `missing_explicit_eof`: отрицательный capture с ошибкой протокола, ревизия не создана.
  8. `invalid_base64_ignored_suffix`: отрицательный capture с ошибкой base64.
  9. `contradictory_complete_counters`: отрицательный capture с несоответствием счётчиков.
  10. `missing_required_body_metadata`: отрицательный capture с отсутствующими полями.

- **Проверки исправлений дефектов R2.1 (5/5):** `all_r21_defects_forbidden = true`
  1. `default_worker_ambient_test_route`: `loopback_gets = 0`, внешняя переменная `PYTHIA_TEST_LOOPBACK_MAP` проигнорирована.
  2. `fresh_prefix_directory_barriers`: каталоги `blobs`, `blobs/e7`, `blobs/e7/e6` синхронизированы через fsync.
  3. `reuse_before_publisher_directory_sync`: второй писатель синхронизирует каталоги (`directory_fsync_count = 3`) до фиксации в БД.
  4. `direct_header_type_collision`: отклонено с `ValueError` до записи.
  5. `direct_final_url_integer`: отклонено с `ValueError` до записи.

**Итог: 15 из 15 контролей пройдены успешно (`all_controls_passed = true`).**

---

## 3. Результаты полного тестового прогона

- **Команда:**
  ```bash
  /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
  ```
- **Результат:** `180 passed in 10.27s` (exit code 0)
- **Журнал:** `docs/research/2026-10-18/r2/revision2/test_run.log`
- **Состав тестов:**
  - `test_evidence_archive.py`: 27 passed (барьеры CAS, фикстурный worker, DTO валидация, идемпотентность, A/B SIGKILL, откат при сбоях)
  - `test_source_probe.py`: 39 passed (строгий контракт 8 источников, лимиты памяти, таймауты)
  - `test_paper_sources.py`: 55 passed
  - `test_paper_telegram.py`: 55 passed
  - `test_zq_futures.py`: 4 passed

---

## 4. Замечание по историческому расхождению в отчёте R2.1

В отчёте `docs/research/2026-10-18/R2_REVISION1_REPORT.md` у первых 5 строк таблицы сводки живого сбора значения SHA-256 и Revision ID имели расхождения суффиксов по сравнению с файлом `docs/research/2026-10-18/r2/revision1/live_capture_summary.json` (префиксы и размеры при этом совпадали). Файл `R2_REVISION1_REPORT.md` сохранён в истории репозитория без изменений как исторический артефакт.

В текущем отчёте R2.2 все идентификаторы, хэши и размеры формируются **строго механически** непосредственно из `live_capture_summary.json` и верифицируются против базы данных `archive.db` и физических файлов в каталоге `blobs/`.

---

## 5. Результаты контрольного живого сбора (Live Capture Revision 2)

Живой сбор 8 канонических источников выполнен в новый каталог состояния:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision2 \
    --capture-all > docs/research/2026-10-18/r2/revision2/live_capture_summary.json
```

Верификация целостности архива:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision2 \
    --verify > docs/research/2026-10-18/r2/revision2/live_verify.json
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

### Сводная таблица живого сбора (механически сгенерирована из live JSON и БД)

| Seq | Source ID | HTTP Status | Status | Bytes Stored | Complete | Usable | SHA-256 (prefix) | Revision ID (prefix) | Full SHA-256 CAS Blob | Full Revision ID |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `nyfed_effr` | 200 | `valid` | 1,433 | True | **False** | `7debc1936f7b78d6…` | `4b7c1345-8a50-46…` | `7debc1936f7b78d6d43ac93a4695fe13269f74e960a31726b80769de80fd1760` | `4b7c1345-8a50-462c-b47f-3c5408a04d47` |
| 2 | `fed_monetary_rss` | 200 | `valid` | 9,687 | True | **False** | `69b638039ee490f0…` | `04e79595-aa6f-49…` | `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1` | `04e79595-aa6f-49fb-9546-00242da46ca8` |
| 3 | `fed_speeches_rss` | 200 | `valid` | 9,610 | True | **False** | `41d3b87fbd6930e4…` | `c1cc018e-18af-48…` | `41d3b87fbd6930e4285ef366f574d20ee3a8db2ab295fceb5d8fa68448055930` | `c1cc018e-18af-48b7-90a6-0f16c8a2fd1a` |
| 4 | `bls_cpi_rss` | 403 | `blocked` | 1,323 | True | **False** | `8522388cc684a0a8…` | `3f115561-a4e2-44…` | `8522388cc684a0a869a16a5d0faddb5eb298237391028403d1b53de252b01112` | `3f115561-a4e2-4401-8f65-2aaaf2dfb6b8` |
| 5 | `atlanta_gdpnow` | 200 | `unverified` | 1,242,486 | True | **False** | `fa18043964a2de13…` | `682bffd3-9d48-48…` | `fa18043964a2de13a5e7cfceb4c385b006956d9f8a742d0e2a161776e63ab883` | `682bffd3-9d48-4876-b77c-abed3cdea1d6` |
| 6 | `github_deepseek_v3_releases` | 200 | `valid` | 2,171 | True | **False** | `7f9f028109e8dad4…` | `324c3f21-2399-4a…` | `7f9f028109e8dad49bdcd4b3b33f2c3d19d624738a2e435c0d9475ab5987161e` | `324c3f21-2399-4a66-bf6d-557686032ee6` |
| 7 | `hf_deepseek_models` | 200 | `valid` | 1,512 | True | **False** | `c7ce0ca16dd8fe21…` | `28c51276-6948-48…` | `c7ce0ca16dd8fe21da273a5a7566e98b18ea25676d3cdd8257ece238192e2bfa` | `28c51276-6948-48e0-bd8d-4d22efd35b49` |
| 8 | `arena_text_overall` | 403 | `blocked` | 5,702 | True | **False** | `d718b6b7dc441166…` | `c5b3e86b-392d-47…` | `d718b6b7dc4411668c6ab9c01cebc8319b4bac38e58a6ee92e179b584ddfaa93` | `c5b3e86b-392d-4728-bdba-0daaa49cfec9` |

*Все 8 записей содержат `usable_for_forecast = False` в строгом соответствии с контрактом.*  
*Все хэши SHA-256 и размеры на 100% совпадают с физическими файлами `.bin` в хранилище CAS.*

---

## 6. Актуализация документации и границы гарантий

1. **Устранено противоречие в документации:**
   - В `docs/EVIDENCE_DATA_CONTRACT.md` значение `PRAGMA synchronous` исправлено с устаревшего `NORMAL` на фактическое `FULL`.
2. **Документированы границы гарантий:**
   - Чётко зафиксировано, что тестовая переадресация через fixture изолирована в `worker_cmd` и не поддерживается в runtime worker.
   - Зафиксированы реальные границы тестов устойчивости: тесты моделируют сбои через SIGKILL и трассировку барьеров `fsync`, однако физическое отключение питания диска или потеря питания контроллера кэша накопителя требуют специализированного аппаратного стенда.

---

## 7. Каталог артефактов ревизии 2

Каталог `docs/research/2026-10-18/r2/revision2/`:
1. `test_run.log`: Полный лог прогона 180 тестов.
2. `reviewer_controls.py`: Исполняемый скрипт воспроизведения 15 контролей.
3. `reviewer_controls_result.json`: Результаты исполнения контролей ревьюера.
4. `live_capture_summary.json`: Полный JSON-дамп живого сбора 8 источников.
5. `live_verify.json`: Результат верификации целостности архива.
6. `R2_REVISION2_REPORT.md`: Настоящий отчёт в `docs/research/2026-10-18/`.

---

## 8. Ограничения и остановка

- **Задание R2.2 выполнено полностью.**
- **К этапу R3 не приступаем.** Работа остановлена в строгом соответствии с заданием.
