# Pythia R3.1.9 — Закрытие нарушений N1–N2 и новое ограниченное research-получение

**Вердикт исполнителя:** **IMPLEMENTED** (с сохранением честного статуса **`PARTIAL`** для исторического acquisition)
**Родительский коммит (исходная точка):** `6280f6efe83657179685773d14422476e05b7a10` (R3.1.8)
**Accepted development baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` (не менялся)
**Ветка:** `codex/evidence-r31-statements`
**Основание:** независимое ревью R3.1.8 от 22.10.2026 (`project/pythia/reviews/2026-10-22/r318-6280f6e/REVIEW_RU.md` в MemPalace) — вердикт CHANGES_REQUIRED по двум остаточным нарушениям N1 [P2] и N2 [P3]; поименованные T1–T3 тем ревью подтверждены закрытыми.

---

## 1. Сводка исправлений (N1–N2)

1. **N1 (Байты потока после конца декодера больше не теряются):**
   - `decode_http_stream()` переработан: после достижения конца члена (`decompressor.eof`) остаток потока из `decompressor.unused_data` подаётся в свежий декомпрессор того же кодирования, а не отбрасывается. Конкатенированные члены gzip декодируются как одно непрерывное тело (RFC 1952) — как это делают стандартные клиенты (urllib3/requests, стек принятого R2).
   - Хвост потока, не образующий валидный следующий член (мусорные байты), отклоняется с `TransportDecodeError`; захват не публикуется.
   - `eof_complete` теперь означает: декодер дошёл до EOF **и** не осталось непотреблённых байтов потока; в конце добавлена явная проверка отсутствия `unused_data`.
   - Лимит декодированного тела (2 MiB) применяется к сумме всех членов, а не к каждому по отдельности.
2. **N2 (`proven_conditions` только по наблюдениям):**
   - `cookie_isolation_on_each_hop` вычисляется по фактически наблюдаемым hops всех попыток и равен `True` только при хотя бы одном наблюдаемом hop и чистоте каждого. Прогон, где все воркеры убиты (ноль наблюдаемых hops), теперь честно записывает `False`.

Парсер, продуктовый код, revision1–8 и исторический PARTIAL не тронуты. Изменения — только новый standalone-драйвер `pythia-bounded-research-driver-r319/1.0` и его hermetic-сьют в директории revision9.

## 2. Результаты тестов и оффлайн-демонстрации

### 2.1 Герметичный сьют драйвера (`test_bounded_research_driver.py`) — 17 passed
13 унаследованных тестов T1–T3/supervisor/URL-gate плюс 4 новых:
- `test_gzip_multi_member_fully_decoded` — **PASSED** (N1: два члена → опубликовано полное конкатенированное тело)
- `test_gzip_trailing_garbage_rejected` — **PASSED** (N1: валидный член + мусорный хвост → отказ, файл не записан)
- `test_gzip_multi_member_combined_limit` — **PASSED** (N1: лимит по сумме членов)
- `test_kill_only_audit_no_vacuous_proofs` — **PASSED** (N2: kill-only прогон → `cookie_isolation_on_each_hop is False`)

Лог: `acquisition/driver_test_run.log` (17 passed in 10.87s). Независимые ревьюерские контроли C2/C3/C9, воспроизводившие N1–N2 на ревизии 8, прогнаны против нового драйвера: C2 публикует 328/328 байт, C3 — отказ, C9 — `cookie_isolation_on_each_hop: false`.

### 2.2 Оффлайн демонстрация (`run_demo.py`) — 100% PASS, zero network
- Все 32 CAS-блоба в 4 live-архивах проверены до и после: **100% intact**, подписи архивов не изменились.
- Дайджест принятого parent dossier bundle `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` подтверждён.
- Все 21 ProofSpan для 2 реальных заявлений подтверждены байт-в-байт неизменным парсером.
- Остаточные контроли F1–F6 — **PASS**. Итог: `demo_summary.json`.

### 2.3 Регрессия
- Strict statement suite внутри полного: зелёный.
- Полный evaluation suite: **256 passed in 11.29s** (`test_run.log`). Регрессий нет.

## 3. Единственный live capture run (Revision 9)

- **Драйвер:** `pythia-bounded-research-driver-r319/1.0`
- **Run ID:** `run-20261022-135130-2858417` (2026-10-22T13:51:30Z, общее время 731.9 мс)
- **Attempt 1 (`monetary20260916a`):** HTTP 200, 84 741 байт, SHA-256 `11440dfe75fa9ce0d06dd3be1bc4be10689ac4b99750cf70868b12f232b13a1a`, `eof_complete: true`, 224.5 мс.
- **Attempt 2 (`monetary20260729a`):** HTTP 200, 83 318 байт, SHA-256 `3048b8b787b2df598d496eb0e661136b2fef9f035f62eca50d74e8cdd99ba0b5`, `eof_complete: true`, 210.4 мс.
- **Бюджет:** 2 попытки из 4, 2 hops из 12, 0 killed workers. `cookie_isolation_on_each_hop: true` — по двум фактически наблюдаемым hops.
- **Экстракция неизменным парсером:** 6 и 7 абзацев; ProofSpans 10/10 и 11/11; bundle digests `a5e650b7191fae4cd2bef0ffc62a15b2c9b960248dc81e1873e063bcb9c0d3cf` и `207279edad56df99967461e4bcc31e929a0a2f0847109f40ffdfaf8f2f19838a`.
- **Сравнение с R8:** размеры равны (дельта 0); основные абзацы (0–3 и 0–4) побайтно идентичны (равенство quote-SHA с бандлами R8); различия — только ротирующиеся токены Cloudflare (Zaraz nonce, email-protection, footer), как зафиксировано в companion notes и в `comparison_with_prior_revision8` пакета.

## 4. Разделение статусов

| Параметр | Fresh Transport (Revision 9) | Submitted Revision 8 | Исторический рекорд (19.10) |
| :--- | :--- | :--- | :--- |
| **Статус верификации** | `VERIFIED_BOUNDED_RESEARCH` | `CHANGES_REQUIRED` (N1–N2) | `PARTIAL` |
| **Декомпрессия и EOF** | Доказан, включая полный учёт байтов после EOF члена | EOF доказан; байты после EOF терялись (N1) | Не исследовано |
| **Публикация файлов** | Атомарная + pre-flight fail-closed | Атомарная + pre-flight fail-closed | Прямая запись |
| **Достоверность аудита** | Неизвестное как `None`; proven — только по наблюдениям | `None` и реальные заголовки; вакуумная изоляция кук (N2) | Неполный аудит |
| **Usable For Forecast** | `False` | `False` | `False` |
| **Класс провенанса** | `research_fixture` | `research_fixture` | `research_fixture` |

> **Принцип честности архива:** Свежая успешная загрузка не переписывает задним числом исторический факт отсутствия контролей 19.10.2026. Исторический статус остаётся строго **`PARTIAL`**.

## 5. Созданные артефакты

Все артефакты размещены строго в директории ревизии 9:
- `docs/research/2026-10-22/R3_INCREMENT1_REVISION9_REPORT.md` (этот отчёт)
- `docs/research/2026-10-22/r3/increment1/revision9/`
  - `acquisition/`: `bounded_research_driver.py`, `test_bounded_research_driver.py`, `driver_test_run.log`, `acquisition_audit.json`, `acquisition_package.json`.
  - `fixtures/`: `raw_monetary20260916a.html`, `raw_monetary20260729a.html`, `manifest_monetary20260916a.json`, `manifest_monetary20260729a.json`, `fixtures_summary.json`.
  - `bundles/`: `statement_bundle_a5e650b7....json`, `statement_bundle_207279ed....json`.
  - `previews/`: `statement_preview_monetary20260916a.md`, `statement_preview_monetary20260916a_note.md`, `statement_preview_monetary20260729a.md`, `statement_preview_monetary20260729a_note.md`.
  - `run_demo.py`, `demo_summary.json`, `test_run.log`.

## 6. Границы и следующий шаг

- R3.1 не принят в одностороннем порядке настоящим отчётом; принятие фиксируется отдельным архитектурным вердиктом по этой ревизии.
- Accepted development baseline остаётся прежним до вердикта приёмки: `395d288a0ecb23829b0232debb29865a00e6ef38`.
- R3.2 не открывается до приёмки R3.1.
