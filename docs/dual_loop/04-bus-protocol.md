# 04 — Bus Protocol (Node.js ↔ Rust)

## Транспорт

- **Redis Streams** — control-plane: ordered, durable, consumer groups (`XADD`, `XREADGROUP`, `XAUTOCLAIM`).
- **NATS JetStream** — data-plane: high-throughput telemetry (mempool, price ticks), at-most-once доставка допустима.

**Почему не Kafka.** Kafka имеет overhead p99 > 5ms на маленьких payload'ах — неприемлемо для control-plane c sub-second ожиданием. Redis Streams даёт consumer groups + p99 < 1ms при локальном deployment'е. Для fire-and-forget телеметрии NATS JetStream быстрее Redis (zero-persistence mode).

## Сериализация

| Канал | Формат | Почему |
|-------|--------|--------|
| Hot path (`triggers.*`, `telem.mempool`, `telem.ob.*`) | **Protobuf** (`prost` / `ts-proto`) | < 1µs encode/decode, schema evolution |
| Control plane (`watchlist.*`, `signals.*`, `exec.*`, `control.*`) | **Protobuf** (тот же registry) | единый реестр контрактов |
| Debug / UI | JSON | читаемость, DevTools |

## Schema registry

Все protobuf-схемы лежат в `schemas/dual_loop/v1/*.proto`. CI блокирует breaking changes через `buf breaking`.

### Wire conventions (Phase 1.1)

Схемы — integer-only. Float/double на шине **запрещены** (IEEE-754 округления ломают идемпотентность и byte-equivalence между Rust/TS/Python):

| Домен | Тип на шине | Пример |
|-------|-------------|--------|
| Деньги / цены | `Money { int64 amount_micros }` | `1_000_000` = $1.00 |
| Вероятности | `int64 *_ppm` | `1_000_000` = 100% |
| Знаковые вероятности (edge) | `sint64 *_ppm` (ZigZag) | `-50_000` = -5% |
| Доходности | `sint64 *_bps` | `40` = 40 bps = 0.40% |
| Wall-clock | `int64 *_unix_ms` | Unix epoch ms |
| Монотоники (hot path) | `uint64 *_ns` | `Trigger.observed_at_ns` |

Константы зафиксированы в `dual_loop.v1.FixedPointScales` (common.proto) и продублированы как `proto::{PROBABILITY_PPM_PER_UNIT, BPS_PER_UNIT, USD_MICROS_PER_UNIT}` в Rust-стороне. TS publisher конвертирует float → ppm на границе (см. `probToPpm` в `watchlist_publisher.ts`).

Timestamp-wrapper из Phase 0 **удалён** — унифицированы плоские `int64 *_unix_ms` (распаковка `Timestamp.monotonic_ns` на hot-path'е съедала такты).

Генерация клиентов:
- **Rust:** `prost-build` → `services/common/src/proto.rs` (через `build.rs`).
- **TypeScript:** `ts-proto` → `src/generated/dual_loop/v1/*.ts`.

См. `buf.yaml` и `buf.gen.yaml` в `schemas/`.

## Топики

| Topic | Transport | Producer | Consumer (cg) | Payload | QoS |
|-------|-----------|----------|---------------|---------|-----|
| `watchlist.upsert` | Redis Stream | pythia-core | sniper-ingest (`cg:sniper`) | `WatchlistEntry` | at-least-once, dedup by `market_id+version` |
| `watchlist.remove` | Redis Stream | pythia-core | sniper-ingest | `WatchlistRemove` | at-least-once |
| `signals.pending` | Redis Stream | pythia-core | gateway-api (`cg:ui`) | `Signal` | at-least-once |
| `signals.decision` | Redis Stream | gateway-api | pythia-core, sniper-ingest | `SignalDecision` | exactly-once (idempotency key) |
| `triggers.fired` | NATS JetStream | sniper-ingest | sniper-executor, gateway-api | `Trigger` | at-most-once (hot path) |
| `triggers.blocked` | Redis Stream | sniper-risk | audit | `TriggerBlocked` | at-least-once |
| `exec.submitted` | Redis Stream | sniper-executor | gateway-api, pythia-core | `ExecSubmitted` | at-least-once |
| `exec.filled` | Redis Stream | sniper-executor | all | `ExecFilled` | at-least-once |
| `exec.reverted` | Redis Stream | sniper-executor | all | `ExecReverted` | at-least-once |
| `telem.mempool` | NATS | sniper-ingest | gateway-api | `MempoolTick` | fire-and-forget |
| `telem.ob.<market>` | NATS | sniper-ingest | gateway-api | `OrderBookUpdate` | fire-and-forget |
| `telem.ping` | NATS | sniper-ingest | gateway-api | `Ping` | fire-and-forget |
| `control.kill` | Redis Stream | gateway-api | sniper-executor | `KillCommand` | exactly-once, подписан HMAC |
| `control.kill.ack` | Redis Stream | sniper-executor | gateway-api | `KillAck` | at-least-once |

## Идемпотентность и дедупликация

Для каждого сообщения в control-plane:
- `message_id` (UUIDv7) — глобально уникальный.
- `version` — монотонный per-entity (`market_id` для watchlist, `signal_id` для signals).
- Consumer ведёт LRU-cache последних 10k `message_id`'ов; дубликаты отбрасываются.

Для `signals.decision`:
- `idempotency_key = hash(signal_id + action + operator_id)`.
- Gateway отбивает 409 Conflict, если тот же `idempotency_key` уже обработан.

Для `control.kill`:
- Подпись HMAC-SHA256 на `{operator_id, nonce, scope, issued_at}` секретом, хранимым в HSM.
- Executor проверяет подпись и `issued_at ∈ [now-5s, now+5s]` (защита от replay).

## Версионирование схем

| Стратегия | Когда применять |
|-----------|-----------------|
| **Backward-compatible (non-breaking):** добавление `optional` поля | Всегда предпочтительно |
| **Parallel versions** (`watchlist.v1.upsert` + `watchlist.v2.upsert`) | При breaking change; старая версия живёт до миграции всех потребителей |
| **Hard rollover** | Запрещён без explicit RFC и downtime window |

`buf breaking` в CI проверяет каждый PR против `main`. Категории breaking:
- `FILE`, `PACKAGE`, `WIRE_JSON` — блокирующие.
- `WIRE` only — warning, требует version bump.

## Backpressure

### Redis Streams

- Каждый consumer group имеет `MAXLEN ~ 100000` (approximate trimming).
- `XAUTOCLAIM` запускается каждые 10s: забирает pending-сообщения с dead consumers.
- Prometheus metric `redis_stream_lag{topic, group}` экспортируется каждые 1s.
- При `lag > 500` → алерт в Grafana + Pythia автоматически повышает порог edge (`θ += 0.01`).

### NATS JetStream

- `max_msgs_per_subject = 10000`, `max_age = 1m` для телеметрии.
- Slow consumer отключается после 100 pending msg без ack'а.
- Telemetry subscribers используют "deliver-new-only" (не требуют replay).

## Пример сообщений (Protobuf → JSON для debug)

### `watchlist.upsert`

```json
{
  "message_id": "01939b3e-7f8a-7123-bf45-0123456789ab",
  "market_id": "polymarket:0xabc123...",
  "ctf_address": "0x1234567890abcdef...",
  "version": 7,
  "edge": 0.173,
  "p_fundamental": 0.723,
  "side_hint": "YES",
  "ttl_ms": 86400000,
  "confidence": 0.82,
  "max_position_usd": 5000,
  "max_slippage_bps": 50,
  "issued_at": "2026-04-22T11:54:00Z"
}
```

### `triggers.fired` (hot path, wire-level protobuf)

```
message Trigger {
  bytes message_id = 1;           // UUIDv7, 16 bytes
  bytes market_id = 2;            // hashed, 32 bytes
  TriggerType trigger_type = 3;
  bytes evidence_hash = 4;        // 32 bytes
  uint64 eta_block = 5;
  uint64 observed_at_ns = 6;      // monotonic clock
  double expected_price = 7;
  double slippage_bps = 8;
}
```

## Implementation (Phase 1)

Phase 1 Track A wires the bus between Pythia (Slow Loop, TS) and
`sniper-ingest` (Fast Loop, Rust). Watchlist is the first topic to go live
end-to-end; signals / triggers / control follow in Phase 2. The bus is
**feature-flagged** and off by default — existing Pythia behaviour is
preserved byte-for-byte when the flag is off.

### Topic prefix and wire version

All Phase 1 topics are prefixed with the protobuf package version:

| Canonical name | Rust const | TS const |
|----------------|------------|----------|
| `dl.v1.watchlist.upsert` | `dual_loop_common::topics::WATCHLIST_UPSERT` | `WATCHLIST_UPSERT` |
| `dl.v1.watchlist.remove` | `WATCHLIST_REMOVE` | `WATCHLIST_REMOVE` |
| `dl.v1.signals.emitted` | `SIGNALS_EMITTED` | `SIGNALS_EMITTED` |
| `dl.v1.triggers.fired` | `TRIGGERS_FIRED` | `TRIGGERS_FIRED` |
| `dl.v1.control.kill` | `CONTROL_KILL` | `CONTROL_KILL` |
| `dl.v1.control.kill_ack` | `CONTROL_KILL_ACK` | `CONTROL_KILL_ACK` |
| `dl.v1.exec.status` (NATS JetStream) | `EXEC_STATUS` | `EXEC_STATUS` |

Single source of truth:
* `services/common/src/topics.rs`
* `src/services/bus/topics.ts`

The TS contract test `src/services/bus/__tests__/contract.test.ts`
parses the Rust file at test time and asserts byte-for-byte parity.

### Env vars

| Env var | Default | Consumed by | Effect |
|---------|---------|-------------|--------|
| `DUAL_LOOP_BUS_ENABLED` | `false` | `src/services/watchlist_publisher.ts` | Master flag. When `false`, `WatchlistPublisher.maybePublishDecision` is a no-op and no Redis connection is opened. |
| `DUAL_LOOP_REDIS_URL` | falls back to `REDIS_URL` then `redis://127.0.0.1:6379` | TS publisher | Redis host for `XADD`. |
| `REDIS_URL` | unset | Rust `sniper-ingest` | When set, the binary runs `ConsumerGroup::consume` for `dl.v1.watchlist.upsert` + `remove`; when unset, it only runs the TTL reaper (Phase 0 skeleton behaviour). |
| `METRICS_ADDR` | `0.0.0.0:9101` | Rust services | Prometheus scrape endpoint. |

### Code layout

```
schemas/dual_loop/v1/*.proto        # canonical contracts (unchanged in Phase 1)
schemas/buf.gen.yaml                # TS codegen only (Rust goes via build.rs)
scripts/proto-gen-ts.sh             # wrapper: `buf generate` into src/generated/
scripts/contract/                   # cross-language test helpers

services/common/
├── build.rs                        # prost-build → services/common/src/generated/
├── src/proto.rs                    # re-exports: common, watchlist, signal, trigger, execution, control
├── src/topics.rs                   # Rust topic constants (source of truth)
├── src/bus/redis.rs                # StreamPublisher, ConsumerGroup (XADD / XREADGROUP / XACK)
├── src/bus/nats.rs                 # JetStreamClient (publish-only MVP)
└── tests/
    ├── roundtrip.rs                # single-language proto roundtrip
    └── cross_lang.rs               # TS-publish → Rust-consume (ignored by default)

services/sniper-ingest/src/watchlist.rs   # WatchlistStore + TTL reaper + metrics

src/services/bus/
├── topics.ts                       # TS topic constants (source of truth)
├── redis_streams.ts                # StreamPublisher, ConsumerGroup
├── nats.ts                         # JetStreamPublisher (optional `nats` dep)
└── __tests__/contract.test.ts      # TS roundtrip + topic parity with Rust
src/services/watchlist_publisher.ts # Pythia → sniper-ingest, flagged by DUAL_LOOP_BUS_ENABLED
```

Generated output (`services/common/src/generated/`, `src/generated/`) is
gitignored.

### Wire framing

Both `StreamPublisher` implementations use the same Redis Streams framing:

* Entry field `payload` holds raw protobuf bytes (`prost::Message::encode`
  or ts-proto `.encode().finish()`).
* Optional entry field `trace_id` carries a correlation id for structured
  logs / OpenTelemetry.
* `XADD` is called with `MAXLEN ~ 10000` by default to cap stream length.

Consumer semantics:

* `XREADGROUP` with `BLOCK 1000 COUNT 64`.
* On successful handler → `XACK`. On failure → message left pending and
  the next `XAUTOCLAIM` run (owned by `sniper-ingest`) will re-deliver it.

### WatchlistPublisher (Pythia, TS)

`src/services/watchlist_publisher.ts` hooks into `decision_core` after the
SQLite commit in a `queueMicrotask` — publishing is never on the hot path.
Mapping:

| decision-core action | Bus kind | Side hint |
|----------------------|----------|-----------|
| `enter_yes` / `enter_no` | ACCEPT (upsert) | `SIDE_YES` / `SIDE_NO` |
| `review` | SNOOZE (upsert) | `SIDE_UNSPECIFIED` |
| `watch` | skip | — |

When `DUAL_LOOP_BUS_ENABLED=false` (default) the publisher is constructed
but never opens a connection; `maybePublishDecision` returns `null`. Only
flipping the flag plus providing `DUAL_LOOP_REDIS_URL` actually changes
runtime behaviour.

### WatchlistConsumer (sniper-ingest, Rust)

`services/sniper-ingest/src/watchlist.rs` maintains an in-memory
`DashMap<(polymarket_id, ctf_address), WatchlistEntry>`:

* Upserts respect `version` — stale versions are ignored.
* A TTL reaper runs every 10s; expired entries are swept.
* Exported Prometheus metrics on the service's existing scrape port:
  - `watchlist_entries_total` (gauge)
  - `watchlist_upserts_total` (counter)
  - `watchlist_removes_total` (counter)
  - `watchlist_ttl_expirations_total` (counter)
  - `sniper_ingest_heartbeat_total` (counter) — confirms the binary is live.

### Local E2E

```sh
# 1. Start the dev stack (Redis, NATS, Postgres, Prometheus, Grafana).
make dev-up

# 2. Regenerate protobuf clients (if you changed schemas).
bun install               # once per checkout
bun run proto:gen         # TS (buf + ts-proto)
make proto-gen            # both TS and Rust (runs bun run proto:gen + cargo build)

# 3. Full CI-equivalent locally.
make check                # fmt + clippy + proto lint + rust tests
bun test src/services/bus/__tests__/contract.test.ts
make bus-cross-lang-test  # TS publish → Rust consume (requires Redis on :6379)
```

### Contract testing

Three layers; all three run in the `bus-contract-tests` CI job
(`.github/workflows/dual-loop-ci.yml`):

1. **Rust roundtrip** (`services/common/tests/roundtrip.rs`) — every
   Phase 1 message encodes, decodes, and round-trips equal.
2. **TS roundtrip + topic parity**
   (`src/services/bus/__tests__/contract.test.ts`) — same as (1) plus a
   file-level parse of `services/common/src/topics.rs` that asserts the
   TS and Rust `ALL_TOPICS` arrays are byte-for-byte identical and the
   `WIRE_VERSION` constants agree.
3. **Cross-language** (`services/common/tests/cross_lang.rs`, `#[ignore]`
   by default) — spins up a real Redis 7 container, the TS helper
   `scripts/contract/publish_watchlist_upsert.ts` publishes a
   `WatchlistUpsert`, and the Rust `ConsumerGroup` reads + decodes it and
   asserts field-level equality.

Total size: ~120 bytes, encode/decode < 500ns на современном CPU.
