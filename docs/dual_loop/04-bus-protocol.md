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

Total size: ~120 bytes, encode/decode < 500ns на современном CPU.
