# 00 — Overview (C4 Level 1: System Context)

## Цель системы

Dual-Loop объединяет две парадигмы принятия торговых решений на Polymarket:

- **Slow Loop (Pythia — The Radar):** фундаментальный анализ рынков с помощью LLM, агрегации новостей (Exa/Tavily) и Gamma API. Горизонт решения — минуты/часы.
- **Fast Loop (Sniper — The Cannon):** мониторинг mempool локальной ноды Polygon, детект оракул-событий, подпись и отправка транзакций за миллисекунды.

Slow Loop играет роль **наводчика**: формирует шорт-лист рынков с высоким `Edge = P_fundamental − P_market` и передаёт их Fast Loop'у. Fast Loop играет роль **исполнителя**: ждёт ончейн-триггера по переданным рынкам и мгновенно входит в сделку.

Единственный human-in-the-loop — оператор за **Command Center**, который видит оба контура и может в любой момент вмешаться (Manual Override) или остановить всё (Kill Switch).

## C4 Level 1 — System Context

```
                ┌────────────────────────────────────────────────┐
                │               HUMAN OPERATOR                   │
                │        (Command Center, Bloomberg-style)       │
                └───────────▲───────────────────────▲────────────┘
                            │ WSS (<50ms)           │ Hotkeys/REST
                            │                       │
        ┌───────────────────┴───────────────────────┴─────────────┐
        │              INTEGRATION BUS (Redis Streams + NATS)     │
        │   topics: signals.*, watchlist.*, triggers.*, exec.*    │
        └───▲─────────────────────────────────────────────────▲───┘
            │                                                 │
   ┌────────┴────────┐                                ┌───────┴─────────┐
   │  SLOW LOOP      │                                │  FAST LOOP      │
   │  Pythia (Node)  │                                │  Sniper (Rust)  │
   │  fundamentals   │───── watchlist + edge ────────▶│  microsecond    │
   │  LLM, Exa/Gamma │◀──── fills, p&l, telemetry ────│  mempool, exec  │
   └────────▲────────┘                                └────────▲────────┘
            │                                                 │
   ┌────────┴────────┐                                ┌───────┴─────────┐
   │ Gamma API       │                                │ Polygon Node    │
   │ Exa, Tavily     │                                │ (Erigon/Bor)    │
   │ DeepSeek LLM    │                                │ Mempool, RPC    │
   └─────────────────┘                                └─────────────────┘
```

## Акторы

| Актор | Роль | Интерфейс |
|-------|------|-----------|
| **Operator** | Единственный human-in-the-loop: approver сигналов, holder of kill-switch, manual override | Command Center (WSS + REST), hotkeys, Telegram fallback |
| **External data sources** | Поставщики данных: Gamma API (Polymarket REST), Exa/Tavily (news), DeepSeek (LLM inference) | HTTPS |
| **Polygon node** | Локальная Erigon/Bor-нода с включённым `txpool.api`, подписка на pending transactions | IPC / WS |
| **Private relay** | bloXroute / Merkle / Manifold для приватного broadcast'а транзакций | HTTPS/gRPC |

## Границы системы (What's in / What's out)

**In scope:**
- Ingestion рынков Polymarket и их оракулов.
- LLM-prediction + edge calculation.
- Mempool monitoring на Polygon.
- Подпись и broadcast транзакций (в Phase 3 — live).
- Операторский UI (Command Center).
- Наблюдаемость (metrics, logs, traces).

**Out of scope (на текущей итерации):**
- Другие chain'ы (Ethereum L1, Arbitrum, Base).
- Market-making / passive LP.
- CEX-арбитраж.
- Автоматическая экспертиза LLM без supervision (этот ограничитель снимается только по явному решению оператора, per-market).
