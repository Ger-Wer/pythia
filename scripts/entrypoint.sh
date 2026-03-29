#!/bin/bash
set -euo pipefail

export PATH="/root/.bun/bin:$PATH"
export DB_PATH="${DB_PATH:-/app/data/pythia.db}"
export LOG_DIR="${LOG_DIR:-/app/logs}"

mkdir -p /app/data "$LOG_DIR"

TODAY="$(date +%Y%m%d)"

case "${1:-}" in
  forecast)
    cd /app
    bun run src/main.ts 2>&1 | tee -a "$LOG_DIR/forecast_${TODAY}.log"
    ;;

  resolve)
    cd /app
    /app/python/venv/bin/python python/resolution_checker.py 2>&1 | tee -a "$LOG_DIR/resolution_${TODAY}.log"
    ;;

  weekly)
    cd /app
    {
      /app/python/venv/bin/python python/analyze.py
      /app/python/venv/bin/python python/telegram_bot.py --weekly-report
    } 2>&1 | tee -a "$LOG_DIR/weekly_${TODAY}.log"
    ;;

  scheduler)
    exec supercronic -json /app/supercronic.crontab
    ;;

  shell)
    exec /bin/bash
    ;;

  *)
    exec "$@"
    ;;
esac
