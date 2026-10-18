# Pythia R3.0.2 — Отчёт о фиксации граничных контрактов первого инкремента (Revision 2)

**Дата:** 18 октября 2026 г.  
**Ветка:** `codex/evidence-r3-extraction`  
**Начальный коммит задания:** `5b6ec1d0c55d36c6473cb0c668af2a0c2bba7949`  
**Принятый baseline проекта:** R2 `b4c6d0f3de5ff0a63e0ecd55fdd45ecb69ca5354`  
**Worktree на amper:** `/home/pythia/pythia-evidence-r3`  
**Локальное зеркало (Mac):** `/Users/gerwer/Desktop/Pythia-evidence-r3`  
**Статус инкремента R3.0.2:** **IMPLEMENTED** (все 4 остаточные границы устранены, 9 дополнительных проверок пройдены, 13 проверок reviewer_controls пройдены, 213 тестов pytest пройдены, 35 из 35 proofs валидны, R3.1 не начинался)

---

## 1. Резюме четырёх граничных исправлений (R3.0.2)

В рамках ревизии R3.0.2 устранены четыре остаточные границы контрактов при сохранении всех ранее принятых исправлений F1–F7:

| Граница | Проблема в R3.0.1 | Реализация в R3.0.2 | Статус |
|---|---|---|---|
| **Остаточный F3** | При независимой проверке содержимого квитанция с замороженным статусом `blocked`, `unverified` или `invalid_content` могла ошибочно промоутироваться, если её контент валиден | Все условия допуска применяются строго одновременно через конъюнкцию (**AND**): 1) сохранённый диагностический статус `rec.status == "valid"` (квитанции с `blocked`, `unverified`, `invalid_content` безусловно отклоняются с 0 фактов и 1 rejection); 2) чистый транспорт `http_status == 200`; 3) совпадение начального URL с каноническим; 4) независимая валидация содержимого эталонным валидатором `SourceProbe.validate_content`. | **RESOLVED** |
| **Остаточный F4** | Проверка обязательности CAS-связки проверяла только `http_status == 200`. Квитанция с HTTP 403, будучи CAS-eligible по контракту R2, при потере ссылок не вызывала ошибку целостности; а частичная квитанция с сохранёнными ссылками не отбраковывалась | В `ReadOnlyArchiveReader` введена симметричная проверка CAS-связности для всех типов квитанций по контракту хранилища R2: `is_cas_eligible = rec.complete and not rec.limit_exceeded and rec.error_msg is None`. Если квитанция CAS-eligible, отсутствие `blob_sha256` или `revision_id` вызывает `ReaderIntegrityError` без публикации досье (`clean403_missing_links`). Если квитанция не CAS-eligible (partial, timeout, error), наличие ссылок на CAS-блоб или ревизию вызывает `ReaderIntegrityError` (`partial_retains_full_links`). Легитимные отрицательные квитанции без ссылок отклоняются штатно. | **RESOLVED** |
| **Остаточный F5** | Поле `provenance` имело дефолт `"fixture"`; синтетический `event_id` не проверялся на префикс; `deadline` и `rules` не проверялись на строгий строковый тип; статус контракта допускал произвольные значения | В `EventDescriptor` поле `provenance` сделано строго обязательным без значения по умолчанию (поддерживаются `"fixture"` и `"operator_supplied"`). Для `provenance == "fixture"` требуется явный синтетический идентификатор (`event_id.startswith(("fixture_", "fixture-"))`). Поле `deadline` допускает `None` или непустую строку ISO 8601 с обязательным явным смещением часового пояса (нестроковые типы, naive datetime отвергаются). Поле `rules` допускает `None` или непустую строку (словари, пустые строки отвергаются). В R3.0 `contract_status` строго ограничен значениями `"incomplete"` и `"unverified"` (статусы `"verified"` и `"complete"` отвергаются). | **RESOLVED** |
| **Остаточный F6** | Функция `_normalize_article_url` отсекала хвостовой слэш `/`, из-за чего различные URL статьи схлопывались в одну сущность происхождения | Нормализация URL сделана консервативной: путь (`parts.path`) и параметры запроса (`parts.query`) сохраняются посимвольно (verbatim). В нижний регистр переводятся только `scheme` и `netloc`. Различные URL (например, `.../monetary-fixture` и `.../monetary-fixture/`) формируют две раздельные сущности происхождения (с нулевой заявленной независимостью). | **RESOLVED** |

### Фиксация версий
В соответствии с требованиями инкремента версии обновлены:
- `EXTRACTOR_VERSION = "pythia-extractor-r3/0.3"`
- `FACT_SCHEMA_VERSION = "pythia-fact-r3/1.2"`
- `DOSSIER_VERSION = "pythia-dossier-r3/0.3"`

---

## 2. Результаты тестов и проверок на сервере `amper`

Все исполнения, проверки и сборка демонстрации производились исключительно на сервере `amper` под пользователем `pythia` в виртуальном окружении `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3`.

### 2.1. Дополнительные контроли контракта (`additional_controls.py`)
Скрипт проверяет все 4 остаточные границы F3–F6 в изолированных временных хранилищах при заблокированной сети:
```bash
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 /tmp/additional_controls.py
```
**Вывод:**
```json
{
  "reviewed_commit": "5b6ec1d0c55d36c6473cb0c668af2a0c2bba7949",
  "utc": "2026-10-18T19:27:10.261922+00:00",
  "network_attempts": 0,
  "controls": [
    {
      "case": "negative_stored_status_blocked",
      "stored_status": "blocked",
      "fact_count": 0,
      "rejection_count": 1,
      "requirement_met": true
    },
    {
      "case": "negative_stored_status_unverified",
      "stored_status": "unverified",
      "fact_count": 0,
      "rejection_count": 1,
      "requirement_met": true
    },
    {
      "case": "negative_stored_status_invalid_content",
      "stored_status": "invalid_content",
      "fact_count": 0,
      "rejection_count": 1,
      "requirement_met": true
    },
    {
      "case": "clean403_missing_links",
      "integrity_error": "Complete clean receipt 'clean403_missing_links' is missing mandatory CAS blob and revision linkage",
      "dossier_published": false,
      "requirement_met": true
    },
    {
      "case": "partial_retains_full_links",
      "integrity_error": "Incomplete/failed receipt 'partial_retains_full_links' unexpectedly references a blob or revision",
      "dossier_published": false,
      "requirement_met": true
    },
    {
      "case": "wrong_nullable_types",
      "published_descriptor": null,
      "requirement_met": true
    },
    {
      "case": "empty_rules_claim_verified",
      "published_descriptor": null,
      "requirement_met": true
    },
    {
      "case": "explicit_provenance_and_synthetic_fixture_identity",
      "published_descriptor": null,
      "requirement_met": true
    },
    {
      "case": "preserve_distinct_url_paths_with_trailing_slash",
      "receipt_statuses": ["valid", "valid"],
      "fact_count": 2,
      "origin_count": 2,
      "origin_urls": [
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary-fixture",
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary-fixture/"
      ],
      "requirement_met": true
    }
  ],
  "unmet_requirements": []
}
```
**Результат:** `unmet_requirements == []`, все 9 дополнительных проверок пройдены успешно. Сетевых запросов: 0.

### 2.2. Контроли независимого ревью (`reviewer_controls.py`)
Проверка на артефакте ревизии 2:
```bash
PYTHIA_REVIEW_DEMO_ARTIFACT=docs/research/2026-10-18/r3/revision2/demo_dossier_bundle.json \
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 /tmp/reviewer_controls.py
```
**Вывод:**
```json
{
  "reviewed_commit": "5b6ec1d0c55d36c6473cb0c668af2a0c2bba7949",
  "utc": "2026-10-18T19:31:32.636290+00:00",
  "network_attempts_blocked": 0,
  "controls": [
    {"case": "positive_fact_proof_digest_idempotence", "passed": true},
    {"case": "exact_decimal_without_float_rounding", "expected": "3.8800000000000001", "actual": "3.8800000000000001", "receipt_status": "valid", "requirement_met": true},
    {"case": "bom_rss_verbatim_proof", "receipt_status": "valid", "fact_count": 1, "proof_verifies": true, "requirement_met": true},
    {"case": "source_identity_not_just_stored_valid_label", "reference_validator_status": "invalid_content", "fact_count": 0, "requirement_met": true},
    {"case": "unsupported_rss_version", "receipt_status": "valid", "fact_count": 0, "requirement_met": true},
    {"case": "clean_transport_gate_not_stored_valid_label", "http_status": 403, "stored_status": "valid", "fact_count": 0, "requirement_met": true},
    {"case": "complete_clean_receipt_missing_mandatory_blob", "detail": "Complete clean receipt 'receipt-missing-links' is missing mandatory CAS blob and revision linkage", "requirement_met": true},
    {"case": "read_only_uri_escaping", "valid_archive_read": true, "unexpected_sibling_created": false, "error": null, "requirement_met": true},
    {"case": "explicit_descriptor_validation", "invalid_descriptor_rejected": true, "requirement_met": true},
    {"case": "cli_requires_explicit_event_descriptor", "exit_code": 2, "published_default_event": false, "requirement_met": true},
    {"case": "atomic_no_clobber_publication", "collision_rejected": true, "competing_bytes_preserved": true, "requirement_met": true},
    {"case": "demo_does_not_claim_url_independence", "origin_groups": 31, "publishers": ["Federal Reserve Bank of New York", "Federal Reserve Board"], "summable_claimed_independent_evidence": 0, "requirement_met": true},
    {"case": "nyfed_origin_url_matches_captured_endpoint", "actual": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json", "expected": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json", "requirement_met": true},
    {"case": "committed_demo_digest_positive", "passed": true, "digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde"},
    {"case": "all_rejected_dossier_freezes_extractor_schema_versions", "facts": 0, "versions_present": true, "requirement_met": true}
  ],
  "positive_controls_passed": true,
  "unmet_requirements": []
}
```
**Результат:** `unmet_requirements == []`, все 13 проверок и 2 позитивных контроля успешно пройдены. Сетевых попыток: 0.

### 2.3. Независимый реплей демонстрации (`demo_replay.py`)
```bash
PYTHIA_REVIEW_DEMO_ARTIFACT=docs/research/2026-10-18/r3/revision2/demo_dossier_bundle.json \
PYTHONPATH=. /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 /tmp/demo_replay.py
```
**Вывод:**
```json
{
  "reviewed_commit": "5b6ec1d0c55d36c6473cb0c668af2a0c2bba7949",
  "utc": "2026-10-18T19:31:12.346764+00:00",
  "facts": 35,
  "all_demo_proofs_valid": true,
  "bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
  "valid_proofs": 35,
  "invalid_proofs": 0,
  "source_proof_counts": {
    "fed_monetary_rss": {
      "total": 15,
      "valid": 15
    },
    "fed_speeches_rss": {
      "total": 15,
      "valid": 15
    },
    "nyfed_effr": {
      "total": 5,
      "valid": 5
    }
  },
  "raw_bom_sources": [
    "fed_monetary_rss",
    "fed_speeches_rss"
  ],
  "identical_to_committed_demo": true,
  "original_signature_before": "35092882573e85265cd747671373a77f27848a647f852548b8aec5d25c2719f1",
  "original_signature_after": "35092882573e85265cd747671373a77f27848a647f852548b8aec5d25c2719f1",
  "network_attempts": 0,
  "original_unchanged": true
}
```
**Результат:**
- `all_demo_proofs_valid`: **true**
- `valid_proofs`: **35 / 35** (100%)
- `identical_to_committed_demo`: **true**
- `original_unchanged`: **true**
- Сетевых запросов: **0**

### 2.4. Регрессионные тесты (`test_event_dossier.py`)
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/test_event_dossier.py -v
```
**Результат:** `26 passed in 1.55s` (exit code 0).
Все 26 модульных тестов, включая 5 новых тестов на остаточные границы F3–F6, пройдены успешно:
- `test_r302_negative_stored_status_diagnostic` — PASSED
- `test_r302_clean403_missing_links` — PASSED
- `test_r302_partial_retains_full_links` — PASSED
- `test_r302_bad_descriptors` — PASSED
- `test_r302_preserve_distinct_url_paths_with_trailing_slash` — PASSED

### 2.5. Полный тестовый сьют Pytest
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
**Результат:** `213 passed in 12.96s` (exit code 0).  
Лог полного прогона сохранён в [`docs/research/2026-10-18/r3/revision2/test_run.log`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision2/test_run.log). Сетевых запросов: 0.

### 2.6. Проверка неприкосновенности живых архивов (`verify_archives.py`)
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 /tmp/verify_archives.py
```
**Результат:**
- Все 32 блоба в 4 живых архивах (`live-r2-run1`, `live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`) **100% интактны** (`all_raw_intact: true`, `sqlite_quick_check: "ok"`).
- Production commit (`ee6460f`), paper parent (`c21967f`), container `pythia-v2` — не затронуты.

---

## 3. Сводка демонстрации Revision 2

| Параметр | Значение |
|---|---|
| **Артефакт демонстрации** | [`docs/research/2026-10-18/r3/revision2/demo_dossier_bundle.json`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision2/demo_dossier_bundle.json) |
| **Сводка демонстрации** | [`docs/research/2026-10-18/r3/revision2/demo_summary.json`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision2/demo_summary.json) |
| **Скрипт демонстрации** | [`docs/research/2026-10-18/r3/revision2/run_demo.py`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/revision2/run_demo.py) |
| **Bundle Digest** | `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` |
| **Всего фактов** | 35 |
| **Валидных цитат (proofs)** | **35 из 35** (100%) |
| **Групп происхождения** | 31 (независимость не заявляется) |
| **Источники с BOM** | `fed_monetary_rss`, `fed_speeches_rss` (все цитаты точны с учётом префикса) |
| **Затрагивание живых архивов** | 0 байт изменено |
| **Сетевые обращения** | 0 попыток (socket guard активен) |

---

## 4. Границы инкремента и отказ от раннего R3.1

В строгом соответствии с контрактом задачи:
- Реализованы **только четыре границы R3.0.2** (F3-диагностика, F4-CAS-связность для всех квитанций, F5-дескриптор и строгие типы, F6-сохранение хвостового слэша).
- Сохранены все закрытые дефекты R3.0.1 (F1 Decimal, F2 BOM quotes, F7 no-clobber, экранирование SQLite URI).
- Живые архивы, production-окружение и ревизии R2 не модифицировались.
- Инкремент R3.1 не начинался. Работа остановлена до ревью архитектора.
