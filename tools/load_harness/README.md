# load_harness

Synthetic load injector + end-to-end latency probe for the Dual-Loop Fast Loop.
Closes release gate #4 ("synthetic 1 000 signals/min, zero drops") and the
quantitative half of gate #8 ("matcher p95 latency < 8 ms").

The harness lives under `tools/` (a workspace separate from `services/`) so it
is **never copied into a production Docker image** — `services/Dockerfile` and
the root `Dockerfile` only `COPY services/`, `src/`, `python/`, `dashboard/`
etc., never `tools/`.

## What it does

1. **Preloads watchlist** — publishes a small set of synthetic
   `WatchlistUpsert` entries on `dl.v1.watchlist.upsert` (Redis Streams) so
   `sniper-ingest`'s in-memory `WatchlistStore` knows about the markets.
2. **Generates load** — at the configured rate (default 1 000 / minute)
   publishes `MockMempoolTx` on `dl.v1.mempool.mock` (Redis Streams). Each
   `tx_hash` is a unique 32-byte counter so `evidence_hash` is unique per
   message and round-tripping is unambiguous.
3. **Consumes triggers** — subscribes to `dl.v1.triggers.fired` (NATS
   JetStream) with an ephemeral pull-consumer, decodes each `Trigger`, and
   matches `evidence_hash` back to the `Instant::now()` captured at publish
   time.
4. **Aggregates** — feeds latency samples into an `hdrhistogram::Histogram`
   and at the end of the run reports `published`, `observed`, `drop_rate`,
   `actual_rate_per_min`, `p50_ms`, `p95_ms`, `p99_ms`, `max_ms`, `mean_ms`.
5. **Asserts gates** — by default fails (`exit(1)`) if
   `drop_rate > --max-drop-rate` (zero by default), `p95_ms > --target-p95-ms`
   (8 ms by default), or `actual_rate_per_min < --min-rate-ratio *
   --rate-per-min` (90 % of target by default). `--no-assert` disables the
   gates so the run can be used purely for measurement.

## Why end-to-end and not Prometheus pull?

The matcher records its own internal latency in
`trigger_matcher_latency_ns` (added in PR-A), but reading that out of
`sniper-ingest`'s `/metrics` endpoint:

- Doesn't cover the **bus transport** (Redis Streams → matcher input,
  matcher output → NATS JetStream → consumer). Gate #8 cares about the
  caller-observable latency, not just the internal slice.
- Adds CI flakiness: a separate port-mapping, a scrape-interval race at
  startup, plus parsing the OpenMetrics exposition format on top.

The harness measures the wall-clock delta between the moment the publisher
hands the `MockMempoolTx` to Redis and the moment the consumer receives the
matching `Trigger` on NATS. That number is a **strict upper bound** on the
matcher-internal histogram and is the metric the gate actually wants.

## Usage

### Local smoke against the dev stack

Start the dev stack (or just Redis + NATS-with-JetStream + a running
`sniper-ingest` with `MOCK_MEMPOOL_ENABLED=true`), then:

```bash
cd tools
cargo run --release --bin load_harness -- \
  --rate-per-min 1000 \
  --duration-secs 30 \
  --markets 4 \
  --target-p95-ms 8.0 \
  --output human
```

A typical local result on a developer laptop (SSD, dev compose, no other
load) is `p50≈1ms / p95≈1.5ms / p99≈2ms`, well under the 8 ms gate.

### Stress test (10× target rate)

The tool deliberately decouples publisher rate from gate thresholds, so
the same binary can be cranked to 10 k/sec for one-off stress runs:

```bash
cargo run --release --bin load_harness -- \
  --rate-per-min 600000 \
  --duration-secs 30 \
  --target-p95-ms 50.0 \
  --max-drop-rate 0.001 \
  --no-assert
```

### CI integration

The `Load harness smoke (gate #4 #8)` job in `.github/workflows/dual-loop-ci.yml`
brings up Redis + a `nats:2.10-alpine -js` sidecar, builds and starts
`sniper-ingest`, then runs the harness for 30 s at 1 000 / min with
`--target-p95-ms 50.0` (relaxed from the 8 ms production target so a noisy
runner doesn't flake the build). Any future change that drops a single
trigger or pushes p95 above 50 ms on the runner blocks merge.

## Synthetic markets

`synth::synthetic_markets(N)` generates N deterministic markets:

| field                  | shape                                  |
| ---------------------- | -------------------------------------- |
| `polymarket_id`        | `load-harness-market-{i:03}`           |
| `ctf_address`          | `0xa110ad00{i+1:032x}` (valid hex!)    |
| `market_specific_key`  | `0x{i × 0x1111111111111111:064x}`      |

The leading bytes of `ctf_address` spell `a110ad` — visually distinct from
real Polymarket CTF addresses but still strict hex (sniper-ingest's mock
consumer parses it with `alloy_primitives::Address::from_str` which rejects
anything containing letters outside `[0-9a-f]`).

## Layout

| file              | role                                                                   |
| ----------------- | ---------------------------------------------------------------------- |
| `src/main.rs`     | bootstrap + four-phase orchestration + final gate enforcement          |
| `src/cli.rs`      | clap-derived CLI; defaults match gate #4 / #8 thresholds               |
| `src/bus.rs`      | Redis + NATS connect helpers (with small startup retry)                |
| `src/synth.rs`    | deterministic synthetic market generator                               |
| `src/publisher.rs`| watchlist preload + paced `MockMempoolTx` publisher loop               |
| `src/consumer.rs` | JetStream pull-consumer + drain logic                                  |
| `src/stats.rs`    | percentile aggregation + `RunReport` (serializable to JSON)            |
