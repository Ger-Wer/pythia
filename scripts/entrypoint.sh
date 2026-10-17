#!/bin/bash
# Pythia v2 entrypoint. All execution and tests belong on amper.
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
    python3 python/evaluation/live_tracker.py 2>&1 | tee -a "$LOG_DIR/tracker.log"
    ;;
  analyze)
    cd /app
    python3 python/evaluation/analyze_snapshots.py 2>&1 | tee -a "$LOG_DIR/analysis.log"
    ;;
  healthcheck)
    cd /app
    python3 python/evaluation/healthcheck.py 2>&1 | tee -a "$LOG_DIR/healthcheck.log"
    ;;
  paper-cycle)
    if [ "${PYTHIA_PAPER_ENABLED:-0}" != "1" ]; then
      echo "Paper cycle disabled; no forecasts, fills or channel posts"
      exit 0
    fi
    cd /app
    python3 -m python.evaluation.paper_cycle --send
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    echo "Unknown command: $1" >&2
    echo "Usage: entrypoint.sh [scheduler|snapshot|analyze|healthcheck|paper-cycle|shell]" >&2
    exit 1
    ;;
esac
