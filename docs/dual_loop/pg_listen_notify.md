# Postgres LISTEN/NOTIFY publisher (Dual-Loop Phase 1B)

Lightweight push layer on top of the Phase 1 Track D shadow store
(see [`db_migration.md`](./db_migration.md)). AFTER INSERT/UPDATE/DELETE
triggers on six high-signal tables emit a compact JSON envelope via
`pg_notify`; a thin Node subscriber (`src/services/pg_listener.ts`) hands
those events off to `gateway-api` (Track B, future) or any operator CLI
that wants to tail the write stream.

> This publisher is **not** the primary Dual-Loop bus. Redis Streams and
> NATS JetStream (see [`04-bus-protocol.md`](./04-bus-protocol.md)) are
> the durable transports. LISTEN/NOTIFY is best-effort, per-session, and
> non-replayable — use it only for immediate UI uplink where losing an
> event is acceptable because subscribers reconcile via the shadow
> store.

## Architecture

```
                           ┌──────────────────────────────────────┐
   writes  ─────► SQLite   │  Phase 1 Track D shadow adapter       │
  (primary)                │  src/services/db_shadow.ts            │
                           └──────────────────┬────────────────────┘
                                              │   DUAL_LOOP_SHADOW_PG=true
                                              ▼
                           ┌──────────────────────────────────────┐
                           │  Postgres 16 (pythia_dev.public)     │
                           │  AFTER INSERT/UPDATE/DELETE triggers │
                           │  fire dual_loop_*_notify() IF GUC    │
                           │  dual_loop.pg_notify='on' is set.    │
                           └──────────────────┬────────────────────┘
                                              │   pg_notify('dual_loop.X', …)
                                              ▼
                           ┌──────────────────────────────────────┐
                           │  PgListener (postgres.listen())      │
                           │  src/services/pg_listener.ts         │
                           └──────────────────┬────────────────────┘
                                              │
                         ┌────────────────────┴──────────────────────┐
                         │                                           │
              gateway-api (Track B, future)          bun run db:pg:listen (ops CLI)
```

## Channels

One channel per logical entity; names are versioned implicitly through
the envelope `v` field.

| Channel                   | Source table      | Typical consumer use |
|---------------------------|-------------------|----------------------|
| `dual_loop.predictions`   | `predictions`     | Forecast dashboards, brier tables |
| `dual_loop.signals`       | `signals`         | Triage Queue push     |
| `dual_loop.decisions`     | `decisions`       | Audit UI, execution timeline |
| `dual_loop.attention_items` | `attention_items` | Operator Inbox        |
| `dual_loop.watch_dossiers` | `watch_dossiers` | Active Watchlist      |
| `dual_loop.watch_updates` | `watch_updates`   | Dossier timeline      |

## Event envelope

```json
{
  "v": 1,
  "op": "insert" | "update" | "delete",
  "table": "predictions",
  "pk": { "id": 42 },
  "market_id": "polymarket:0x…",
  "ts": "2026-04-22T12:34:56.789Z"
}
```

Rules:

- Payload stays well under Postgres' **8000-byte** NOTIFY limit by
  design — no column values, just primary key + optional `market_id`.
  Subscribers re-read the row from Postgres when they need the body.
- `pk` always maps to a stable unique identifier (`id` for serial PKs;
  `{id, watch_dossier_id}` for `watch_updates`).
- `ts` is produced inside the trigger using `now() AT TIME ZONE 'utc'`
  and matches the ISO-8601 format the rest of Pythia emits.
- Additions are backward-compatible; a breaking change must bump `v`
  and add a parallel channel (same rule as `04-bus-protocol.md`).

## Feature flag

The publisher is **off by default**. Two independent switches must be
flipped to get events:

1. **Writer side** — environment variable `DUAL_LOOP_PG_NOTIFY=true`
   causes `src/services/db_pg.ts` to open every backend with startup
   parameter `dual_loop.pg_notify=on`. Without this GUC the trigger
   function short-circuits (`current_setting('dual_loop.pg_notify',
   true) <> 'on'` → RETURN).

2. **Schema side** — `sql/migrations/0001_listen_notify.sql` must be
   applied. The Phase 1 Track D CI job (`shadow-pg-tests`) runs every
   migration under `sql/migrations/` so this lands alongside the Phase 1
   schema.

For a permanent "always on" staging deployment:

```sql
ALTER DATABASE pythia_dev SET dual_loop.pg_notify = 'on';
```

## Operator runbook

### Tail events in a shell

```bash
export DUAL_LOOP_PG_URL=postgres://pythia:…@staging-pg:5432/pythia
bun run db:pg:listen
```

Each event is printed as a single JSON line to stdout:

```
{"channel":"dual_loop.predictions","event":{"v":1,"op":"insert","table":"predictions","pk":{"id":42},"market_id":"polymarket:0xabc…","ts":"2026-04-22T12:34:56.789Z"}}
```

SIGINT/SIGTERM cleanly unlistens and closes the session.

### Re-use the listener from application code

```ts
import { PgListener } from './src/services/pg_listener';

const listener = new PgListener({ url: process.env.DUAL_LOOP_PG_URL! });
listener.on(async (event, channel) => {
  if (event.table !== 'predictions') return;
  // Hot-path subscribers typically re-fetch the row:
  const row = await fetchPrediction(event.pk.id);
  emitToUI(row);
});
await listener.start();
```

`PgListener` pins `max: 1` to keep LISTEN on a single backend (rotating
over a pool would drop notifications) and surfaces decode errors through
the `onError` hook.

## Failure modes and mitigations

| Failure                                   | Detection                      | Mitigation |
|-------------------------------------------|--------------------------------|------------|
| Connection drops silently                 | No events for N minutes        | Subscriber restarts the listener and performs a reconciling `SELECT` against the shadow store (best-effort NOTIFY is documented). |
| Writer forgets `DUAL_LOOP_PG_NOTIFY=true` | GUC check in trigger → no-op   | Log NOTIFY counter per channel; alert when the writer is reporting shadow writes but the listener sees zero events. |
| Payload > 8000 bytes                      | `NotifyAsync` protocol error   | By construction we never put row bodies in the envelope; the only safety guard is `market_id` being a bounded string. |
| Schema drift (new tables not triggered)   | Missing events for new feature | Follow-up migration to add the trigger; the `DUAL_LOOP_CHANNELS` constant in `pg_listener.ts` is the canonical channel list. |

## Relationship to the 7-day parity window

This publisher does **not** touch the shadow-write contract. With the
flag off, Phase 1 Track D behaviour is unchanged. With the flag on,
additional NOTIFY traffic flows to subscribers but the SQLite primary
continues to own the write path — Phase 2 cutover is unaffected.
