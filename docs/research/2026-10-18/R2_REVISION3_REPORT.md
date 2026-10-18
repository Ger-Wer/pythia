# Pythia R2.3 — Отчёт об исполнении и устранении остаточных дефектов D1/D2

**Дата:** 18 октября 2026 г.  
**Ветка:** `codex/evidence-r2-archive`  
**Базовый коммит этапа R2.2:** `e41dcc0772f9415a224e21f6ddf14dd9ba7b6435`  
**Принятый baseline разработки (R1):** `4f0015eec61ad2a8f8eb14c68f944cae053182ad`  
**Статус этапа R2.3:** **IMPLEMENTED** (все требования закрыты, дефекты устранены, R3 не начинался)

---

## 1. Краткое резюме

В рамках этапа **R2.3** устранены две последние выявленные ревьюером границы без расширения скоупа этапа R2:
1. **D1 (Единый контракт `CaptureResult` после разбора IPC):**
   - Устранена тихая перезапись заголовков: коллизии регистронезависимых имен заголовков (например, `ETag: v1` и `etag: v2`) проверяются **до** приведения к нижнему регистру. При несовпадении значений возвращается ошибка протокола (`protocol error`), исключая принцип «последний победил».
   - После декодирования результата воркера кандидат проверяется единой функцией `validate_capture_result`, включая неполные (`complete=False`) результаты.
   - Любая ошибка протокола, схемы или контракта воркера преобразуется в валидный отрицательный `CaptureResult` (`complete=False`, непустой `error_msg`, `final_url=None`, `status_code=None`, `headers={}`, `body=b""`, счетчики `None`). Архив корректно сохраняет отрицательную квитанцию без создания CAS-блоба и без создания ревизии (`revision_id is None`).
   - Сохранена генерация прямого `ValueError` для ошибочных DTO, переданных напрямую в `record_capture`.
2. **D2 (Синхронизация базовой иерархии каталогов до корня файловой системы):**
   - Барьер `_fsync_cas_barrier` расширен на всю цепочку каталогов от каталога хранения блоба (`blobs/aa/bb`) вверх до корня файловой системы (`/`).
   - Это гарантирует устойчивость всех каталогов, включая `state_dir`, `deep`, `new-base` и `T`, создаваемых архивом через `mkdir(parents=True)`, до подтверждения транзакции SQLite (`BEGIN IMMEDIATE` -> `COMMIT`).
   - Барьер применяется для всех трёх путей: новая публикация, переиспользование существующего блоба, и проигравший участник гонки публикации.
   - Ошибка `fsync` на любом предке (включая `state_dir.parent`) гарантированно предотвращает коммит квитанции в БД, а последующий повтор успешно проходит полный барьер и фиксирует квитанцию.
3. **Безопасность default-worker (автономный тест с DNS guard):**
   - Тест штатного воркера изолирован с помощью временного тестового `sitecustomize.py` DNS guard, блокирующего внешние DNS-запросы и фиксирующего вход в резолвер для канонического хоста `markets.newyorkfed.org`. При этом localhost GET = 0, а бюджет 3.0с позволяет процессу войти в резолвер без сетевого транзита.

---

## 2. Результаты исполнения контролей ревьюера (Reviewer Controls)

Контроли выполнены на сервере `amper` с изоляцией DNS:
```bash
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 docs/research/2026-10-18/r2/revision3/reviewer_controls.py
```

Результаты зафиксированы в `docs/research/2026-10-18/r2/revision3/reviewer_controls_result.json`:

- **Прежние контроли (R2.0 / R2.1):** 10 из 10 пройдены (`all_previous_requirements_met = true`).
  - Положительное воспроизведение идентичного `receipt_id`.
  - Отклонение изменения `etag`, `fetch_completed_at`, `collector_version`, `negative_error` с `ArchiveConflictError`.
  - Защита CAS от повреждения блоба (тот же размер, другой хэш).
  - 4 случая некорректного вывода воркера (`missing_explicit_eof`, `invalid_base64_ignored_suffix`, `contradictory_complete_counters`, `missing_required_body_metadata`) отклоняются с отрицательной квитанцией без ревизии.
- **Прежние дефекты R2.1:** 5 из 5 устранены (`all_r21_defects_forbidden = true`).
  - `default_worker_ambient_test_route`: 0 обращений к ambient loopback (`loopback_gets = 0`).
  - `fresh_prefix_directory_barriers`: абсолютные пути синхронизированы, отсутствующих барьеров 0 (`missing_directory_barriers = []`).
  - `reuse_before_publisher_directory_sync`: второй писатель синхронизирует каталоги (`directory_fsync_count = 7 >= 3`).
  - `direct_header_type_collision`: отклонено с `ValueError`.
  - `direct_final_url_integer`: отклонено с `ValueError`.
- **Усиленный контроль default-worker:**
  - `default_worker_ignores_env_with_dns_entry_confirmed`: `loopback_gets = 0`, маркер зафиксировал вход в `markets.newyorkfed.org`, `passed = true`.
- **Остаточные дефекты D1 / D2 (R2.3):**
  - `conflicting_case_variant_ipc_headers`: `capture_complete = false`, `error_msg` содержит сообщение о конфликте заголовков, `receipt_persisted = true`, `full_revision_created = false`, `defect_forbidden = true`.
  - `invalid_incomplete_ipc_final_url`: `capture_complete = false`, `capture_final_url = null`, квитанция успешно сохранена без ошибки `ValueError`, `full_revision_created = false`, `defect_forbidden = true`.
  - `archive_created_base_ancestry_barrier`: вложенный каталог `T/new-base/deep/state`, все предки до корня (`bb`, `aa`, `blobs`, `state`, `deep`, `new-base`, `T`, `/tmp`, `/`) включены в трассировку `fsync`, отсутствующих барьеров 0 (`missing_explicit_directory_barriers = []`), `defect_forbidden = true`.
  - `ancestry_barrier_fsync_error_and_retry`: ошибка `fsync` на `state.parent` предотвратила коммит в БД, повторная попытка успешно зафиксировала квитанцию с `seq = 1`, целостность архива подтверждена (`healthy = true`), `defect_forbidden = true`.

**Итог проверки контролей: `all_controls_passed = true` (все 15 базовых + усиленный default worker + 4 остаточных контроля успешно пройдены).**

---

## 3. Результаты полного тестового сьюта

- **Команда:**
  ```bash
  /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
  ```
- **Результат:** `184 passed in 10.58s` (exit code 0)
- **Журнал:** `docs/research/2026-10-18/r2/revision3/test_run.log`
- **Состав тестов:** 184 теста (все прошли успешно, 0 упавших, 0 пропущенных).

---

## 4. Результаты контрольного живого сбора (Live Capture Revision 3)

Живой сбор 8 канонических источников выполнен в новый изолированный каталог состояния:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision3 \
    --capture-all > docs/research/2026-10-18/r2/revision3/live_capture_summary.json
```

Верификация целостности архива:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision3 \
    --verify > docs/research/2026-10-18/r2/revision3/live_verify.json
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
| 1 | `nyfed_effr` | 200 | `valid` | 1,433 | True | **False** | `7debc1936f7b78d6…` | `c1ec7abd-7124-41…` | `7debc1936f7b78d6d43ac93a4695fe13269f74e960a31726b80769de80fd1760` | `c1ec7abd-7124-41d3-990c-13918e85072d` |
| 2 | `fed_monetary_rss` | 200 | `valid` | 9,687 | True | **False** | `69b638039ee490f0…` | `26b40558-1014-47…` | `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1` | `26b40558-1014-47f2-9b10-d7b8007a07d0` |
| 3 | `fed_speeches_rss` | 200 | `valid` | 9,610 | True | **False** | `41d3b87fbd6930e4…` | `8b8f0b0b-b42c-4d…` | `41d3b87fbd6930e4285ef366f574d20ee3a8db2ab295fceb5d8fa68448055930` | `8b8f0b0b-b42c-4d02-9a68-596ba692a046` |
| 4 | `bls_cpi_rss` | 403 | `blocked` | 1,323 | True | **False** | `f0d2ea46e3e48fce…` | `0b8fef3e-9ffd-4a…` | `f0d2ea46e3e48fcecc39823f04d353b36fc91bf46685a54d7abc843fdd44a1de` | `0b8fef3e-9ffd-4ab2-ab5d-a628bedc8f1f` |
| 5 | `atlanta_gdpnow` | 200 | `unverified` | 1,242,486 | True | **False** | `fa18043964a2de13…` | `3609d1ed-1599-4a…` | `fa18043964a2de13a5e7cfceb4c385b006956d9f8a742d0e2a161776e63ab883` | `3609d1ed-1599-4add-a8e9-d21a10cf9d00` |
| 6 | `github_deepseek_v3_releases` | 200 | `valid` | 2,171 | True | **False** | `7f9f028109e8dad4…` | `9abb8e48-12d4-45…` | `7f9f028109e8dad49bdcd4b3b33f2c3d19d624738a2e435c0d9475ab5987161e` | `9abb8e48-12d4-45e3-b117-089d149f6707` |
| 7 | `hf_deepseek_models` | 200 | `valid` | 1,512 | True | **False** | `26d62607f2cf5627…` | `3869c59b-e5f1-41…` | `26d62607f2cf5627bd08d1e7ee9dba94eb236324739a61e10e5ba307a79122b6` | `3869c59b-e5f1-4136-93cd-fcda3daf0ecf` |
| 8 | `arena_text_overall` | 403 | `blocked` | 5,702 | True | **False** | `4aba8b4cde836a9d…` | `b3dffdfc-1ce6-46…` | `4aba8b4cde836a9db9ebe34b7dc175a09bce1df7d4ee11a5a710afd34be13823` | `b3dffdfc-1ce6-46e1-862d-c0e9eb877f01` |

- Все 8 записей содержат `usable_for_forecast = False` в строгом соответствии с контрактом.
- Все хэши SHA-256 и размеры на 100% совпадают с физическими файлами `.bin` в хранилище CAS на диске.

---

## 5. Сохранность прежних архивов и окружения

1. **Существующие живые архивы:**
   - `live-r2-run1`: 8 receipts, 8 revisions (не модифицировался, защищён проверкой схемы v1 `?mode=ro`).
   - `live-r2-revision1`: 8 receipts, 8 revisions (не модифицировался).
   - `live-r2-revision2`: 8 receipts, 8 revisions (не модифицировался).
2. **Продакшен и внешнее окружение:**
   - Production коммит `ee6460f31149917e29463ebb6f54c1d80e6c4562` и родительский коммит `c21967f651eae899ccad1da29d2c9aa6133875cb` не затрагивались.
   - Контейнер `pythia-v2` (`sha256:8957620732c68dfd8c7bbf65241486691b7c89158ea41b060f1b2bd7251c82ec`), запущенный `2026-08-29T08:36:28.679362795Z`, не перезапускался и не изменялся.
   - Telegram, торговые сделки, внешние сервисы и база данных MemPalace не изменялись.

---

## 6. Каталог артефактов ревизии 3

Каталог `docs/research/2026-10-18/r2/revision3/`:
1. `test_run.log`: Полный лог прогона 184 тестов.
2. `reviewer_controls.py`: Исполняемый скрипт воспроизведения контролей.
3. `reviewer_controls_result.json`: Результаты исполнения контролей ревьюера (`all_controls_passed = true`).
4. `live_capture_summary.json`: Полный JSON-дамп живого сбора 8 канонических источников.
5. `live_verify.json`: Результат верификации целостности архива `live-r2-revision3`.
6. `R2_REVISION3_REPORT.md`: Настоящий итоговый отчёт в `docs/research/2026-10-18/`.

---

## 7. Границы исполнения и остановка

- **Задание R2.3 выполнено полностью:**
  - Дефекты D1 и D2 устранены в исходном коде (`source_probe.py`, `evidence_archive.py`).
  - Документация актуализирована (`EVIDENCE_DATA_CONTRACT.md`, `EVIDENCE_ARCHIVE.md`).
  - Регрессионные тесты добавлены в тестовый сьют (`test_evidence_archive.py`).
  - Полный сьют из 184 тестов проходит на сервере `amper`.
  - Контрольный живой сбор выполнен в новый архив `live-r2-revision3`.
- **К этапу R3 не приступаем.** Работа остановлена после фиксации отчёта и коммита.
