# 06 — Roadmap & Phases

## Phase 0 — Foundations (2 недели) — **в работе**

**Цель:** подготовить общую инфраструктуру, не ломая существующую Pythia.

**Задачи:**
- [x] Architecture docs (`docs/dual_loop/`).
- [x] Cargo workspace + stub `services/sniper-*` (компилятся, но no-op).
- [x] Protobuf schemas (`schemas/dual_loop/v1/*.proto`) + `buf.yaml`.
- [x] `docker-compose.dev.yml` с Redis 7, NATS JetStream, Postgres 16, Prometheus, Grafana, Loki, Tempo.
- [x] CI skeleton (`.github/workflows/dual-loop-ci.yml`): `buf lint`, `buf breaking`, `cargo check`, `cargo fmt --check`, `cargo clippy`.
- [x] Makefile с `dev-up`, `dev-down`, `fmt`, `lint`, `test`, `proto-gen`.
- [ ] Миграция SQLite → PostgreSQL (schema-first, `drizzle-kit` миграции, shadow-write 7 дней) — **откладывается на Phase 1** (большой scope).
- [ ] Feature flags service — **откладывается на Phase 1**.

**DoD:**
- [x] `make dev-up` поднимает весь dev stack < 60s (Redis, NATS, Postgres, Prometheus, Grafana, Loki, Tempo).
- [x] Существующая Pythia продолжает работать без регрессий (не изменён `docker-compose.yml`, не изменён `src/**/*.ts`, не изменён `python/**/*.py`).
- [x] `cargo check` в `services/` зелёный.
- [x] `buf lint` на `schemas/` зелёный.
- [x] CI gate блокирует merge при нарушении lint/breaking.

## Phase 1 — Bus & Contracts (2 недели)

**Цель:** формализовать границу между контурами, запустить observability-loop данных без реального исполнения.

**Задачи:**
- Определить все protobuf-схемы (watchlist, signal, trigger, exec, control) в финальном виде с валидацией.
- Stub Rust-сервиса `sniper-ingest` — только consumer `watchlist.upsert`, логирование в structured log.
- Переписать Pythia signal publisher (`src/services/watchlist_publisher.ts`) в новый формат, dual-write в старый канал для parity.
- Gateway-API (Node/Fastify): WSS endpoint, auth (PASETO-токены), JSON-Patch стейт, bus bridge.
- Базовая версия Command Center (read-only): Triage Queue + Status Bar + Heatmap.
- Миграция SQLite → PostgreSQL (shadow-write, диффы пишутся в лог).
- Feature flags через Unleash или self-hosted.
- Dev ноутбук-setup: локальный Polygon mock (Anvil fork) для Phase 2.

**DoD:**
- Сигналы из Pythia появляются в Command Center < 500ms.
- Contract-тесты (schema-compat) зелёные в CI.
- Оператор может Accept/Reject (запись в `signals.decision`), но Sniper ещё не исполняет.
- Synthetic load test: 1k signals/min без drop.
- Shadow PG live 7 дней без дельт с SQLite на критических запросах.

## Phase 2 — Sniper MVP: Ingest & Paper Execution (3–4 недели)

**Цель:** Fast Loop видит мемпул, принимает решения, но исполняет только paper-trades.

**Задачи:**
- `sniper-ingest`: IPC-подписка на локальный Erigon/Bor, mempool firehose.
- ABI-декодер для CTF-контрактов и ключевых оракулов (UMA, Chainlink).
- `OrderBookTracker` с REST warm-up + WS incremental.
- `TriggerMatcher` + risk pre-checks (position caps, slippage, stale watchlist).
- `sniper-executor` в **paper mode**: строит tx, подписывает dev-ключом, но отправляет в `/dev/null` + пишет в `exec.paper`.
- Active Watchlist widget + Execution Panel (Kill disabled, но UI готов).
- Replay harness: детерминированный replay mempool-дампа для тестов.

**DoD:**
- End-to-end latency измерен: `mempool_seen → trigger_emitted` p95 < 8ms на staging-hardware.
- 72 часа непрерывного paper-run без memory leak (RSS drift < 5%).
- Backtest-harness: replay mempool-дампа даёт детерминированные triggers (byte-for-byte).
- Shadow-comparison: paper-fills vs реальное движение цены — статистика расхождений в weekly report.

## Phase 3 — Live Execution (Hardened) (3–4 недели)

**Цель:** выход в прод с реальным капиталом под строгим контролем.

**Задачи:**
- Hardware-backed signing (YubiHSM 2 или Fireblocks) для prod-ключа.
- Private relay integration (bloXroute / Merkle / Manifold).
- Per-market и global position caps, exposure circuit breaker.
- **Kill Switch** с двойным подтверждением, подписанный операторский token (HMAC).
- Канареечный rollout: лимиты $10 → $100 → $1k → $10k по рынкам.
- Полный Command Center: Execution Panel, Portfolio Panel, alert toasts.
- Runbooks: incident response, key rotation, node failover.

**DoD:**
- Ежедневный PnL reconciliation Pythia-paper vs live: расхождение < 2%.
- Chaos test: убивание RPC-ноды, Redis, gateway — система переходит в safe DRAIN < 1s.
- Kill Switch проверен в prod на пустой позиции: `arm → kill → all orders cancelled` < 500ms.
- SOC-lite: все критические действия audit-logged и immutable (append-only log + hash chain).
- Incident runbook прошёл table-top review.

## Phase 4 — Scale & Optimization (непрерывно, старт после Phase 3)

**Цель:** снижать latency, расширять покрытие.

**Задачи:**
- Kernel-bypass networking (DPDK / io_uring) для ingest на dedicated host.
- Co-location ноды и sniper-executor (same rack as validator peers).
- Multi-path RPC с racing (fastest-wins) к 2–3 private relays.
- LLM optimization: distillation DeepSeek → локальная модель 7B для faster re-scoring.
- Auto-widening/narrowing watchlist по realized hit-rate.
- A/B тестирование prompt-шаблонов Pythia.

**DoD:**
- p95 `trigger → broadcast` < 3ms.
- Охват: > 150 одновременных рынков в watchlist без деградации.
- Automated weekly model eval + rollback на regression.
- Latency budget monitoring: алерт при p99 > budget на 3 окна подряд.

## Критические пути (CPM)

```
Phase 0 ─┬─▶ Phase 1 ─┬─▶ Phase 2 ─▶ Phase 3 ─▶ Phase 4
         │            │
   (bus+infra)  (contracts+gateway)
         │            │
         └─▶ SQL→PG  (можно параллельно)
```

Параллелизуемые треки внутри Phase 1:
- **Backend:** gateway-api + bus bridge.
- **Frontend:** Command Center readonly skeleton.
- **Data:** SQL→PG shadow write.
- **Schemas:** lock контракты.

## Релиз-чеклист для каждой фазы

1. Все DoD критерии выполнены.
2. CI зелёный.
3. Documentation updated (`docs/dual_loop/`).
4. `docs/DECISION_LOG.md` обновлён со всеми принятыми архитектурными решениями.
5. Runbook обновлён (если применимо).
6. Post-phase retrospective проведена.
