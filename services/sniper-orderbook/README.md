# sniper-orderbook

Fast Loop Polymarket L2 order-book tracker — **production-ready (gate #10, milestone 4 complete)**.

## What this is

A pure-Rust crate that owns the synchronization dance between a REST
snapshot and a WebSocket incremental feed. It exposes:

- **Types** — `PriceLevel`, `OrderBook`, `BookUpdate`, `BookSnapshot`,
  `BookSide` (everything in integer micros, no `f64` anywhere).
- **State machine** — `TrackerState`, `TrackerEvent`, `transition()`.
- **Tracker** — `Tracker` owns the book + buffer + state and exposes the
  full event API (`on_ws_connected`, `on_ws_message`,
  `request_snapshot`, `on_snapshot_received`, ...).

## M4 Completion (Gate #10)

**Multi-Market Driver** is now fully implemented and production-ready:

- ✅ **Multi-market orchestration** — `MultiMarketDriver` coordinates N trackers (one per market)
- ✅ **Live Polymarket integration** — Real REST snapshots + WebSocket price updates  
- ✅ **Watchlist integration** — SQLite initialization + NATS incremental updates
- ✅ **Fault tolerance** — Circuit breaker + exponential backoff (1s→32s)
- ✅ **Backpressure control** — Memory/CPU/tracker limit enforcement
- ✅ **Graceful shutdown** — Concurrent tracker shutdown with timeouts
- ✅ **Production binary** — CLI entry point with signal handling

**Deployment:**
```bash
cd services/sniper-orderbook
cargo build --release --bin driver
./target/release/driver
```

**Required environment variables:**
- `NATS_URL` — NATS server (default: nats://127.0.0.1:4222)
- `DRIVER_DB_PATH` — SQLite database path (default: /app/data/pythia.db)
- `DRIVER_MAX_TRACKERS` — Soft limit (default: 100)
- `DRIVER_MEMORY_THRESHOLD_MB` — Backpressure threshold (default: 2048)

See `docs/superpowers/specs/2026-06-26-gate10-m4-multi-market-driver-design.md` for full architecture.

## Architecture Overview

### Core Components (M1-M3)

The foundation consists of:

- **Types** — `PriceLevel`, `OrderBook`, `BookUpdate`, `BookSnapshot` (all integer micros)
- **State machine** — `TrackerState`, `TrackerEvent`, `transition()`
- **Tracker** — Owns book + buffer + state, exposes event API

### Multi-Market Driver (M4)

```
Main Binary (driver)
    ↓
MultiMarketDriver (coordinator)
    ├─► SQLite (initial watchlist)
    ├─► NATS (watchlist.upsert/remove)
    └─► TrackerPool (HashMap<AssetId, TrackerHandle>)
         └─► TrackerTask (one per market)
              ├─► Polymarket WS (price updates)
              ├─► Polymarket REST (snapshots)
              └─► M2 Tracker (state machine)
```

**Key features:**
- Dynamic spawn/shutdown from watchlist updates
- Circuit breaker prevents infinite retry loops
- Backpressure prevents resource exhaustion
- Health monitoring per tracker (Tracking/Syncing/Degraded/Stopping)

## Why this design

Polymarket's order book API — like virtually every exchange feed — is
eventually consistent. REST snapshots are stale by the time they're
parsed, and WS deltas can start arriving before the snapshot is fetched.
The only way to assemble a deterministic local book is:

1. Open the WS subscription **first**.
2. **Buffer** every incoming WS delta into memory; do not apply yet.
3. Issue the REST snapshot request.
4. When the snapshot lands, drop every buffered delta whose sequence is
   `<= snapshot.sequence`.
5. Apply the remaining buffered deltas in order to the snapshot.
6. Verify the next live delta is `last + 1`. If there's a gap, degrade
   and re-snapshot.

That dance is encoded as a strict state machine:

```
Connecting ─ws_connected─► BufferingWs ─snapshot_requested─► FetchingSnapshot
                                                                    │
                                                            snapshot_received
                                                                    ▼
                                                                Syncing
                                                                    │
                                                       buffer_drained_no_gap
                                                                    ▼
                                                                Tracking
```

Plus two terminal-ish states:

- `Degraded { reason }` — gap detected, snapshot failed, WS dropped,
  buffer overflowed. Caller is expected to reconnect and re-snapshot.
- `Stopped` — terminal, no further transitions.

## Running the tests

```
cd services
cargo test -p sniper-orderbook
```

78 unit tests + 4 integration tests (4 ignored - require live Polymarket API).

**Feature flags:**
- `polymarket-live` — Enables real Polymarket WS/REST integration (for manual testing)
- Default: Stub clients for hermetic testing

## Where it fits in the Fast Loop

**M4 Update:** Now live with multi-market coordination!

```
            ┌────────────────────────────┐
            │ Polymarket REST + WS       │
            └────────────┬───────────────┘
                         │ (✓ LIVE via driver binary)
                         ▼
        ┌─────────────────────────────────┐
        │ sniper-orderbook (this crate)   │
        │  - MultiMarketDriver            │
        │  - TrackerTask per market       │
        │  - M2 Tracker state machine     │
        └────────────┬────────────────────┘
                     │ (future: NATS publish)
                     ▼
        ┌─────────────────────────────────┐
        │ Bus (NATS)                      │
        │  dl.v1.orderbook.updated        │
        └────────────┬────────────────────┘
                     │
                     ├──► sniper-risk (slippage checks)
                     └──► sniper-executor (order placement)
        │ sniper-orderbook::Tracker       │
        │   - state machine               │
        │   - OrderBook (bids/asks)       │
        │   - delta buffer                │
        └────────────┬────────────────────┘
                     │ (future: spread / liquidity events on bus)
                     ▼
        ┌─────────────────────────────────┐
        │ sniper-executor / sniper-risk   │
        │ (consumes best_bid / mid /      │
        │  spread for slippage checks)    │
        └─────────────────────────────────┘
```

## Layout (Post-M4)

```
services/sniper-orderbook/
├── Cargo.toml
├── README.md          (this file)
├── src/
│   ├── lib.rs         (module-level docs + re-exports)
│   ├── book.rs        (PriceLevel, OrderBook, BookUpdate, BookSnapshot)
│   ├── state.rs       (TrackerState, TrackerEvent, transition())
│   ├── tracker.rs     (Tracker — owns book + buffer + state)
│   ├── polymarket/    (M2: REST + WS clients)
│   │   ├── mod.rs
│   │   ├── rest.rs
│   │   ├── ws.rs
│   │   └── wire.rs
│   ├── driver/        (M4: Multi-market coordination)
│   │   ├── mod.rs
│   │   ├── config.rs
│   │   ├── backoff.rs
│   │   ├── resource_monitor.rs
│   │   ├── tracker_task.rs
│   │   ├── coordinator.rs
│   │   └── polymarket_client.rs
│   └── bin/
│       └── driver.rs  (main binary entry point)
└── tests/
    └── (integration tests)
```

## CI

Covered by the existing `Rust (fmt + clippy + test)` workspace job — no
new workflow needed.

## Next steps (Post-M4)

**Completed in Gate #10:**
- ✅ Live Polymarket REST client (`fetch_snapshot`)
- ✅ Live Polymarket WS subscriber (`subscribe_to_book`)
- ✅ Multi-market driver (one `Tracker` per market, fan-out from watchlist)

**Future enhancements:**
1. **NATS publishing** — Publish orderbook updates to `dl.v1.orderbook.updated` for sniper-risk consumption
2. **Metrics/Observability** — Prometheus metrics + health endpoint (Task 9)
3. **Advanced monitoring** — Tracker exit monitoring, predictive backpressure
4. **Replay determinism** — Golden-fixture testing similar to PR #25
5. **Horizontal scaling** — Distribute trackers across multiple driver instances (if needed at scale)
