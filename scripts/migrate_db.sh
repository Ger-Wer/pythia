#!/usr/bin/env bash
set -euo pipefail

# Database migration script for Pythia
# Adds missing columns to existing tables

DB_PATH="${DB_PATH:-/app/data/pythia.db}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting database migration..."

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
  echo "Database not found at $DB_PATH, skipping migration"
  exit 0
fi

# Function to check if column exists
column_exists() {
  local table=$1
  local column=$2
  sqlite3 "$DB_PATH" "PRAGMA table_info($table)" | grep -q "^[0-9]*|$column|"
}

# Add research_status_detail to signals table if missing
if ! column_exists "signals" "research_status_detail"; then
  echo "Adding research_status_detail column to signals table..."
  sqlite3 "$DB_PATH" "ALTER TABLE signals ADD COLUMN research_status_detail TEXT"
  echo "✓ Added research_status_detail column"
else
  echo "✓ research_status_detail column already exists"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Database migration completed"
