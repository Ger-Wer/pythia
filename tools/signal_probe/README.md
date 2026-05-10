# signal_probe

End-to-end **Slow Loop → Gateway → UI** signal latency probe — closes
Paper Operator Release **gate #3** ("UI signal feed latency < 500 ms").

Per the 2026-05-05 decision log this probe is **measurement-only**: it
prints percentiles + an ASCII histogram and always exits 0. Operators run
it manually against a local dev stack to catch regressions before tagging
a release. **It is not a CI quality gate.**

## How it works

```
   ┌──────────────┐    XADD       ┌──────────────┐   bridge      ┌──────────────┐
   │ signal_probe │ ──────────►   │  Redis       │ ────────────► │  gateway-api │
   │              │    payload   │  Streams     │               │  EventStore  │
   │              │              │  dl.v1.      │               │              │
   │              │              │  signals.    │               │              │
   │              │              │  emitted     │               │              │
   │              │              └──────────────┘               │              │
   │              │                                             │              │
   │              │ ◄──────────────────────────────────────────  │              │
   │              │   WS  /ws/v1   {kind:"snapshot"|"patch"}    │              │
   └──────────────┘                                             └──────────────┘
```

1. The probe authenticates with the dev operator (`POST /api/v1/auth/token`,
   defaults `admin / changeme`) and obtains a PASETO v4.local token.
2. Opens `wss?://<host>/ws/v1?token=...`, subscribes to the `signals`
   topic, and waits for the initial snapshot to drain.
3. Periodically `XADD`s a synthetic signal payload to
   `dl.v1.signals.emitted`. The payload embeds a fresh `issued_at_unix_ms`
   timestamp.
4. On every WS snapshot/patch, finds matching `signal_id`s and records
   `received_at - issued_at_unix_ms` into a histogram.
5. After the run window, prints percentiles + an ASCII histogram and
   exits 0.

## Why TypeScript and not Python

`gateway-api` is already a Bun TS project; matching the language gives:

- Zero new toolchains in CI (the `tools/signal_probe` CI job is a sibling
  of `gateway-api` / `dashboard`, not a new ecosystem).
- Direct typing of the WS message shapes (`snapshot`, `patch`,
  `signals` topic) without re-deriving them.
- Native `WebSocket` and `fetch` in Bun — only one runtime dependency
  (`ioredis`) needed.

## Why a separate workspace under `tools/`

Same logic as `tools/load_harness/` (PR #26) and `tools/soak/` (PR #27):
this directory is **not copied into any production Docker image**. The
gateway-api Dockerfile, the root Dockerfile, and `services/Dockerfile`
all only `COPY services/`, `src/`, `python/`, `dashboard/`. Putting the
probe under `tools/` guarantees by construction that a measurement-only
tool can never leak into a deployed image.

## Usage

### Quick smoke against a local dev stack

```bash
# 1. Stand up a minimal stack (Redis + gateway-api). Either:
make dev-up
# …or just the bits the probe needs:
docker run -d --name probe-redis -p 6379:6379 redis:7.4-alpine
cd gateway-api && REDIS_URL=redis://127.0.0.1:6379 PORT=9103 \
  GATEWAY_PASETO_KEY="$(openssl rand -base64 32)" bun run src/index.ts &

# 2. Install & run the probe:
cd tools/signal_probe
bun install
bun run src/main.ts \
  --gateway-http http://127.0.0.1:9103 \
  --gateway-ws  ws://127.0.0.1:9103/ws/v1 \
  --redis        redis://127.0.0.1:6379 \
  --duration-secs 30 \
  --interval-ms   1000
```

Sample output:

```
[probe] gateway=http://127.0.0.1:9103  ws=ws://127.0.0.1:9103/ws/v1  redis=redis://127.0.0.1:6379
[probe] duration=5s  interval=250ms  stream=dl.v1.signals.emitted
[probe] obtained PASETO token
[probe] WS connected, subscribed to 'signals' topic

[probe] --- final report ---
[probe] published        = 20
[probe] matched          = 20
[probe] unmatched        = 0
[probe] duration         = 7.0s
[probe] latency          = samples=20  min=1.00ms  mean=1.85ms  p50=2.00ms  p95=3.00ms  p99=5.00ms  max=5.00ms
[probe] histogram:
  [1.00..1.40 ms]  ######################### (7)
  [1.80..2.20 ms]  ######################################## (11)
  [3.00..3.40 ms]  #### (1)
  [4.60..5.00 ms]  #### (1)
[probe] (measurement-only — exit 0 regardless of p95)
```

### Pre-release ritual on staging

When preparing a release on `amper`, run the probe against the live
stack with the production PASETO key in the env:

```bash
SIGNAL_PROBE_PASSWORD="$REAL_OPERATOR_PASSWORD" bun run src/main.ts \
  --gateway-http https://gateway.staging.pythia \
  --gateway-ws   wss://gateway.staging.pythia/ws/v1 \
  --redis        $REDIS_URL_STAGING \
  --duration-secs 120 \
  --interval-ms   500
```

The `unmatched` field in the report is the most actionable signal — a
non-zero count means a published signal never arrived at the WS client
within `--tail-wait-ms`, which is a sign of a stuck consumer or a broken
bridge.

## CLI

| flag                         | default                          | meaning                                                              |
| ---------------------------- | -------------------------------- | -------------------------------------------------------------------- |
| `--gateway-http URL`         | `http://127.0.0.1:9103`          | gateway-api base URL for `POST /api/v1/auth/token`                   |
| `--gateway-ws URL`           | `ws://127.0.0.1:9103/ws/v1`      | gateway-api WS endpoint                                              |
| `--redis URL`                | `redis://127.0.0.1:6379`         | Redis URL used for `XADD`                                            |
| `--operator NAME`            | `admin`                          | operator id for `/auth/token`                                        |
| `--password VALUE`           | `changeme` (or env)              | operator password; falls back to `$SIGNAL_PROBE_PASSWORD`            |
| `--duration-secs N`          | `30`                             | how long to publish synthetic signals                                |
| `--interval-ms N`            | `1000`                           | gap between published signals                                        |
| `--stream NAME`              | `dl.v1.signals.emitted`          | Redis stream to publish to                                           |
| `--warmup-ms N`              | `250`                            | wait after WS connect before publishing (drain initial snapshot)     |
| `--tail-wait-ms N`           | `2000`                           | wait after the last publish before computing the report              |

## Design notes

- **Why publish to `dl.v1.signals.emitted` and not via WatchlistPublisher?**
  The original brief said "inject via `WatchlistPublisher`", but that
  publisher writes to `dl.v1.watchlist.upsert` — which feeds the
  sniper-ingest matcher, not the UI's signals panel. The UI's signals
  panel is fed by `dl.v1.signals.emitted` (decision-core output) via the
  gateway-api bridge. The probe injects directly to that stream so the
  measured latency is the *actual* end-to-end Slow Loop → UI delivery
  path.

- **Why match by `signal_id` instead of timestamp?** The gateway folds
  signals into an `EventStore` keyed by `signal_id`. The same id can
  arrive multiple times (initial snapshot + later patch) — we only count
  the *first* arrival per id, which is the moment the UI would render
  the signal for the first time.

- **Why exit 0 always?** Per the decision log: "Никакого enforcement'а
  через headless-браузеры в CI — это создаст 'flaky' тесты". The probe
  is a measurement tool for humans, not a regression gate. A failing
  Redis or gateway-api should not mask itself as a signal-feed problem
  in CI.

## Layout

```
tools/signal_probe/
├── README.md
├── package.json
├── tsconfig.json
└── src/
    ├── main.ts          orchestrator: auth + WS + publisher + report
    ├── main.test.ts     bun test for parseArgs + buildSignalPayload
    ├── stats.ts         pure percentile + ASCII histogram helpers
    └── stats.test.ts    bun test for stats helpers
```

## CI

`.github/workflows/dual-loop-ci.yml` adds a small `signal-probe` job that
runs `bun typecheck` and `bun test` on every change to `tools/**`. It
does **not** spin up Redis or gateway-api — there is no smoke job. The
probe is a manual instrument; CI only protects the helper code from
breaking.
