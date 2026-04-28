# 06 — Roadmap & Phases

Дата актуализации: 2026-04-28

## Текущий статус

- `Phase 0` завершена.
- `Phase 1` в основном уже влита в `main`: bus/contracts, gateway-api foundation, Command Center preview, shadow PG.
- `gateway-api` больше не purely JSON-only на receive-side: inbound bridge уже умеет проецировать protobuf Redis payloads в UI state, сохраняя JSON fallback для тестов и legacy-path.
- `Phase 2` частично в коде: `sniper-ingest` и `sniper-risk` уже реализованы, а в `sniper-executor` уже влиты paper submit/fill/revert lifecycle, nonce actor, stuck-timeout loop и `control.kill` consumer/ack path. End-to-end DoD и build verification в этой среде пока не закрыты.
- `origin/devin/phase1-foundation-corrections` по сути интегрирована: wire contracts переведены на fixed-point (`*_ppm`, `*_bps`, `*_unix_ms`; без финансовых `double`).
- `origin/devin/phase1-track-c-command-center-ui` интегрирована как read-only / mock-fed preview.
- `origin/devin/phase1b-pg-listen-notify` **не** считается каноническим путем и не должна вливаться в `main`: LISTEN/NOTIFY остается best-effort side-channel, а source of truth для межсервисного uplink остается Redis/NATS bus.

## Phase 0 — Foundations (2 недели) — **завершена**

**Цель:** подготовить общую инфраструктуру, не ломая существующую Pythia.

**Задачи:**
- [x] Architecture docs (`docs/dual_loop/`).
- [x] Cargo workspace + stub `services/sniper-*` (компилятся, но no-op).
- [x] Protobuf schemas (`schemas/dual_loop/v1/*.proto`) + `buf.yaml`.
- [x] `docker-compose.dev.yml` с Redis 7, NATS JetStream, Postgres 16, Prometheus, Grafana, Loki, Tempo.
- [x] CI skeleton (`.github/workflows/dual-loop-ci.yml`): `buf lint`, `buf breaking`, `cargo check`, `cargo fmt --check`, `cargo clippy`.
- [x] Makefile с `dev-up`, `dev-down`, `fmt`, `lint`, `test`, `proto-gen`.
- [x] Миграция SQLite → PostgreSQL foundation: schema-first + shadow-write adapter landed in code.
- [x] Env-based feature flags / toggles landed; отдельного flag-service пока нет.

**DoD:**
- [x] `make dev-up` поднимает весь dev stack < 60s (Redis, NATS, Postgres, Prometheus, Grafana, Loki, Tempo).
- [x] Существующая Pythia продолжает работать без регрессий (не изменён `docker-compose.yml`, не изменён `src/**/*.ts`, не изменён `python/**/*.py`).
- [x] `cargo check` в `services/` зелёный.
- [x] `buf lint` на `schemas/` зелёный.
- [x] CI gate блокирует merge при нарушении lint/breaking.

## Phase 1 — Bus & Contracts (2 недели) — **в коде почти завершена, DoD частично открыт**

**Цель:** формализовать границу между контурами, запустить observability-loop данных без реального исполнения.

**Задачи:**
- [x] Определить protobuf-схемы (watchlist, signal, trigger, exec, control) и вычистить wire format до integer fixed-point.
- [x] `sniper-ingest` перестал быть чистым stub и уже содержит watchlist consumer + matcher/test surface.
- [x] Переписать Pythia watchlist publisher (`src/services/watchlist_publisher.ts`) в новый формат.
- [x] `gateway-api` foundation: REST, auth, Redis bridge, JSON Patch state.
- [x] Inbound protobuf → gateway state bridge (с JSON fallback на тестовом/legacy пути).
- [x] Базовая версия Command Center (read-only preview): Triage Queue + Status Bar + Heatmap + Execution panel.
- [x] SQLite → PostgreSQL shadow-write foundation.
- [x] Feature flags/toggles через env.
- [x] Dev setup с Anvil / local stack для Fast Loop foundation.
- [ ] Реальный gateway ↔ Command Center live feed вместо preview/mock path.

**DoD:**
- [x] Contract/codegen foundation и canonical topic registry есть в `main`.
- [x] Gateway routes и state bridge существуют в коде.
- [ ] Сигналы из Pythia появляются в **реальном** Command Center < 500ms.
- [ ] Оператор может Accept/Reject из самого Command Center UI (backend routes есть, UI feed еще preview/mock).
- [ ] Synthetic load test: 1k signals/min без drop.
- [ ] Убрать legacy JSON publish-side для `signals.decisions` / `control.kill` и довести `gateway-api` до чистого protobuf control-plane.
- [ ] Shadow PG live 7 дней без дельт с SQLite на критических запросах.

## Phase 2 — Sniper MVP: Ingest & Paper Execution (3–4 недели) — **частично реализована**

**Цель:** Fast Loop видит мемпул, принимает решения, но исполняет только paper-trades.

**Задачи:**
- [x] `sniper-ingest`: mempool firehose / reconnect foundation в коде.
- [x] ABI-декодер для CTF-контрактов и ключевых оракулов.
- [ ] `OrderBookTracker` с REST warm-up + WS incremental.
- [x] `TriggerMatcher` + risk pre-checks (`sniper-risk`) в коде.
- [x] `sniper-executor` paper-mode foundation в коде: submit/fill/revert lifecycle, `BroadcastSink::Noop`, nonce actor, stuck detection, `control.kill` consumer + `KillAck`.
- [x] Active Watchlist / Execution Panel preview в UI.
- [x] Replay harness и matcher/perf test surface.

**DoD:**
- [ ] End-to-end latency измерен: `mempool_seen → trigger_emitted` p95 < 8ms на staging-hardware.
- [ ] 72 часа непрерывного paper-run без memory leak (RSS drift < 5%).
- [ ] Backtest-harness: replay mempool-дампа даёт детерминированные triggers (byte-for-byte).
- [ ] Build/test verification для обновлённого `sniper-executor` и protobuf bridge подтверждена реальным toolchain/CI, а не только code inspection.
- [ ] Shadow-comparison: paper-fills vs реальное движение цены — статистика расхождений в weekly report.

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
