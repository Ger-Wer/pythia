# Phase 2 — Mempool Ingest: Setup & Replay Guide

## Overview

`sniper-ingest` (Phase 2) adds three core capabilities to the Fast Loop:

1. **MempoolSubscriber** — streams pending transactions from an EVM node via WebSocket or IPC.
2. **ABI Decoder** — decodes on-chain events from Polymarket CTF Exchange, UMA Optimistic Oracle V3, and Chainlink Aggregator contracts into strongly-typed Rust enums.
3. **TriggerMatcher** — matches decoded events against the in-memory watchlist and emits `Trigger` messages to the bus when policy criteria are met.

## Transport Configuration

### Primary: WebSocket (dev/CI)

Set `ANVIL_WS_URL=ws://127.0.0.1:8545` to connect to a local Anvil fork.

```bash
# Start Anvil via docker-compose
make dev-up

# Or manually
anvil --fork-url https://polygon-rpc.com --chain-id 137
```

### Secondary: IPC (staging/prod)

Set `POLYGON_IPC_PATH=/var/run/bor/bor.ipc` to connect to a local Bor/Erigon node.
This transport is **not used in dev/CI** — the actor only starts when the env var is set.

## Recorder Mode

Set `MEMPOOL_RECORDER=true` to dump every pending transaction to rolling hourly files:

```
/data/mempool-dumps/2025-01-15-14.jsonl
/data/mempool-dumps/2025-01-15-15.jsonl
```

Each line is a JSON object:
```json
{"ts_ns": 1234567890, "tx": { ... }}
```

## Replay Harness

The `replay` binary reads a mempool dump and feeds it through the TriggerMatcher at real (or scaled) timings:

```bash
# Build
cd services && cargo build --release --bin replay

# Replay at 10x speed
./target/release/replay --dump /data/mempool-dumps/2025-01-15-14.jsonl --speed 10.0

# Replay first 1000 transactions
./target/release/replay --dump sample.jsonl --start-tx 0 --limit 1000
```

Output is JSONL triggers on stdout, suitable for `diff`-ing against baselines.

## Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `mempool_tx_seen_total` | counter | Total pending txs observed |
| `mempool_decoded_total{contract}` | counter | Decoded events by contract |
| `mempool_drop_total` | counter | Txs dropped (channel overflow) |
| `trigger_fired_total{market,kind}` | counter | Triggers emitted |
| `trigger_matcher_latency_ns` | histogram | tx_seen → trigger_emitted latency |
| `watchlist_size` | gauge | Current watchlist entry count |

Metrics are scraped by Prometheus at `:9101/metrics`.

## Benchmarks

```bash
cd services && cargo bench --package sniper-ingest
```

Key benchmarks:
- `matcher_throughput_1M` — 1M events through the matcher.
- `decode_ctf_event_10k` — 10k ABI decode operations.
- `e2e_latency_100_events` — End-to-end latency measurement.

## Docker Compose

The `anvil` service in `docker-compose.dev.yml` provides a local Polygon fork:

```yaml
anvil:
  image: ghcr.io/foundry-rs/foundry:latest
  command: "anvil --host 0.0.0.0 --chain-id 137 --fork-url ${POLYGON_RPC_URL:-https://polygon-rpc.com}"
  ports:
    - "127.0.0.1:8545:8545"
```

If `POLYGON_RPC_URL` is not set, Anvil starts with a clean chain (no fork).

## ABI Contracts

ABI JSON files are stored in `contracts/abi/`:
- `CtfExchange.json` — Polymarket CTF Exchange (`0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`)
- `UmaOracleV3.json` — UMA Optimistic Oracle V3 (`0xfb55F43fB9F48F63f9269DB7Dde3BbBe1ebDC0dE`)
- `ChainlinkAggregator.json` — Chainlink Aggregator (any address)

Solidity interfaces are defined inline via `alloy_sol_types::sol!` in `services/common/src/abi/`.
