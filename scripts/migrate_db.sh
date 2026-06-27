#!/usr/bin/env bash
set -euo pipefail

# Database migration script for Pythia
# Applies all .sql files from sql/migrations/ in order, tracking applied state

DB_PATH="${DB_PATH:-/app/data/pythia.db}"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-/app/sql/migrations}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting database migration..."

if [ ! -f "$DB_PATH" ]; then
  echo "Database not found at $DB_PATH, skipping migration"
  exit 0
fi

# Ensure migration tracking table exists
sqlite3 "$DB_PATH" "CREATE TABLE IF NOT EXISTS _sql_migrations (filename TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"

# Function to check if column exists (legacy support)
column_exists() {
  local table=$1
  local column=$2
  sqlite3 "$DB_PATH" "PRAGMA table_info($table)" | grep -q "^[0-9]*|$column|"
}

# Legacy: add research_status_detail to signals table if missing
if ! column_exists "signals" "research_status_detail"; then
  echo "Adding research_status_detail column to signals table..."
  sqlite3 "$DB_PATH" "ALTER TABLE signals ADD COLUMN research_status_detail TEXT"
  echo "✓ Added research_status_detail column"
else
  echo "✓ research_status_detail column already exists"
fi

# Apply all .sql migration files in order (skip PostgreSQL drizzle-kit files)
if [ -d "$MIGRATIONS_DIR" ]; then
  for sql_file in $(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | sort); do
    filename=$(basename "$sql_file")
    # Skip drizzle-kit PostgreSQL migrations (0000_init is PG, not SQLite)
    if [ "$filename" = "0000_init.sql" ]; then
      echo "✓ Skipping $filename (PostgreSQL drizzle-kit, not SQLite)"
      continue
    fi
    # Skip files with PostgreSQL-specific syntax
    if grep -q "USING btree\|CREATE TYPE\|jsonb\|SERIAL\|::" "$sql_file" 2>/dev/null; then
      echo "✓ Skipping $filename (PostgreSQL syntax detected)"
      continue
    fi
    already=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM _sql_migrations WHERE filename='$filename'")
    if [ "$already" -eq 0 ]; then
      echo "Applying migration: $filename"
      sqlite3 "$DB_PATH" < "$sql_file"
      sqlite3 "$DB_PATH" "INSERT INTO _sql_migrations (filename) VALUES ('$filename')"
      echo "✓ Applied $filename"
    else
      echo "✓ $filename already applied"
    fi
  done
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Database migration completed"
