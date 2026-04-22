#!/usr/bin/env bash
# Drop & recreate the Pythia Postgres shadow schema (dev only).
#
# Used during Phase 1 (Dual-Loop Track D) when shadow state diverges from
# expectations and operator wants a clean slate. SQLite is NEVER touched —
# PG is shadow-only.
#
# USAGE: bash scripts/pg_reset.sh
#
# Refuses to run against anything other than the dev default unless
# DUAL_LOOP_ALLOW_PG_RESET=1.
set -euo pipefail

cd "$(dirname "$0")/.."

PG_URL="${DUAL_LOOP_PG_URL:-${DATABASE_URL:-postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev}}"

if [[ "$PG_URL" != *"pythia_dev_password"* && "${DUAL_LOOP_ALLOW_PG_RESET:-0}" != "1" ]]; then
  echo "[pg_reset] Refusing to drop schema on non-dev URL: $PG_URL" >&2
  echo "[pg_reset] Set DUAL_LOOP_ALLOW_PG_RESET=1 to override."      >&2
  exit 2
fi

echo "[pg_reset] dropping + recreating public schema on ${PG_URL%%\?*}"

# Use psql if available; otherwise fall back to a tiny node shim through bun.
if command -v psql >/dev/null 2>&1; then
  psql "$PG_URL" -v ON_ERROR_STOP=1 <<'SQL'
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO PUBLIC;
SQL
else
  bun <<'TS'
import postgres from 'postgres';
const url = process.env.DUAL_LOOP_PG_URL ?? process.env.DATABASE_URL ?? 'postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev';
const sql = postgres(url, { max: 1 });
try {
  await sql.unsafe('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO PUBLIC;');
} finally {
  await sql.end({ timeout: 5 });
}
TS
fi

echo "[pg_reset] done. Re-apply schema via: bash scripts/pg_migrate.sh"
