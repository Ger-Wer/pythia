#!/bin/bash
# Pythia v2 entrypoint. Modes: scheduler (default), snapshot, analyze, healthcheck.
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-/app}"
export LOG_DIR="${LOG_DIR:-/app/logs}"
mkdir -p "$LOG_DIR" /app/data 2>/dev/null || true

case "${1:-scheduler}" in
  scheduler)
    exec supercronic -json -no-reap /app/supercronic.crontab
    ;;
  snapshot)
    cd /app
    python3 python/backtest/live_tracker.py 2>&1 | tee -a "$LOG_DIR/tracker.log"
    ;;
  analyze)
    cd /app
    python3 python/backtest/analyze_snapshots.py 2>&1 | tee -a "$LOG_DIR/analysis.log"
    ;;
  healthcheck)
    cd /app
    python3 python/backtest/healthcheck.py 2>&1 | tee -a "$LOG_DIR/healthcheck.log"
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    echo "Unknown command: $1" >&2
    echo "Usage: entrypoint.sh [scheduler|snapshot|analyze|healthcheck|shell]" >&2
    exit 1
    ;;
esac
