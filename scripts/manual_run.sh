#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVICE="pythia-scheduler"
ENTRYPOINT="/app/scripts/entrypoint.sh"
DB_PATH="${DB_PATH:-/app/data/pythia.db}"
COMMAND="${1:-help}"

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
    docker compose logs -f --tail=100 "$SERVICE"
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
    cat <<'EOF'
Pythia Manual Runner

Usage: bash scripts/manual_run.sh <command>

Commands:
  forecast  - run forecast manually
  resolve   - run resolution checker
  weekly    - generate weekly report
  edge      - calculate edge and Brier scores
  db        - open SQLite shell
  logs      - tail scheduler logs
  shell     - open shell in the container
  status    - show compose and database status
  help      - show this message
EOF
    ;;

  *)
    echo "Unknown command: $COMMAND" >&2
    echo "" >&2
    cat <<'EOF' >&2
Pythia Manual Runner

Usage: bash scripts/manual_run.sh <command>

Commands:
  forecast  - run forecast manually
  resolve   - run resolution checker
  weekly    - generate weekly report
  edge      - calculate edge and Brier scores
  db        - open SQLite shell
  logs      - tail scheduler logs
  shell     - open shell in the container
  status    - show compose and database status
  help      - show this message
EOF
    exit 1
    ;;
esac
