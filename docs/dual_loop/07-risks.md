# 07 — Risk Assessment

## 7.1. Языковые / стековые границы

| # | Риск | Вероятность | Impact | Паттерн решения |
|---|------|-------------|--------|-----------------|
| R1 | **Schema drift** между Node и Rust (разные ожидания полей) | High | High | Proto-схемы как single source of truth; `buf breaking` в CI; versioned topics (`watchlist.v1`, `v2` параллельно); contract tests в обоих стеках (generated clients + golden fixtures). |
| R2 | **JS event-loop stalls** (GC, sync fs, heavy JSON.parse) блокируют publisher | Medium | Medium | Publisher выносится в `worker_threads`; bound queue с drop-oldest при overflow; health probe на event-loop lag через `perf_hooks.monitorEventLoopDelay`; алерт при p99 > 50ms. |
| R3 | **Несогласованность таймзон / монотонных часов** между Node и Rust | Medium | High | Все события стэмпятся монотонным временем (`hrtime.bigint()` / `Instant::now()`) + wall-clock UTC для correlation; `chrony` с PPS-источником на prod-хостах; latency budget явно включает clock-skew < 1ms. |
| R4 | **Serialization overhead** JSON в hot path | High | Medium | Hot path — только protobuf + zero-copy (`prost` + `bytes`). JSON разрешён исключительно для UI и debug. CI-benchmark `bus_roundtrip_p99_us` с регрессионным gate'ом. |

## 7.2. Latency / надежность

| # | Риск | Паттерн |
|---|------|---------|
| R5 | **Mempool stream stall** (нода отстала, peer-count упал) | Dual-node setup (primary Erigon + secondary Bor), heartbeat < 200ms, auto-failover. `mempool_tps` экспортируется в UI — оператор видит deviation от baseline. |
| R6 | **Нерепрезентативный mempool** (private tx пропускают public) | Интеграция с несколькими private relays (bloXroute subscription), сверка coverage offline, алерт при падении coverage ratio > 10% WoW. |
| R7 | **RPC broadcast timeout / tx stuck** | Pre-signed RBF bump-ladder (1.1x, 1.25x, 1.5x gas), max 3 попытки, затем cancel-tx (same nonce, 0 value, high gas). |
| R8 | **GC/allocator pauses в Rust** | Запрет на `Box::new` в hot path (lint `clippy::disallowed_types`); arena allocators (`bumpalo`) для per-trigger буферов; проверка через `dhat` + `criterion` в CI. |
| R9 | **Redis latency spike** в control plane | Redis sentinel (3 nodes) + local in-memory cache watchlist в Sniper; bus-outage не должен ронять executor — он работает на last-known watchlist до TTL; graceful degradation индицируется в UI. |

## 7.3. Гонки данных и consistency

| # | Риск | Паттерн |
|---|------|---------|
| R10 | **Stale watchlist**: Pythia сняла рынок, Sniper ещё исполняет | ✅ **Mitigated (Phase 2 Track C).** Каждый entry имеет `valid_until` (TTL) + `version`. `PreTradeChecks.evaluate()` проверяет TTL + stale signal (>60s) ДО broadcast; просроченный entry → auto-disarm + `RiskError::WatchlistExpired` / `RiskError::StaleSignal`. `dl.v1.watchlist.remove` — идемпотентно, с `reason`. |
| R11 | **Double-fire trigger** (тот же оракул матчится дважды) | Idempotency key = `hash(market_id \|\| tx_hash \|\| log_index)`, дедуп в executor LRU cache (size=10k). |
| R12 | **Race Accept ↔ auto-expire** | Signal имеет `server_version`. Decision включает `expected_version`; conflict → 409 + UI показывает новую версию. |
| R13 | **Nonce race** при параллельных сделках одного ключа | Single-writer NonceManager (actor model, tokio mpsc channel), reservation pool, на timeout — automatic release + alert. |
| R14 | **Split-brain на kill-switch** (UI считает killed, executor ещё нет) | ✅ **Mitigated (foundation path landed).** Kill = two-phase: gateway публикует `dl.v1.control.kill`, executor валидирует HMAC/staleness/replay, включает scoped kill state и отвечает через `dl.v1.control.kill_ack`. UI держит промежуточное состояние до ack. В paper-mode heartbeat/state projection пока coarse на уровне всего executor'а, а не per-market. Дополнительно: `DeadManSwitch` блокирует все новые сделки если operator heartbeat не получен >5 мин (`dl.v1.control.heartbeat`). |

## 7.4. Operational / security

| # | Риск | Паттерн |
|---|------|---------|
| R15 | **Компрометация hot-wallet ключа** | ✅ **Partially mitigated (Phase 2 Track C).** HSM-signing (YubiHSM 2 / Fireblocks); per-session ephemeral approval; hard cap по суточному notional в контракте-мультисиге; ключ не покидает HSM. Дополнительно: `DailyBudget` ($100k default cap) блокирует сделки при превышении суточного лимита потерь (gas + realized PnL). State persisted в Redis. |
| R16 | **Ошибочный Kill оператором** | Two-key confirm + 3s abort countdown + post-mortem log. Кнопка физически разнесена с остальными (нижний-правый угол). Audit log запись включает hash state системы до/после. |
| R17 | **LLM hallucination → ложный Edge** | Multi-source grounding (Exa + on-chain history + Polymarket comments); JSON-schema валидация вывода; confidence < threshold → сигнал помечается LOW_CONF и не попадает в Active Watchlist автоматически, только в Triage Queue. |
| R18 | **Oracle manipulation / flash news** | Минимум 2 независимых источника подтверждения для авто-триггера; при одиночном источнике — only alert, no auto-exec; оператор видит в UI, какие источники подтвердили. |
| R19 | **Supply-chain атака на зависимости** | `cargo-deny`, `npm audit --production`, pinned lockfiles (`Cargo.lock`, `bun.lockb`); private registry mirror; reproducible builds (`--locked`); SBOM в release artifacts. |

## 7.5. Ключевые инварианты системы (assertions в коде)

Эти инварианты проверяются runtime-assertions и exported в metrics; при нарушении — paging alert и auto-DRAIN.

1. `kill(scope=GLOBAL or NEW_TRIGGERS) ⇒ no new trigger execution`; `kill(scope=MARKET) ⇒ matching market blocked`.
2. `∀ trigger fired ⇒ ∃ watchlist entry with valid TTL`.
3. `∀ tx broadcast ⇒ nonce ∈ reserved_set AND size ≤ position_cap`.
4. `bus_lag(any_topic) > 500 ⇒ auto_drain()`.
5. `operator_last_heartbeat > 30s ⇒ no new triggers armed` (dead-man switch для hands-off сессий).
6. `signed_tx.from = hot_wallet_address` (проверка целостности HSM pipeline).
7. `daily_spent_gas + daily_spent_notional ≤ daily_budget`.

Каждый инвариант имеет unit-test + integration-test + chaos-test сценарий.

## 7.5b. Phase 1 — Track D mitigation notes (data divergence SQLite ↔ Postgres)

Phase 1 Track D (shadow-write миграция) вводит параллельный PG store. Это
отдельный класс "data divergence" риска поверх общего R13 (nonce race) —
ниже фиксируем, как он парируется до Phase 2 cutover. Детали и runbook —
в [db_migration.md](./db_migration.md).

| Подриск | Паттерн |
|---------|---------|
| **Shadow error kills primary write** | `db_shadow.ts` использует `Promise.allSettled` + `queueMicrotask` + fire-and-forget; primary commit уже завершён к моменту первого обращения к PG. CI-тест `db_shadow.test.ts` явно проверяет, что `insertPrediction` не бросает даже при исключении в shadow. |
| **Silent divergence в scalar полях** | Cron `bun run db:parity` раз в час: 100 случайных строк `predictions`, diff по `market_price / dexter_p / naked_p / outcome / is_valid / api_cost_usd`. Weekly full-scan через `bun run db:parity -- --full`. Каждая дивергенция → `PARITY DIVERGE` line + counter `parity_diverge_total`. |
| **PG offline во время shadow-window** | `pingPg()` на старте + `shadow_pg_errors_total{operation=ping}`; при падении PG primary loop не страдает (SQLite primary), ops видит счётчик в Prometheus. Runbook: отключить флаг, поднять PG, запустить `pg_initial_load.ts` чтобы восстановить потерянные строки. |
| **Schema drift** между `src/db/pg/schema.ts` и миграциями | CI job `shadow-pg-tests` запускает `drizzle-kit generate` и fail'ит build если `sql/migrations/` diff'ится. |
| **Python-side deferral** | Полноценный Python shadow отложен; минимальный `python/db_pg_shadow.py` покрывает только `update_outcome` (resolution-checker). Обоснование — в [db_migration.md → Python side scope](./db_migration.md#python-side-scope). Риск принят: parity проверяется по итоговым значениям в таблицах независимо от того, какой стек их произвёл. |
| **Cutover без 7-дневной тишины** | DoD для Phase 2: `shadow_pg_errors_total{operation=insertPrediction or updateOutcome} == 0` **и** `parity_diverge_total == 0` **и** `parity_missing_*_total == 0` 7 дней подряд. |

## 7.6. Пре-mortem матрица "если упадёт X, что делать"

| Что упало | Auto-response | Оператор видит | Manual action |
|-----------|---------------|----------------|---------------|
| Polygon node primary | Failover на secondary < 5s | Warning toast + `node: DEGRADED` в status bar | Проверить primary логи |
| Redis | Pythia пауза paper-writes, Sniper работает на last-known watchlist до TTL expire | Red pulsing border на status bar | Рестарт Redis; если не поднимается — Kill Switch |
| NATS | Telemetry теряется, но control plane работает | UI показывает `OB: stale` | Рестарт NATS |
| Gateway-api | WSS disconnect, UI goes degraded | Full-screen reconnect modal | Рестарт gateway |
| Sniper-ingest | Не выдаёт triggers, executor idle | `trigger_rate = 0` alert | Рестарт ingest; если повторно — investigate ноду |
| Sniper-executor | Triggers теряются | `trigger_queue_depth > 10` alert | Кill Switch; fix executor offline |
| HSM | Подпись неудачна → executor → DRAIN | `hsm: ERROR` red | On-call unlocks HSM |
| LLM API | Новые signals не поступают | `pythia_signal_rate < baseline * 0.5` | Проверить API quota; fallback на кэш |
| Операторский UI | Operator не может действовать | Telegram fallback alerts активируются | Backup operator заходит через Telegram |
