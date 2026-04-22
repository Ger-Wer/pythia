# 01 — Containers (C4 Level 2)

## Таблица контейнеров

| # | Container | Stack | Отв. за | SLO |
|---|-----------|-------|---------|-----|
| C1 | `pythia-core` | Node.js 20 / TS (Bun) | Poll Gamma, orchestration, paper portfolio | p95 cycle < 60s |
| C2 | `pythia-llm` | Node.js worker | DeepSeek batched inference, scoring | p95 inference < 8s |
| C3 | `pythia-db` | SQLite → **PostgreSQL 16** (миграция в Phase 1) | Markets, signals, decisions, fills | p99 write < 10ms |
| C4 | `bus-redis` | Redis 7 Streams + RedisTimeSeries | Pub/sub, consumer groups, ordered delivery, control-plane | < 1ms RTT |
| C5 | `bus-nats` | NATS JetStream | High-throughput telemetry (mempool, price ticks) | < 0.5ms enqueue |
| C6 | `sniper-ingest` | Rust (tokio + alloy-rs) | Mempool sub, log parsing, оракул-детект | < 5ms from block to trigger |
| C7 | `sniper-executor` | Rust (alloy + private relay client) | Sign & broadcast, nonce mgr, RBF/EIP-1559 | < 15ms sign→broadcast |
| C8 | `sniper-risk` | Rust | Pre-trade checks, position caps, circuit breaker | < 100µs per check |
| C9 | `command-center` | React 18 + Vite + Zustand + uWebSockets | Bloomberg-style UI | < 16ms frame, < 50ms WS lag |
| C10 | `gateway-api` | Node (Fastify) + WSS | Auth, REST for mutations, WS fan-out | p99 < 20ms |
| C11 | `telegram-bots` | Node | Low-priority alerts, EOD digests | async |
| C12 | `observability` | Prometheus + Grafana + Loki + Tempo | Metrics, logs, traces | — |

## Маппинг на существующий репозиторий

| Container | Расположение в репо (текущее) | Расположение в репо (целевое) |
|-----------|-------------------------------|-------------------------------|
| C1 `pythia-core` | `src/main.ts`, `src/services/*` | без изменений |
| C2 `pythia-llm` | `src/services/research_runner.ts`, `src/services/naked_r1.ts` | выделить в отдельный worker-entry, Phase 1 |
| C3 `pythia-db` | `src/services/db.ts` (SQLite) | `src/services/db_pg.ts` + миграция, Phase 1 |
| C4/C5 bus | — | `docker-compose.dev.yml` → сервисы `redis`, `nats`, Phase 0 |
| C6/C7/C8 sniper-* | — | `services/sniper-*/` (Rust workspace), Phase 2 |
| C9 `command-center` | `dashboard/` (существующий) | расширение `dashboard/` в Phase 2 |
| C10 `gateway-api` | `python/dashboard_api.py` (FastAPI) | отдельный Node/Fastify-сервис, Phase 1 |
| C11 `telegram-bots` | `python/telegram_*` | без изменений |
| C12 `observability` | — | `infra/` + compose, Phase 0 |

## Принципы развёртывания

- **Dev:** всё через `docker-compose.dev.yml` (`make dev-up`). Локальный Polygon mock или testnet-нода.
- **Staging:** отдельный VPS, реальная Polygon-нода, paper-mode executor, оператор тестирует UI/hotkeys.
- **Production:** co-location с Polygon peer, dedicated host для `sniper-*`, HSM для подписи ключей, private relay с SLA.

## Сетевые границы

```
┌─────────────────── Internet ───────────────────┐
│                                                │
│   [Operator]    [Gamma API]    [DeepSeek]      │
│       │              │              │          │
└───────┼──────────────┼──────────────┼──────────┘
        │ HTTPS        │ HTTPS        │ HTTPS
┌───────▼──────────────▼──────────────▼──────────┐
│           DMZ / Reverse Proxy (Caddy)          │
└───────┬─────────────────────────────┬──────────┘
        │                             │
┌───────▼─────────────┐     ┌─────────▼──────────┐
│  gateway-api (C10)  │     │  pythia-core (C1)  │
│  command-center(C9) │     │  pythia-llm  (C2)  │
└───────┬─────────────┘     └─────────┬──────────┘
        │                             │
        └──────────────┬──────────────┘
                       │
       ┌───────────────▼────────────────┐
       │     Integration Bus (Redis/NATS)
       │     pythia-db (Postgres, private)
       └───────────────┬────────────────┘
                       │
       ┌───────────────▼────────────────────────┐
       │   Fast Loop (dedicated, air-gapped)    │
       │   sniper-ingest → sniper-risk          │
       │                 → sniper-executor      │
       │                       │                │
       │                       ▼                │
       │               Polygon Node (local)     │
       │               Private Relay (egress)   │
       └────────────────────────────────────────┘
```

## Горизонтальное масштабирование

| Container | Scale strategy |
|-----------|----------------|
| `pythia-core` | Single instance (leader election через Redis), watchdog-рестарт |
| `pythia-llm` | Worker pool по числу CPU, batch inference |
| `sniper-ingest` | 1 инстанс на нода-peer (избыточность через 2 ноды) |
| `sniper-executor` | Single writer per HSM-ключ (nonce-consistency) |
| `gateway-api` | N инстансов за reverse proxy, sticky WS |
| Bus | Redis sentinel (3 nodes), NATS cluster (3 nodes) |

Все инстансы health-check'аются через Prometheus `/metrics` + liveness HTTP probe.
