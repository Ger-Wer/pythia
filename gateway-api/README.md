# gateway-api

Phase 1 / Track B component of the **Dual-Loop architecture**.

`gateway-api` is the only service that talks to the Command Center UI. It bridges the internal messaging bus (Redis Streams for the control plane, NATS JetStream for telemetry) and the browser via **WSS** + **REST**.

Design references:

- [`docs/dual_loop/01-containers.md`](../docs/dual_loop/01-containers.md) — C5 container definition.
- [`docs/dual_loop/04-bus-protocol.md`](../docs/dual_loop/04-bus-protocol.md) — stream names, payload shapes, idempotency.
- [`docs/dual_loop/05-command-center-ui.md`](../docs/dual_loop/05-command-center-ui.md) — WSS protocol details.
- [`docs/dual_loop/06-roadmap.md`](../docs/dual_loop/06-roadmap.md) — Phase 1 DoD.

## Quickstart

### With docker-compose

```bash
make dev-up          # brings up redis, nats, grafana, gateway-api, …
curl http://127.0.0.1:9103/api/v1/healthz
curl http://127.0.0.1:9103/metrics | grep gateway_
```

Gateway listens on `127.0.0.1:9103`. In the default dev compose stack the hardcoded operator is `admin` / `changeme` (bcrypt hash baked into `docker-compose.dev.yml`). **Change these before any non-local deployment.**

For live Dual-Loop projection you also need the root repository TS protobuf clients generated into `src/generated/`. `gateway-api` can still boot without them, but then the bus bridge falls back to JSON-only behaviour and will not decode protobuf Redis payloads from the Rust/TS writers.

### Locally with Bun

```bash
cd ..
bun install
bun run proto:gen
cd gateway-api
bun install
export GATEWAY_PASETO_KEY="$(openssl rand -base64 32)"
export OPERATOR_PASSWORD_HASH='$2a$10$bR/.j3ziz5BC2vE4dETKLuX8AVPYFY0o5pRRnN.NXj29u9EcAkk1y'  # "changeme"
export OPERATOR_ID=admin
export KILL_HMAC_SECRET="dev-kill-hmac"
export REDIS_URL=redis://127.0.0.1:6379
export NATS_URL=nats://127.0.0.1:4222
bun run dev
```

### Running tests

```bash
bun install
bun run lint   # tsc --noEmit
bun test       # rest + ws + bus bridge suites (in-memory bus, ioredis-mock)
bun run build  # bundles to dist/
```

The test suite is hermetic — no Docker required.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `HOST` | no | `0.0.0.0` | Listen address |
| `PORT` | no | `9103` | Listen port |
| `LOG_LEVEL` | no | `info` | `trace`/`debug`/`info`/`warn`/`error` |
| `REDIS_URL` | yes | `redis://127.0.0.1:6379` | Redis 7 used for Streams + state snapshot + idempotency + audit |
| `NATS_URL` | yes | `nats://127.0.0.1:4222` | NATS JetStream used for telemetry (soft dependency) |
| `GATEWAY_PASETO_KEY` | yes | — | 32-byte base64 key for PASETO v4.local. Generate with `openssl rand -base64 32` |
| `OPERATOR_ID` | no | `admin` | Hardcoded MVP operator username |
| `OPERATOR_PASSWORD_HASH` | yes | — | bcrypt hash of the operator password |
| `KILL_HMAC_SECRET` | yes | — | Shared secret for verifying kill-switch client signatures |
| `TOKEN_TTL_SECONDS` | no | `28800` (8 h) | PASETO token lifetime |
| `RATE_LIMIT_PER_SECOND` | no | `100` | Per-operator REST rate-limit |
| `WS_MAX_BUFFERED_PATCHES` | no | `64` | Back-pressure threshold — when exceeded, client is force-resynced with a fresh snapshot |
| `WS_HEARTBEAT_INTERVAL_MS` | no | `5000` | Server ping cadence |
| `WS_SNAPSHOT_INTERVAL_MS` | no | `30000` | Periodic full snapshot |
| `STATE_SNAPSHOT_INTERVAL_MS` | no | `30000` | Periodic Redis persistence of in-memory state |
| `KILL_ACK_TIMEOUT_MS` | no | `2000` | How long to wait for an executor ack on kill |
| `IDEMPOTENCY_TTL_SECONDS` | no | `86400` (24 h) | Idempotency key retention |

## Protocol reference

### REST

All `/api/v1/*` endpoints except `/auth/token` and `/healthz` require an
`Authorization: Bearer <paseto>` header.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/token` | `{operator_id, password}` | `{token, operator_id, exp_unix_ms}` |
| `GET` | `/api/v1/healthz` | — | `{status, deps}` · 503 if Redis unreachable |
| `GET` | `/metrics` | — | Prometheus exposition |
| `GET` | `/api/v1/state/snapshot` | — | `{server_version, data: {signals, watchlist, triggers, exec_status, kill}}` |
| `POST` | `/api/v1/signals/:id/accept` | `{operator_id, idempotency_key, expected_version}` | `202 {decision_id, action:"ACCEPT"}` |
| `POST` | `/api/v1/signals/:id/reject` | `{..., reason}` | `202 {decision_id, action:"REJECT"}` |
| `POST` | `/api/v1/signals/:id/snooze` | `{..., snooze_until_unix_ms}` | `202 {decision_id, action:"SNOOZE"}` |
| `POST` | `/api/v1/control/kill` | `{operator_id, scope, market_id?, nonce, issued_at_unix_ms, signature, idempotency_key}` | `202 {command_id, scope, ack_status, ack_detail}` |

**Errors:**

- `401 unauthorized` — missing/invalid token, or wrong credentials.
- `403 operator_id_mismatch` — body operator_id ≠ token subject.
- `404 signal_not_found`.
- `409 version_conflict` — `expected_version` ≠ current `server_version`. Body includes `actual_version`.
- `400 stale_request` — kill command outside the ±5 s clock skew window.
- `429` — rate-limit exceeded.

### Kill-switch signature

```
signature = HMAC_SHA256(
  KILL_HMAC_SECRET,
  operator_id | scope | market_id | nonce | issued_at_unix_ms
).hex()
```

(`|` = literal `|` separator.) The gateway re-signs the envelope with its own `message_id` before publishing, so downstream executors must re-verify using the server-side signature.

### WebSocket `/ws/v1`

Auth: `?token=<paseto>` query parameter (preferred) or `Sec-WebSocket-Protocol: paseto, <token>`.

Server → client:

| `kind` | Fields |
| --- | --- |
| `snapshot` | `server_version`, `topics`, `data` (only subscribed topics). Sent on connect, on `subscribe`, and every `WS_SNAPSHOT_INTERVAL_MS` |
| `patch` | `server_version`, `topic`, `patch` (RFC 6902 ops). One frame per mutation |
| `ping` | `ts` — client must reply with `{kind:"pong", ts}` |
| `error` | `code`, `message` |

Client → server:

| `kind` | Fields |
| --- | --- |
| `subscribe` | `topics: ["signals"\|"watchlist"\|"triggers"\|"exec_status"]` |
| `unsubscribe` | `topics: [...]` |
| `pong` | `ts` |

Close codes:

- `4401` — missing/invalid token.
- `4408` — heartbeat timeout (2 consecutive missed pongs).

## Architecture

```
Redis Streams (dl.v1.*) ──┐
                          ├──▶ EventStore (in-memory, JSON Patch)
NATS JetStream (telemetry)┘                │
                                           ▼
                                   ┌────────────────┐
                            ─── WSS │  gateway-api   │ REST ───
                                   └────────────────┘
                                           ▲
                                           │
                                   Redis (state snapshot,
                                   idempotency, audit log)
```

### Bus bridge

Current behaviour on `main`:

- inbound streams may arrive either as raw protobuf bytes in `payload` or as legacy JSON envelopes;
- the bridge prefers protobuf decode for `signals/watchlist/triggers/exec/kill_ack`;
- tests and local mocks still use JSON payload objects;
- gateway publish-side for `dl.v1.signals.decisions` and `dl.v1.control.kill` is still a JSON compatibility envelope.

Consumer group: **`gateway`** on every subscribed stream:

- `dl.v1.signals.emitted` → `state.signals[signal_id]`
- `dl.v1.watchlist.upsert` / `dl.v1.watchlist.remove` → `state.watchlist[market_id]`
- `dl.v1.triggers.fired` → `state.triggers[trigger_id]`
- `dl.v1.exec.submitted` / `dl.v1.exec.filled` / `dl.v1.exec.reverted` → `state.exec_status[tx_hash]`
- `dl.v1.control.kill_ack` → `state.kill` + resolves any pending kill-command waiters.

Publishes:

- `dl.v1.signals.decisions` — after a successful `POST /signals/:id/{accept,reject,snooze}`.
- `dl.v1.control.kill` — after a successful `POST /control/kill`.

`XAUTOCLAIM` runs every 10 s to reclaim pending entries from dead consumers.

### EventStore

In-memory state projection. Every mutation is applied through `apply(topic, mutator)`, which:

1. Computes an RFC 6902 JSON Patch diff of the touched topic-slice.
2. Bumps `server_version`.
3. Fans out the patch to WS subscribers.
4. Persists the full state to Redis key `gateway:state` every `STATE_SNAPSHOT_INTERVAL_MS` (restored on boot — purely an optimization; WS clients always receive a full snapshot on connect).

### Audit log

Append-only Redis lists, trimmed to 100 000 entries:

- `gateway:audit:decision`
- `gateway:audit:kill`
- `gateway:audit:auth`

Each entry is a JSON object with `ts_unix_ms`, `kind`, `operator_id`, `action`, and a `detail` object.

### Idempotency

Per-`(operator_id, idempotency_key)`. The gateway uses `SET NX PX` to reserve the slot, then stores the full response envelope once complete. Replays return the cached status + body verbatim. When Redis is unavailable the service falls back to an in-memory map (not survivable across restarts — operators will see the command re-execute).

## Metrics

Prefix: `gateway_`. Non-exhaustive list:

- `gateway_http_requests_total{method,route,status}`
- `gateway_http_request_duration_seconds_bucket{…}`
- `gateway_ws_connections_active`
- `gateway_ws_messages_sent_total{kind}` / `gateway_ws_messages_received_total{kind}`
- `gateway_ws_disconnects_total{reason}`
- `gateway_bus_messages_consumed_total{stream}` / `gateway_bus_consume_errors_total{stream}`
- `gateway_nats_telemetry_messages_total{subject}`
- `gateway_decisions_published_total{action}`
- `gateway_kill_commands_total{scope,status}`
- `gateway_idempotency_replays_total`
- `gateway_state_server_version`

## TODO / Phase 3 upgrades

- Replace hardcoded single-operator with OIDC / SSO.
- Move `GATEWAY_PASETO_KEY` and `KILL_HMAC_SECRET` into a real KMS/HSM.
- Add signed snapshot checkpoints (server_version cryptographic chain).
- Add per-topic fine-grained rate limits for WS clients.
