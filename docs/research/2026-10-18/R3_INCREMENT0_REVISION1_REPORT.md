# Pythia R3.0.1 — Отчёт об исправлении достоверности первого инкремента (Revision 1)

**Дата:** 18 октября 2026 г.  
**Ветка:** `codex/evidence-r3-extraction`  
**Начальный коммит задания:** `62aa2301845f264d099412fbfe350081158f4846`  
**Принятый baseline проекта:** R2 `b4c6d0f3de5ff0a63e0ecd55fdd45ecb69ca5354`  
**Worktree на amper:** `/home/pythia/pythia-evidence-r3`  
**Локальное зеркало (Mac):** `/Users/gerwer/Desktop/Pythia-evidence-r3`  
**Статус инкремента R3.0.1:** **IMPLEMENTED** (все 7 групп дефектов F1–F7 устранены, 13 проверок reviewer_controls пройдены, 35 из 35 proofs валидны, R3.1 не начинался)

---

## 1. Резюме исправлений F1–F7

В рамках ревизии R3.0.1 устранены все замечания независимого ревью архитектора без расширения скоупа продукта и без модификации существующих живых архивов:

| Группа | Проблема в R3.0 | Реализация в R3.0.1 | Статус |
|---|---|---|---|
| **F1** | `json.loads` превращал числа в `float`, из-за чего `3.8800000000000001` округлялось до `"3.88"` | В `json.loads` установлен `parse_float=Decimal`. Числовые лексемы парсятся напрямую в точный `Decimal` без промежуточного `float`. Сохранены строгие проверки на конечность (`is_finite()`), запрет `bool`, `NaN`, `Infinity`, дубликатов ключей и валидация дат. Малые экспоненты (`1e-400`) не зануляются. Опциональные поля target/volume выведены из скоупа модели факта, как рекомендовано архитектором. | **RESOLVED** |
| **F2** | `utf-8-sig` удалял BOM (`\xef\xbb\xbf`) из `raw_quote_text`, что ломало `verify_against_blob` для 30 Fed RSS proofs | Исходный блоб декодируется в строгом UTF-8 с сохранением `\ufeff` в `raw_quote_text`. Символ BOM отсекается только на входе XML-парсера (`xml_text = blob_text[1:] if blob_text.startswith("\ufeff") else blob_text`). Каждый emitted proof независимо валидируется через `ProofSpan.verify_against_blob` перед добавлением в список фактов. На повторном демо получено **35 из 35 валидных proofs** (100%). | **RESOLVED** |
| **F3** | Допуск к экстракции доверял полю `status=valid` в БД, пропуская HTTP 403, поддельный feed identity и RSS v9.9 | Введён независимый гейт содержимого и транспорта: 1) чистый транспорт — строгая проверка `rec.http_status == 200` (HTTP 403 отклоняется); 2) независимая валидация содержимого через `SourceProbe.validate_content` (чужой канал / невалидный URL отклоняется); 3) строгая проверка версии RSS: поддерживается только `version="2.0"`, неизвестные версии отклоняются с фиксацией в `rejections`. | **RESOLVED** |
| **F4** | 1) Чистая квитанция HTTP 200 с `blob_sha256=NULL` и `revision_id=NULL` публиковалась с rejection. 2) URI `file:{path}?mode=ro` не экранировал `?/#`, создавая sibling-файлы. | 1) В `ReadOnlyArchiveReader` для квитанций с `complete=True`, `http_status=200`, `error_msg=None` отсутствие блоба или ревизии вызывает `ReaderIntegrityError` без создания бандла. Легитимные отрицательные квитанции (timeout/partial) без блоба обрабатываются штатно. 2) Подключение к БД выполняется строго через `path.resolve().as_uri() + "?mode=ro"`, что корректно процент-кодирует `?` (`%3F`) и `#` (`%23`) и гарантирует режим `mode=ro` без создания посторонних файлов. | **RESOLVED** |
| **F5** | CLI создавал синтетический дескриптор по умолчанию; `rules=None` допускал статус `verified`; версии исчезали при нуле фактов | 1) Параметр `--event-descriptor-json` в CLI сделан строго обязательным (`required=True`), дефолт убран. 2) В `EventDescriptor.__post_init__` добавлена валидация непустых строк `event_id` и `question`, а также инвариант: при `rules is None` статус контракта не может быть `verified` или `complete`. 3) Поля `extractor_version`, `fact_schema_version` и `dossier_version` зафиксированы на верхнем уровне полезной нагрузки и в манифесте **всегда**, включая досье с нулём фактов. | **RESOLVED** |
| **F6** | Каждому origin присваивался `independent_evidence_count=1`; канонический URL NYFed указывал на неверный endpoint | 1) Поле `independent_evidence_count` полностью удалено из групп происхождения; наблюдения и сущности хранятся раздельно, независимость не заявляется (`not_assessed`). 2) Канонический URL серии EFFR приведен к действительному эндпоинту `CANONICAL_SOURCES["nyfed_effr"]`: `https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json`. 3) В манифесте для каждого receipt сохранены `initial_url` и `final_url`. | **RESOLVED** |
| **F7** | Метод `exists() + os.replace()` допускал перезапись конкурирующего файла при гонке публикации | Публикация временного файла выполняется через атомарный жесткий линк `os.link(tmp_file, target)`. При `FileExistsError` существующий файл сравнивается по байтам: если байты идентичны — выполняется идемпотентный replay; если байты отличаются — генерируется `DossierError` с сохранением содержимого чужого файла (no-clobber). | **RESOLVED** |

---

## 2. Результаты тестов и проверок на сервере `amper`

Все тесты и проверки запускались исключительно на сервере `amper` под пользователем `pythia` в виртуальном окружении `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3`.

### 2.1. Регрессионные тесты R3.0.1 (`test_event_dossier.py`)
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/test_event_dossier.py -v
```
**Результат:** `21 passed in 1.54s` (exit code 0).
- `test_f1_exact_decimal_without_float_rounding` — PASSED
- `test_f2_bom_preservation_and_verbatim_proof` — PASSED
- `test_f3_content_and_transport_gate_rejects_untrusted_valid_labels` — PASSED
- `test_f4_complete_clean_receipt_missing_mandatory_blob` — PASSED
- `test_f4_read_only_uri_escaping` — PASSED
- `test_f5_descriptor_validation_and_frozen_versions_in_empty_dossier` — PASSED
- `test_f6_origin_groups_no_claimed_independence_and_correct_nyfed_url` — PASSED
- `test_f7_atomic_no_clobber_publication_and_collision_rejection` — PASSED
- Все 13 базовых тестов R3.0 адаптированы под корректные контракты и успешно пройдены.

### 2.2. Полный тестовый сьют Pytest
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
**Результат:** `208 passed in 12.57s` (exit code 0).  
Лог полного прогона сохранён в [`docs/research/2026-10-18/r3/revision1/test_run.log`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision1/test_run.log). Сетевых запросов: 0.

### 2.3. Независимый аудит архитектора (`reviewer_controls.py`)
Запуск скрипта архитектора с указанием на новый артефакт:
```bash
PYTHIA_REVIEW_DEMO_ARTIFACT=docs/research/2026-10-18/r3/revision1/demo_dossier_bundle.json \
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 /tmp/reviewer_controls.py
```
**Вывод:**
```json
{
  "reviewed_commit": "62aa2301845f264d099412fbfe350081158f4846",
  "utc": "2026-10-18T19:02:58.906417+00:00",
  "network_attempts_blocked": 0,
  "controls": [
    {"case": "positive_fact_proof_digest_idempotence", "passed": true},
    {"case": "exact_decimal_without_float_rounding", "expected": "3.8800000000000001", "actual": "3.8800000000000001", "receipt_status": "valid", "requirement_met": true},
    {"case": "bom_rss_verbatim_proof", "receipt_status": "valid", "fact_count": 1, "proof_verifies": true, "requirement_met": true},
    {"case": "source_identity_not_just_stored_valid_label", "reference_validator_status": "invalid_content", "fact_count": 0, "requirement_met": true},
    {"case": "unsupported_rss_version", "receipt_status": "valid", "fact_count": 0, "requirement_met": true},
    {"case": "clean_transport_gate_not_stored_valid_label", "http_status": 403, "stored_status": "valid", "fact_count": 0, "requirement_met": true},
    {"case": "complete_clean_receipt_missing_mandatory_blob", "detail": "Complete clean HTTP 200 receipt 'receipt-missing-links' is missing mandatory CAS blob and revision linkage", "requirement_met": true},
    {"case": "read_only_uri_escaping", "valid_archive_read": true, "unexpected_sibling_created": false, "error": null, "requirement_met": true},
    {"case": "explicit_descriptor_validation", "invalid_descriptor_rejected": true, "requirement_met": true},
    {"case": "cli_requires_explicit_event_descriptor", "exit_code": 2, "published_default_event": false, "requirement_met": true},
    {"case": "atomic_no_clobber_publication", "collision_rejected": true, "competing_bytes_preserved": true, "requirement_met": true},
    {"case": "demo_does_not_claim_url_independence", "origin_groups": 31, "publishers": ["Federal Reserve Bank of New York", "Federal Reserve Board"], "summable_claimed_independent_evidence": 0, "requirement_met": true},
    {"case": "nyfed_origin_url_matches_captured_endpoint", "actual": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json", "expected": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json", "requirement_met": true},
    {"case": "committed_demo_digest_positive", "passed": true, "digest": "eea9119c7b6275ae02c896584e2313f2eba487001f30541ed420624228f04805"},
    {"case": "all_rejected_dossier_freezes_extractor_schema_versions", "facts": 0, "versions_present": true, "requirement_met": true}
  ],
  "positive_controls_passed": true,
  "unmet_requirements": []
}
```
**Результат:** `unmet_requirements == []`, все 13 требований инкремента полностью закрыты.

### 2.4. Независимый реплей демонстрации (`demo_replay.py`)
```bash
PYTHIA_REVIEW_DEMO_ARTIFACT=docs/research/2026-10-18/r3/revision1/demo_dossier_bundle.json \
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 /tmp/demo_replay.py
```
**Вывод:**
```json
{
  "reviewed_commit": "62aa2301845f264d099412fbfe350081158f4846",
  "utc": "2026-10-18T19:03:08.398883+00:00",
  "facts": 35,
  "all_demo_proofs_valid": true,
  "bundle_digest": "eea9119c7b6275ae02c896584e2313f2eba487001f30541ed420624228f04805",
  "valid_proofs": 35,
  "invalid_proofs": 0,
  "source_proof_counts": {
    "fed_monetary_rss": {"total": 15, "valid": 15},
    "fed_speeches_rss": {"total": 15, "valid": 15},
    "nyfed_effr": {"total": 5, "valid": 5}
  },
  "raw_bom_sources": ["fed_monetary_rss", "fed_speeches_rss"],
  "identical_to_committed_demo": true,
  "original_signature_before": "35092882573e85265cd747671373a77f27848a647f852548b8aec5d25c2719f1",
  "original_signature_after": "35092882573e85265cd747671373a77f27848a647f852548b8aec5d25c2719f1",
  "network_attempts": 0,
  "original_unchanged": true
}
```
**Результат:**
- `all_demo_proofs_valid`: **true**
- `valid_proofs`: **35**
- `invalid_proofs`: **0**
- `identical_to_committed_demo`: **true**
- `original_unchanged`: **true** (сигнатура оригинального архива идентична до и после реплея)

---

## 3. Данные нового демонстрационного досье (Revision 1)

Новое досье сгенерировано безопасным скриптом [`docs/research/2026-10-18/r3/revision1/run_demo.py`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision1/run_demo.py) на временной копии архива:
- **Файл бандла:** [`docs/research/2026-10-18/r3/revision1/demo_dossier_bundle.json`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision1/demo_dossier_bundle.json)
- **Канонический SHA-256 дайджест:** `eea9119c7b6275ae02c896584e2313f2eba487001f30541ed420624228f04805`
- **Количество фактов:** 35
  - `nyfed_effr`: 5 фактов (ставки EFFR за 2026-10-08 — 2026-10-15, все 5 цитат проверены против CAS)
  - `fed_monetary_rss`: 15 фактов (пресс-релизы FOMC, все 15 цитат проверены против CAS с BOM)
  - `fed_speeches_rss`: 15 фактов (выступления руководства ФРС, все 15 цитат проверены против CAS с BOM)
- **Проверка доказательных срезов:** 35 из 35 proofs валидны (`valid_proofs = 35`, `invalid_proofs = 0`).
- **Группы происхождения:** 31 группа (1 серия EFFR, 30 статей ФРС). Поле `independent_evidence_count` отсутствует во всех группах; суммарное заявленное независимое свидетельство = 0.
- **Канонический URL NYFed:** `https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json`.
- **Замороженные версии:**
  - `extractor_version`: `"pythia-extractor-r3/0.2"`
  - `fact_schema_version`: `"pythia-fact-r3/1.1"`
  - `dossier_version`: `"pythia-dossier-r3/0.2"`

---

## 4. Неизменность живых архивов и производственного контура

Запуск `verify_archives.py` на сервере `amper` подтвердил:
- Все 4 живых архива (`live-r2-run1`, `live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`) строго read-only.
- Все 32 оригинальных CAS-блоба сохранены без изменений (`all_raw_intact: true`, `sqlite_quick_check: ok`).
- Хэши и идентификаторы ревизий в точности соответствуют отчёту R2 Revision 3.
- Производственный контейнер `pythia-v2` (`sha256:89576207...`, запущен 2026-08-29) не затрагивался.
- Ветки `ee6460f` (production), `c21967f` (paper), `b4c6d0f` (R2 baseline) не модифицировались.

---

## 5. Заключение

Инкремент **R3.0.1** полностью реализован на сервере `amper`:
- Все 7 групп дефектов F1–F7 устранены;
- Все 13 требований тестового сьюта архитектора закрыты;
- Все 35 доказательных срезов нового демонстрационного досье верифицируются против оригинальных блобов (35/35);
- Полный сьют из 208 тестов проходит успешно при нуле сетевых обращений;
- Исходные данные, живые архивы и смежные контуры сохранены в первозданном виде.

После фиксации коммита работа остановлена в ожидании ревью архитектора; R3.1 не начинался.
