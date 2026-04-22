# DB migration: SQLite → PostgreSQL (shadow-write) — Phase 1 Track D

Pythia's primary store is SQLite (`data/pythia.db`). The Dual-Loop
architecture needs concurrent writers, logical replication, and
`LISTEN/NOTIFY` to push state into the `gateway-api` (see
[01-containers.md](./01-containers.md) C3 and C10). That requires
PostgreSQL 16.

This document is the operator runbook for the shadow-write migration
landed in Phase 1 Track D. The actual cutover (primary → PG) is
[Phase 2](./06-roadmap.md). Shadow-write exists so we can observe 7 days
of real-traffic parity before flipping.

Risk reference: [07-risks.md R13 — "data divergence"](./07-risks.md).

## Architecture at a glance

```
                     ┌──────────────┐
  write API call ──▶ │ db_shadow.ts │──▶ SQLite (primary, synchronous)
                     │   (wrapper)  │
                     │              │──▶ Postgres (shadow, fire-and-forget)
                     └──────────────┘             via db_pg.ts (drizzle-orm)

            Reads  ──────────────────▶ SQLite only (Phase 1)
```

- Primary writes are unchanged, synchronous, transactional.
- Shadow writes go through `Promise.allSettled` so a Postgres failure
  never takes down the primary loop.
- Errors surface as NDJSON lines in `logs/shadow_pg_errors.log` plus an
  in-process counter `shadow_pg_errors_total{operation=…}` exported
  through `getShadowPgMetrics()` (wired into Prometheus in a later PR).

### Feature flag

| env var | default | effect |
| --- | --- | --- |
| `DUAL_LOOP_SHADOW_PG` | `false` | `db_shadow.ts` is a pure re-export of `db.ts`. Zero-impact. |
| `DUAL_LOOP_SHADOW_PG=true` | — | Shadow fan-out enabled. PG must be reachable at `DUAL_LOOP_PG_URL`. |
| `DUAL_LOOP_PG_URL` | `postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev` | Shadow connection string (falls back to `DATABASE_URL`). |

Reads always stay on SQLite in Phase 1.

### Files

- `src/db/pg/schema.ts` — drizzle-orm schema (single source of truth for
  the PG side; mirrors `src/services/db.ts` + `sql/*.sql`).
- `sql/migrations/0000_init.sql` — `drizzle-kit generate` output.
- `src/services/db_pg.ts` — connection pool (`postgres-js`, max 10) +
  write/read API.
- `src/services/db_shadow.ts` — user-facing wrapper. Drop-in for
  `src/services/db.ts`.
- `src/services/db_parity.ts` — sample/full-scan parity diff logger +
  CLI (`bun run db:parity`).
- `scripts/pg_migrate.sh` / `scripts/pg_reset.sh` — operator helpers.
- `scripts/pg_initial_load.ts` — one-time SQLite → PG bulk load.
- `python/db_pg_shadow.py` — minimal Python-side helper (see
  [Python side scope](#python-side-scope)).

## Operator runbook

### 1. Prepare Postgres

```sh
make dev-up                          # starts postgres + NATS + Redis + observability
bash scripts/pg_migrate.sh           # applies sql/migrations/0000_init.sql
```

Run `bun run db:pg:push` instead of `pg_migrate.sh` if you'd like to
skip migration files during tight local iteration.

### 2. Initial load from SQLite

```sh
# READ: data/pythia.db  WRITE: $DUAL_LOOP_PG_URL/predictions
bun run db:pg:initial-load
```

The script upserts every row of `predictions` from SQLite into Postgres.
Other tables are populated live as the primary writes them during the
shadow window — they do not need to be back-filled for the parity check.

### 3. Enable shadow writes

```sh
export DUAL_LOOP_SHADOW_PG=true
bun start                            # or the unit you normally use
```

When the flag is on the start-up log will include a `pingPg` attempt.
If PG is unreachable `shadow_pg_errors_total{operation=ping}` increments
and the service continues on SQLite only.

### 4. Monitor parity (every hour)

The parity CLI samples 100 random `predictions` rows from the primary,
fetches the matching rows from the shadow, and diff's the scalar
columns:

```sh
bun run db:parity
# → PARITY OK {table=predictions,row_count=100}
# or
# → PARITY DIVERGE {table=predictions,market_id=…,field=dexter_p,primary=…,shadow=…}
```

Schedule via cron / systemd timer in your deployment of choice.

### 5. Weekly full audit

```sh
bun run db:parity -- --full
# Logs PARITY OK or PARITY DIVERGE with counts of matches, diverges,
# missing_shadow (present only in primary), and missing_primary
# (present only in shadow).
```

The full scan is O(rows) — expect minutes on production data.

### 6. DoD for cutover (Phase 2 ticket, not this PR)

Before flipping reads to PG the roadmap expects:

- 7 consecutive days of `shadow_pg_errors_total == 0` for `insertPrediction`
  and `updateOutcome`.
- Zero `PARITY DIVERGE` lines on critical scalar fields for 7 days.
- `scripts/pg_initial_load.ts` reconciled: `COUNT(*) FROM predictions`
  equal on both stores.
- `gateway-api` has consumed `LISTEN/NOTIFY` smoke in staging.

### 7. Rollback

- **Flip the flag**: `unset DUAL_LOOP_SHADOW_PG` and restart. SQLite is
  untouched.
- **Blow away the shadow schema** (dev only): `bash scripts/pg_reset.sh`
  then `bash scripts/pg_migrate.sh` to recreate it. The script refuses
  to run against anything other than the dev URL unless
  `DUAL_LOOP_ALLOW_PG_RESET=1`.

## Python side scope

The TS adapter covers the `predictions` write path plus the full
schema. The three Python stores (`python/decision_store.py`,
`python/watchlist_store.py`, `python/attention_store.py`) write only to
SQLite in Phase 1.

A minimal helper (`python/db_pg_shadow.py`) exists so Python code can
opportunistically mirror rows (today only the resolution-checker
`updateOutcome`). It is fire-and-forget and requires `psycopg` or
`psycopg2`. If neither driver is installed the module logs a single
warning and becomes a no-op.

Full Python-side mirroring is **deferred** because:

1. Each Python store rewrites the same tables the TS side already
   touches (`signals`, `decisions`, `positions`). Having two shadow
   paths converge on the same PG rows doubles the surface for races we
   must test during the 7-day window.
2. The Python stores are mid-refactor toward a single FastAPI
   `gateway-api` service in Phase 1/2. Shadow-writing from
   soon-to-be-deleted code is wasted effort.
3. Parity via `bun run db:parity` already observes rows regardless of
   which stack produced them, because it compares the stored values
   directly.

Mitigation updates are recorded in [07-risks.md R13](./07-risks.md).
Once the FastAPI gateway consolidates writes, the TS + Python
shadow-write paths collapse into one.

## CI

A new GitHub Actions job `shadow-pg-tests` runs on every PR that
touches SQL, scripts, or the new services:

- Brings up a `postgres:16-alpine` service.
- Verifies `drizzle-kit generate` is in sync with `src/db/pg/schema.ts`
  (fails if someone edits the schema without regenerating migrations).
- Applies `sql/migrations/*` via `scripts/pg_migrate.sh`.
- Runs `bun test` for `db_pg.test.ts`, `db_shadow.test.ts`, and
  `db_parity.test.ts`.
- Executes `bun run db:parity` against an empty shadow to assert the
  `PARITY OK` path.

Tests that require Postgres skip themselves when `DUAL_LOOP_PG_TEST_URL`
(or `DUAL_LOOP_PG_URL`) is empty, so laptop-only `bun test` runs stay
fast.
