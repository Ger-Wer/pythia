# sniper-orderbook

Fast Loop Polymarket L2 order-book tracker — **skeleton stage (gate #10, milestone 1)**.

## What this is

A pure-Rust crate that owns the synchronization dance between a REST
snapshot and a WebSocket incremental feed. It exposes:

- **Types** — `PriceLevel`, `OrderBook`, `BookUpdate`, `BookSnapshot`,
  `BookSide` (everything in integer micros, no `f64` anywhere).
- **State machine** — `TrackerState`, `TrackerEvent`, `transition()`.
- **Tracker** — `Tracker` owns the book + buffer + state and exposes the
  full event API (`on_ws_connected`, `on_ws_message`,
  `request_snapshot`, `on_snapshot_received`, ...).

## What this is NOT (yet)

No live network. No `reqwest`. No `tokio-tungstenite`. No bus publish.
No persistence. The network layer (Polymarket REST/WS clients) lands in
a follow-up PR.

The point of this milestone is to ship a **deterministic, fully
unit-tested state machine** so the network layer can be plugged in
without ambiguity about what to do on every edge case (sequence gaps,
WS drops mid-snapshot, buffer overflow, periodic re-snapshot).

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

26 unit tests + 4 integration tests, all hermetic (no network, no
filesystem).

## Where it fits in the Fast Loop

```
            ┌────────────────────────────┐
            │ Polymarket REST + WS       │
            └────────────┬───────────────┘
                         │ (future: live clients)
                         ▼
        ┌─────────────────────────────────┐
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

## Layout

```
services/sniper-orderbook/
├── Cargo.toml
├── README.md          (this file)
├── src/
│   ├── lib.rs         (module-level docs + re-exports)
│   ├── book.rs        (PriceLevel, OrderBook, BookUpdate, BookSnapshot)
│   ├── state.rs       (TrackerState, TrackerEvent, transition())
│   └── tracker.rs     (Tracker — owns book + buffer + state)
└── tests/
    └── state_machine.rs   (end-to-end integration tests)
```

## CI

Covered by the existing `Rust (fmt + clippy + test)` workspace job — no
new workflow needed.

## Next steps (out of scope for this PR)

1. Live Polymarket REST client (`fetch_snapshot`).
2. Live Polymarket WS subscriber (`subscribe_to_book`).
3. Bus integration — publish `spread` / `mid_price` / `liquidity` events
   for sniper-risk slippage checks.
4. Multi-market driver (one `Tracker` per market, fan-out config from
   watchlist).
5. Replay / golden-fixture determinism similar to PR #25's
   replay binary.
