#!/bin/bash
set -euo pipefail

export PATH="/root/.bun/bin:$PATH"
# Ensure `from python.*` imports resolve. Cron jobs invoke scripts as
# `python python/foo.py`, which puts /app/python on sys.path but not /app
# itself, so any `from python.bar import …` fails with ModuleNotFoundError.
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/app"
export DB_PATH="${DB_PATH:-/app/data/pythia.db}"
export LOG_DIR="${LOG_DIR:-/app/logs}"

mkdir -p /app/data "$LOG_DIR"

# Run database migrations
if [ -f /app/scripts/migrate_db.sh ]; then
  /app/scripts/migrate_db.sh
fi

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

  oracle-publish)
    cd /app
    /app/python/venv/bin/python python/oracle_publisher.py 2>&1 | tee -a "$LOG_DIR/oracle_publish_${TODAY}.log"
    ;;

  oracle-update-scores)
    cd /app
    /app/python/venv/bin/python python/oracle_score_updater.py 2>&1 | tee -a "$LOG_DIR/oracle_scores_${TODAY}.log"
    ;;

  oracle-scorecard)
    cd /app
    /app/python/venv/bin/python python/oracle_scorecard.py 2>&1 | tee -a "$LOG_DIR/oracle_scorecard_${TODAY}.log"
    ;;

  llm-stats)
    cd /app
    /app/python/venv/bin/python python/llm_stats.py --json 2>&1 | tee -a "$LOG_DIR/llm_stats_${TODAY}.log"
    ;;
  stats-bot)
    cd /app
    exec /app/python/venv/bin/python python/telegram_stats_bot.py
    ;;

  oracle-backfill)
    cd /app
    # ONE-TIME command: backfill archive
    /app/python/venv/bin/python python/oracle_backfill.py --limit 25 --sleep 3 2>&1 | tee -a "$LOG_DIR/oracle_backfill_${TODAY}.log"
    ;;

  llm-stats-publish)
    cd /app
    /app/python/venv/bin/python python/llm_stats_publisher.py 2>&1 | tee -a "$LOG_DIR/llm_stats_publish_${TODAY}.log"
    ;;
  portfolio-update)
    cd /app
    /app/python/venv/bin/python python/portfolio_manager.py --update-pinned 2>&1 | tee -a "$LOG_DIR/portfolio_${TODAY}.log"
    ;;

  dashboard)
    cd /app
    exec /app/python/venv/bin/python -m uvicorn python.dashboard_api:app --host 0.0.0.0 --port 8080
    ;;

  scheduler)
    exec supercronic -json -no-reap /app/supercronic.crontab
    ;;

  shell)
    exec /bin/bash
    ;;

  *)
    exec "$@"
    ;;
esac
