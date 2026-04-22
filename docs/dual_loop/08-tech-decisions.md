# 08 — Tech Decisions & North Star Metrics

## Выбор технологий (обоснования)

| Решение | Альтернативы | Почему выбрано |
|---------|--------------|----------------|
| **Rust** для Fast Loop | Go, C++ | Zero-cost abstractions + отсутствие GC pauses критичны (Go: 100µs–1ms STW для HFT неприемлемо). Экосистема `alloy-rs`, `revm` для EVM зрелая и активно развивается. Memory safety важна для HSM-пайплайна. |
| **Redis Streams + NATS JetStream** | Kafka, RabbitMQ, NATS Core | Kafka: overhead p99 > 5ms на маленьких payload'ах (ZooKeeper/KRaft overhead). RabbitMQ: нет нативного replay consumer groups. Redis Streams: p99 < 1ms на local deployment + consumer groups. NATS JetStream: < 0.5ms enqueue для fire-and-forget telemetry. |
| **PostgreSQL 16** | оставить SQLite, MySQL, CockroachDB | Concurrent writes, logical replication для backup, `LISTEN/NOTIFY` для мгновенных uplinks в Gateway. Zero-downtime schema migrations. SQLite — single-writer bottleneck при multi-service архитектуре. |
| **Protobuf** (v3) | JSON, Avro, Cap'n Proto, MessagePack | Быстрая сериализация (< 1µs/msg), schema evolution, отличная поддержка в Rust (`prost`) и TS (`ts-proto`). Cap'n Proto быстрее (zero-copy), но экосистема беднее + сложнее migrate. |
| **React 18 + Zustand** | Svelte, Solid, vanilla | Существующая React-база в `dashboard/`; Zustand даёт fine-grained subs без Redux-boilerplate. Selectors → isolated re-renders для hot rows. |
| **uWebSockets** на gateway | `ws`, socket.io | Порядок на величину выше throughput; < 1ms overhead. Socket.io слишком heavy для наших нужд (rooms, binary) — не нужен. |
| **Fastify** (gateway REST) | Express, Koa, Hono | Schema-first validation, async hooks, best throughput в Node. Express legacy, Koa изолирован от экосистемы. |
| **Tokio** (Rust async runtime) | async-std, smol | Де-факто стандарт, лучшая интеграция с `alloy-rs`, `lapin`, `redis-rs`. |
| **`alloy-rs`** для EVM | `ethers-rs`, `web3-rs` | Active development, стал наследником `ethers-rs`. Лучшая поддержка EIP-1559 и новых типов tx. |
| **buf** для protobuf | Native `protoc`, Bazel | Современный pipeline, `buf breaking`/`buf lint` — industry standard. Integration с CI проще чем Bazel. |
| **HSM: YubiHSM 2** | AWS KMS, Fireblocks, self-built | YubiHSM 2: on-premise, sub-ms sign latency (важно для HFT), FIPS 140-2 Level 3. AWS KMS: network latency 10-50ms — неприемлемо в hot path. Fireblocks: dependency на third party с SLA, дорого. |
| **Private relay: bloXroute** | Flashbots (нет для Polygon), Merkle, Manifold | Лучший coverage на Polygon, гарантированное приоритетное распространение. Fallback на Merkle для редундантности. |

## North Star Metrics

Эти метрики — главные показатели успеха системы. Каждая имеет target (цель) и SLA (минимум); отклонение от SLA — paging alert.

| Метрика | Target | SLA (minimum) | Как мерить |
|---------|--------|---------------|------------|
| **Latency**: `mempool_seen → broadcast` p95 | < 5ms | < 15ms | monotonic clock delta, exported per-trigger |
| **Accuracy**: Brier score Pythia-forecasts | < 0.20 | < 0.25 | rolling 100+ разрешённых рынков |
| **Edge realization**: realized PnL / theoretical edge | ≥ 60% | ≥ 40% | weekly reconciliation live vs paper |
| **Uptime ingest** | 99.9% | 99.5% | Prometheus uptime monitor |
| **Kill-switch responsiveness** | < 500ms | < 1s | chaos test еженедельно |
| **Operator time-to-decision** (median) | < 10s | < 30s | WS event timestamps |
| **False-positive trigger rate** | < 1% | < 5% | weekly post-mortem sample |
| **Cost per profitable trade** (gas + infra amortized) | < 20% realized PnL | < 40% | monthly finance recon |

## Нефункциональные требования (NFR)

### Безопасность
- Каждая подпись tx — через HSM, приватный ключ не покидает HSM.
- Audit log immutable (append-only hash-chain, verify-on-read).
- RBAC: роли `operator`, `admin`, `viewer`; principle of least privilege.
- Secrets — только в secret manager (Vault / Doppler / 1Password Secrets), не в ENV на диске.

### Надёжность
- RTO (Recovery Time Objective): 5 минут для non-trading функций, 30 секунд для kill-switch.
- RPO (Recovery Point Objective): 0 для signed transactions, 1 минута для decision log.
- Backup: Postgres — continuous WAL archive + daily base backup в S3-compatible storage.

### Наблюдаемость
- 100% requests traced (OpenTelemetry).
- Все async boundaries пересекаются с context propagation.
- SLI/SLO для каждого container'а в Grafana.
- Alert runbook link в каждом alert'е.

### Performance
- Hot path (mempool→trigger): zero allocation, no lock contention.
- Control plane RTT: p99 < 2ms.
- UI: 60fps sustained, no jank при 150 markets в watchlist.

### Cost
- LLM budget: cap в Pythia уже есть (`DAILY_COST_LIMIT_USD`), сохранить.
- Infra: dev < $200/mo, staging < $500/mo, prod < $2000/mo (excl. Polygon node co-location).

## Решения, которые ОТКЛОНЕНЫ

| Идея | Почему отклонена |
|------|------------------|
| Использовать существующий Python stack для Fast Loop | GIL + GC делают sub-ms latency недостижимым без сложных workarounds |
| Бросить SQLite и мигрировать сразу в Phase 0 | Риск регрессии production'а; shadow-write safer |
| Использовать gRPC для bus | Connection overhead + proto-over-HTTP2 медленнее чем Redis Streams для pub/sub pattern |
| Сделать Command Center Electron-приложением | Web UI достаточен; Electron добавляет maintenance burden |
| Автоматическое исполнение БЕЗ operator approval | Risk слишком высок для MVP; в Phase 4 может быть опция per-market |
