#!/usr/bin/env bash
# Apply drizzle-generated Postgres migrations for the Pythia shadow store.
#
# Used during Phase 1 (Dual-Loop Track D). Reads connection URL from
# DUAL_LOOP_PG_URL / DATABASE_URL, falling back to the dev compose default.
#
# Typical dev loop:
#   make dev-up
#   bash scripts/pg_migrate.sh
#
# Production:
#   DUAL_LOOP_PG_URL=postgres://... bash scripts/pg_migrate.sh
set -euo pipefail

cd "$(dirname "$0")/.."

export DUAL_LOOP_PG_URL="${DUAL_LOOP_PG_URL:-${DATABASE_URL:-postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev}}"

echo "[pg_migrate] applying drizzle migrations to ${DUAL_LOOP_PG_URL%%\?*}"

if command -v bun >/dev/null 2>&1; then
  bun x drizzle-kit migrate
else
  npx --yes drizzle-kit migrate
fi

echo "[pg_migrate] done."
