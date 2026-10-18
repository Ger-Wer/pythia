# Pythia R2.4 — Отчёт об исполнении: завершающий deadline после валидации

**Дата:** 18 октября 2026 г.  
**Ветка:** `codex/evidence-r2-archive`  
**Базовый коммит этапа R2.3:** `bd25e57e5a3af9d5761db6ad9327210172746da4`  
**Принятый baseline разработки (R1):** `4f0015eec61ad2a8f8eb14c68f944cae053182ad`  
**Статус этапа R2.4:** **IMPLEMENTED** (все требования закрыты, дефект устранён, R3 не начинался)

---

## 1. Краткое резюме

В рамках этапа **R2.4** устранено последнее оставшееся замечание к этапу R2:
- В `python/evaluation/source_probe.py` реализована завершающая проверка общего срока (`deadline`) и актуализация доверенных родительских метаданных (`duration_ms` и `fetch_completed_at`) **после** выполнения общей валидации кандидата (`validate_capture_result(candidate)`).
- Если общий бюджет времени истекает во время выполнения родительской валидации (или вытеснения процесса супервизора), результат отклоняется и преобразуется в валидный отрицательный `CaptureResult` с `complete=False`, причиной истечения дедлайна, пустым телом, пустыми заголовками и статусом `None`.
- Отрицательный результат штатно фиксируется архивом как квитанция с `status="network_error"` без создания CAS-блоба и без создания ревизии (`revision_id is None`, `blob_sha256 is None`).
- Для успешных запросов `duration_ms` и `fetch_completed_at` актуализируются с учётом времени валидации через `dataclasses.replace`.
- Сохранены все прежние гарантии: строгая 9-ключевая схема IPC, проверка коллизий заголовков до lowercase-нормализации, единый контракт `validate_capture_result` для кандидатов, полный барьер каталогов предков до `/`, crash recovery и защита legacy-архива.

---

## 2. Реализация в кодовой базе

1. **`python/evaluation/source_probe.py`:**
   - Импортирована функция `replace` из модуля `dataclasses`.
   - В метод `capture_source` после блока `validate_capture_result(candidate)` добавлена повторная проверка истечения общего тайм-аута:
     ```python
     final_elapsed = time.monotonic() - start_monotonic
     if final_elapsed > self.timeout:
         return make_negative_capture(
             f"Overall source deadline exceeded ({self.timeout}s): elapsed {final_elapsed:.3f}s; response rejected."
         )

     return replace(
         candidate,
         fetch_completed_at=utc_now_iso(),
         duration_ms=final_elapsed * 1000.0,
     )
     ```
2. **`docs/EVIDENCE_DATA_CONTRACT.md` и `docs/EVIDENCE_ARCHIVE.md`:**
   - Документировано, что финальная фиксация дедлайна, измерение длительности и назначение `fetch_completed_at` выполняются после валидации кандидата.

---

## 3. Результаты исполнения контролей ревьюера (Reviewer Controls)

Контроли выполнены на сервере `amper`:
```bash
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 docs/research/2026-10-18/r2/revision4/reviewer_controls.py
```

Результаты зафиксированы в `docs/research/2026-10-18/r2/revision4/reviewer_controls_result.json`:

- **Повторение 20 контролей R2.3 (через `runpy` без `__main__`):** `submitted_controls_passed = true`.
  - 10 базовых инвариантов R2.0/R2.1 (идемпотентность, конфликты, CAS corruption, malformed IPC).
  - 5 дефектов R2.1 (ambient test route, fresh-dirs, publish race fsync, direct header type, direct final_url).
  - Усиленный контроль default-worker с блокировкой DNS через `sitecustomize.py`.
  - 4 контроля R2.3: коллизии заголовков (D1), неполный final_url (D1), барьер предков до корня (D2), откат при ошибке fsync state.parent и повтор (D2).
- **Четыре независимых проверки D2 (полная ансестрия до корня `/`):** `all_d2_passed = true`.
  - `fresh_full_ancestry_before_commit`: `missing = []`, `passed = true`.
  - `reuse_full_ancestry_before_commit`: `missing = []`, `passed = true`.
  - `state_parent_failure_and_retry`: `error_raised = true`, `archive_empty_after_failure = true`, `retry_seq = 1`, `missing = []`, `passed = true`.
  - `reuse_while_publisher_paused_full_ancestry`: `publisher_was_paused = true`, `missing = []`, `passed = true`.
- **Контроль пересечения дедлайна внутри валидации (`deadline_check`):**
  - Бюджет: `0.5s`, искусственная задержка валидатора: `0.8s`.
  - Реальная длительность вызова (`wall_seconds`): `0.816378s`.
  - Зафиксированная длительность (`reported_duration_ms`): `816.114139ms`.
  - Зафиксированная ошибка: `"Overall source deadline exceeded (0.5s): elapsed 0.816s; response rejected."`.
  - Статус захвата: `capture_complete = false`.
  - Квитанция в архиве: `status = "network_error"`.
  - Создание полной ревизии: `full_revision_created = false`.
  - Хэш блоба: `full_blob_hash = null`.
  - **Требование выполнено:** `requirement_met = true`.
  - **Остаточный дефект устранён:** `remaining_deadline_defect_reproduced = false`.

**Итог: `all_controls_passed = true` (все контроли зелёные).**

---

## 4. Результаты полного тестового сьюта

- **Команда:**
  ```bash
  /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v
  ```
- **Результат:** `187 passed in 11.54s` (exit code 0)
- **Журнал:** `docs/research/2026-10-18/r2/revision4/test_run.log`
- **Новые серверные регрессионные тесты (добавлены в `test_evidence_archive.py`):**
  1. `test_post_validation_deadline_positive_fixture_without_delay_remains_complete`: положительный результат без задержки остаётся `complete=True`, сохраняются точные байты и хэш в CAS, создаётся ревизия.
  2. `test_post_validation_deadline_crossed_inside_shared_validation_yields_negative_receipt`: при пересечении дедлайна внутри валидации вызывается настоящий валидатор, возвращается отрицательный результат с сообщением об истечении дедлайна, архив сохраняет квитанцию без CAS-блоба и без ревизии, длительность включает задержку.
  3. `test_post_validation_deadline_sufficient_budget_with_controlled_delay_remains_complete`: при достаточном бюджете (2.0с) и задержке валидации (0.15с) результат остаётся `complete=True`, длительность и `fetch_completed_at` честно отражают задержку, квитанция и ревизия успешно фиксируются.

---

## 5. Сохранность прежних архивов и продакшена

1. **Существующие живые архивы (проверены через SQLite `mode=ro` и прямое чтение файлов):**
   - `live-r2-run1`: 8 receipts, 8 revisions, 8 blobs verified OK.
   - `live-r2-revision1`: 8 receipts, 8 revisions, 8 blobs verified OK.
   - `live-r2-revision2`: 8 receipts, 8 revisions, 8 blobs verified OK.
   - `live-r2-revision3`: 8 receipts, 8 revisions, 8 blobs verified OK.
   - Все 32 блоба целы, хэши и размеры совпадают, каталоги не изменялись.
2. **Продакшен и внешнее окружение:**
   - Production коммит `ee6460f31149917e29463ebb6f54c1d80e6c4562` и родительский коммит `c21967f651eae899ccad1da29d2c9aa6133875cb` не затрагивались.
   - Контейнер `pythia-v2` (`sha256:8957620732c68dfd8c7bbf65241486691b7c89158ea41b060f1b2bd7251c82ec`), запущенный `2026-08-29T08:36:28.679362795Z`, не перезапускался и не изменялся.
   - Telegram, торговые сделки, внешние сервисы и база данных MemPalace не изменялись.

---

## 6. Каталог артефактов ревизии 4

Каталог `docs/research/2026-10-18/r2/revision4/`:
1. `test_run.log`: Полный лог прогона 187 тестов (`187 passed in 11.54s`).
2. `reviewer_controls.py`: Исполняемый скрипт воспроизведения контролей.
3. `reviewer_controls_result.json`: Результаты исполнения контролей ревьюера (`all_controls_passed = true`).
4. `R2_REVISION4_REPORT.md`: Настоящий итоговый отчёт в `docs/research/2026-10-18/`.

---

## 7. Границы исполнения и остановка

- **Задание R2.4 выполнено полностью.**
- **К этапу R3 самостоятельно не приступаем.** Работа остановлена после фиксации отчёта и коммита.
