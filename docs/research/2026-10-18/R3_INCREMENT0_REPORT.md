# Pythia R3.0 — Отчёт об исполнении: офлайн-факты и замороженное контекстное досье

**Дата:** 18 октября 2026 г.  
**Ветка:** `codex/evidence-r3-extraction`  
**Базовый коммит (принятый R2 baseline):** `b4c6d0f3de5ff0a63e0ecd55fdd45ecb69ca5354`  
**Серверный worktree:** `/home/pythia/pythia-evidence-r3` на `amper`  
**Локальное зеркало:** `/Users/gerwer/Desktop/Pythia-evidence-r3`  
**Статус инкремента R3.0:** **IMPLEMENTED** (все требования инкремента R3.0 закрыты, R3.1/JEV/прогнозы не начинались)

---

## 1. Краткое резюме

В рамках первого инкремента **R3.0** реализован механизм офлайн-извлечения структурированных фактов и формирования неизменяемых контекстных досье событий по сохранённым оригинальным документам архива:
- **`python/evaluation/evidence_reader.py` (`ReadOnlyArchiveReader`):**
  - Строгое подключение к существующей базе `archive.db` через SQLite `mode=ro` (без DDL, без `mkdir`, без модифицирующих PRAGMA и без `immutable=1` для корректного чтения WAL-снапшотов).
  - Проверка версии схемы (`pythia-archive-r2/2.0`) и целостности через `PRAGMA quick_check;`.
  - Обязательный числовой срез `as_of_seq` (целое `>= 0`, не `bool`) и явный список `receipt_id`. Детерминированная сортировка и дедупликация входных идентификаторов.
  - Полная перекрёстная проверка связей: `receipt` → `revision` → `source_id`/`initial_url`/`blob_sha256`/`byte_size` и условие `first_seen_seq <= as_of_seq`.
  - Независимая верификация CAS-блоба на диске: проверка существования файла, совпадения размера и контрольной суммы SHA-256.
  - Честная обработка легитимных отрицательных квитанций (`blob_sha256 is None` и `revision_id is None`) как отклоненных попыток без ошибки отсутствующего блоба.
  - Любое нарушение целостности (неизвестный receipt, выход за cutoff, разорванная связь, повреждённый блоб) прерывает формирование досье и возвращает явную ошибку без создания артефактов.
- **`python/evaluation/evidence_extract.py`:**
  - Детерминированное извлечение фактов для трёх поддерживаемых источников R3.0:
    1. `nyfed_effr`: строгий JSON, запрет дубликатов ключей, запрет `NaN`/`Infinity`/`bool`, валидация календарной даты `effectiveDate` (точность `day`), точное строковое десятичное число `rate`, единица измерения `percent`, `published_at=None` с обоснованием отсутствия поля в API, эмитент `Federal Reserve Bank of New York`.
    2. `fed_monetary_rss` и `fed_speeches_rss`: разбор структуры RSS 2.0 с поддержкой BOM, сохранение описания/CDATA как `feed_excerpt`, пометка связанной статьи как `referenced_not_fetched`, строгая нормализация времени в UTC ISO-8601 только при доказанном часовом поясе RFC 2822 (иначе `published_at=None` с причиной), эмитент `Federal Reserve Board`.
  - Доказательные срезы (`ProofSpan`): точные байтовые границы в оригинальном CAS-блобе, хэш цитаты, исходный хэш блоба и структурные локаторы (`JSONPointer` / `XPath`). Исходные байты строго отделены от распарсенных значений.
  - Роль всех фактов зафиксирована как `background/context`, `usable_for_forecast=False`.
  - Манифест отклонений (`RejectionRecord`) для неподдерживаемых, заблокированных (HTTP 403) или отрицательных квитанций.
- **`python/evaluation/event_dossier.py` (`build_event_dossier`):**
  - Дескриптор события (`EventDescriptor`) с явным указанием `contract_status="incomplete"`, `rules=None`, происхождением `fixture` (без выдумывания правил Polymarket).
  - Группировка происхождения (`build_origin_groups`): единый URL статьи ФРС, встречающийся в разных лентах или снапшотах, группируется в один origin ФРС с несколькими наблюдениями; количество URL не выдаётся за независимые доказательства.
  - Строгая изоляция выходного каталога: `output_dir` физически и логически отделён от сырого архива `state_dir`.
  - Атомарная публикация канонического бандла `dossier_<digest>.json` с защитой от перезаписи и проверкой совпадения байт при идемпотентных повторных вызовах. Канонический дайджест не содержит абсолютных путей, системного времени и порядка аргументов CLI.

---

## 2. Результаты тестов на сервере `amper`

### 2.1. Изоляция сети и модульные тесты R3.0
В `python/evaluation/tests/test_event_dossier.py` реализован строгий сетевой барьер на уровне сокетов (`socket.socket.connect` блокируется фикстурой `guard_no_network`), исключающий любые внешние сетевые вызовы.

Проверены 13 специализированных тестов:
1. `test_nyfed_effr_multiple_dates_and_exact_decimals` — множественные даты, строгие десятичные строки, единица `percent`.
2. `test_nyfed_effr_rejection_of_duplicate_keys` — отклонение дубликатов ключей в JSON.
3. `test_nyfed_effr_rejection_of_bool_nan_infinity_and_bad_calendar_dates` — отклонение `bool`, `NaN`, `Infinity`, `2026-02-30`, игнорирование других серий (`OBFR`).
4. `test_fed_rss_cdata_entities_and_proven_timezone_pubdate` — разбор CDATA, HTML-сущностей, нормализация даты с часовым поясом в UTC ISO-8601.
5. `test_fed_rss_zoneless_or_missing_date_yields_null_published_at` — корректный `published_at=None` при отсутствии часового пояса или даты.
6. `test_blocked_partial_and_unsupported_sources_recorded_in_rejections` — квитанции 403, отрицательные и неподдерживаемые источники попадают в манифест отклонений без создания фактов.
7. `test_missing_or_corrupt_blob_and_linkage_error_aborts_dossier` — повреждение или отсутствие блоба вызывает `ReaderIntegrityError` и запрещает публикацию досье.
8. `test_receipt_after_cutoff_aborts_entire_input` — квитанция с `seq > as_of_seq` вызывает `CutoffViolationError`.
9. `test_missing_archive_directory_does_not_create_files` — отсутствие каталога архива вызывает `ArchiveNotFoundError` без создания файлов или папок.
10. `test_origin_lineage_same_article_in_multiple_feeds_groups_together` — единый URL статьи в нескольких лентах/снапшотах агрегируется в единый origin с несколькими наблюдениями (`independent_evidence_count = 1`).
11. `test_a_b_a_revisions_preserve_provenance` — последовательность ревизий A → B → A сохраняет корректную provenance-связь с CAS-блобами.
12. `test_idempotent_bundle_digest_and_permutation_invariance` — перестановка и дублирование receipt ID возвращают тождественный дайджест и не модифицируют исходный архив.
13. `test_output_dir_cannot_be_inside_archive_dir` — запрет размещения `output_dir` внутри `archive_dir`.

**Результат прогона:** `13 passed in 1.08s`.

### 2.2. Полный тестовый сьют Pytest
Выполнен на сервере `amper`:
```bash
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
- **Результат:** `200 passed in 12.76s` (exit code 0).
- Все прежние 187 тестов (R1 + R2) полностью сохранены и успешно проходят.
- Лог сохранён в [`docs/research/2026-10-18/r3/increment0/test_run.log`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/increment0/test_run.log).

---

## 3. Офлайн-демонстрация на согласованной копии `live-r2-revision3`

Демонстрационный скрипт [`docs/research/2026-10-18/r3/increment0/run_demo.py`](file:///home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/increment0/run_demo.py) выполнен на сервере `amper`:

1. **Проверка 4 живых архивов ДО демонстрации:**
   - Проверены 32 CAS-блоба в `live-r2-run1`, `live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`.
   - Результат: `total_blobs = 32`, `healthy = True`.
2. **Создание WAL-safe копии:**
   - Выполнено резервное копирование SQLite через `ro_conn.backup(dst_conn)` из read-only подключения.
   - Скопированы блобы в изолированный каталог снапшота.
3. **Механический выбор квитанций:**
   - Из снапшота выбраны 3 целевые квитанции при срезе `as_of_seq = 8`:
     - `c5a86df9-f433-4090-a6a0-af11f2d18643` (seq 1, `nyfed_effr`)
     - `39bf5ee5-5918-489f-b9e1-dfcfcfbce96b` (seq 2, `fed_monetary_rss`)
     - `65207891-9bef-4a74-af54-abe32ddf58ae` (seq 3, `fed_speeches_rss`)
4. **Дескриптор события:**
   - `event_id`: `"fixture_fomc_202610_context"`
   - `question`: `"Will the Federal Reserve adjust the target federal funds rate range at the October/November 2026 FOMC meeting?"`
   - `contract_status`: `"incomplete"`
   - `rules`: `None`
   - `provenance`: `"fixture"`
5. **Формирование досье и публикация:**
   - Бандл опубликован: `dossier_0a3b8e58777a9cfffe21eee05da8e455b80c7778d419633d4ff205c7148e565b.json`.
   - Канонический дайджест: `0a3b8e58777a9cfffe21eee05da8e455b80c7778d419633d4ff205c7148e565b`.
   - Извлечено фактов: 35 (5 наблюдений EFFR за разные даты, 15 пресс-релизов ФРС по монетарной политике, 15 выступлений руководства ФРС).
   - Сформировано origin-групп: 31 (1 серия ставок NYFed с 5 наблюдениями, 15 уникальных статей монетарной политики, 15 уникальных статей выступлений; для каждой статьи `article_fetched = False`, `independent_evidence_count = 1`).
   - Метаданные досье: `dossier_type = "context_only"`, `evidence_status = "insufficient_direct_evidence"`, `usable_for_forecast = False`.
6. **Проверка 4 живых архивов ПОСЛЕ демонстрации:**
   - Повторная проверка 32 CAS-блобов через `mode=ro`:
   - Результат: `total_blobs = 32`, `healthy = True` (исходные архивы полностью неизменны).

---

## 4. Сохранность внешнего окружения и продакшена

1. **Коммиты и ветки:**
   - Production коммит `ee6460f31149917e29463ebb6f54c1d80e6c4562` и родительский `c21967f651eae899ccad1da29d2c9aa6133875cb` не затрагивались.
   - Базовая ветка R2 `codex/evidence-r2-archive` на коммите `b4c6d0f` не модифицировалась.
   - Новая ветка `codex/evidence-r3-extraction` создана в отдельном изолированном worktree `/home/pythia/pythia-evidence-r3`.
2. **Сервисы и контейнеры:**
   - Контейнер `pythia-v2` (`sha256:89576207...`, StartedAt 2026-08-29) работает непрерывно.
   - Telegram, торговые сделки, внешние API и Cron не изменялись.
   - Новых сетевых обращений не выполнялось (все операции офлайн).

---

## 5. Артефакты инкремента R3.0

В каталоге `docs/research/2026-10-18/r3/increment0/`:
1. `run_demo.py` — исполняемый скрипт офлайн-демонстрации и сквозной проверки 32 блобов.
2. `demo_output.log` — полный текстовый вывод демонстрационного прогона.
3. `demo_summary.json` — структурированная сводка параметров досье и дайджеста.
4. `demo_dossier_bundle.json` — канонический бандл опубликованного контекстного досье (размер ~413 КБ).
5. `test_run.log` — полный лог прогона 200 тестов сьюта Pytest.

---

## 6. Границы исполнения и остановка

- **Инкремент R3.0 выполнен полностью.**
- **К этапам R3.1 (краулинг полных статей, извлечение HTML), JEV/LLM-валидаторам, вероятностным моделям и подключению к каналу самостоятельно не приступаем.**
- Работа остановлена до ревью ведущего архитектора.
