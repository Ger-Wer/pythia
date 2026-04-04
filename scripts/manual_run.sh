#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCHEDULER_SERVICE="pythia-scheduler"
TELEGRAM_SERVICE="pythia-telegram"
DASHBOARD_SERVICE="pythia-dashboard"
SERVICE="$SCHEDULER_SERVICE"
ENTRYPOINT="/app/scripts/entrypoint.sh"
COMMAND="${1:-help}"

usage() {
  cat <<'EOF'
Pythia Manual Runner

Usage: bash scripts/manual_run.sh <command>

Commands:
  forecast  - run forecast manually
  resolve   - run resolution checker
  weekly    - generate weekly report
  edge      - calculate edge and Brier scores
  db        - open SQLite shell
  logs      - tail scheduler and telegram logs
  dashboard-logs - tail dashboard logs
  shell     - open shell in the container
  status    - show compose and database status for all services
  help      - show this message
EOF
}

resolve_db_path() {
  if [ -f .env ]; then
    awk -F= '
      /^[[:space:]]*DB_PATH=/ {
        sub(/^[[:space:]]*DB_PATH=[[:space:]]*/, "", $0)
        print
        exit
      }
    ' .env
  fi
}

DB_PATH="$(resolve_db_path)"
DB_PATH="${DB_PATH:-/app/data/pythia.db}"

case "$COMMAND" in
  forecast)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" forecast
    ;;

  resolve)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" resolve
    ;;

  weekly)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" weekly
    ;;

  edge)
    docker compose run --rm --entrypoint /app/python/venv/bin/python "$SERVICE" /app/python/edge_calculator.py
    ;;

  db)
    docker compose run --rm --entrypoint sqlite3 "$SERVICE" "$DB_PATH"
    ;;

  logs)
    docker compose logs -f --tail=100 "$SCHEDULER_SERVICE" "$TELEGRAM_SERVICE" "$DASHBOARD_SERVICE"
    ;;

  dashboard-logs)
    docker compose logs -f --tail=100 "$DASHBOARD_SERVICE"
    ;;

  shell)
    docker compose run --rm --entrypoint "$ENTRYPOINT" "$SERVICE" shell
    ;;

  status)
    docker compose ps
    echo ""
    echo "=== Forecast Summary ==="
    docker compose run --rm --entrypoint sqlite3 "$SERVICE" "$DB_PATH" \
      "SELECT date(forecast_timestamp) AS day, COUNT(*) AS predictions, ROUND(COALESCE(SUM(api_cost_usd), 0), 4) AS cost FROM predictions GROUP BY day ORDER BY day DESC LIMIT 5;"
    echo ""
    echo "=== Resolution Summary ==="
    docker compose run --rm --entrypoint sqlite3 "$SERVICE" "$DB_PATH" \
      "SELECT COUNT(*) AS total, SUM(CASE WHEN outcome IS NOT NULL AND outcome IN (0,1) THEN 1 ELSE 0 END) AS resolved FROM predictions;"
    ;;

  help)
    usage
    ;;

  *)
    echo "Unknown command: $COMMAND" >&2
    echo "" >&2
    usage >&2
    exit 1
    ;;
esac
