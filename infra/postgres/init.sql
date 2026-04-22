-- Dual-Loop dev database init.
-- Эта схема — placeholder для Phase 1 (миграция SQLite → Postgres).
-- Реальные таблицы будут добавлены через drizzle-kit миграции.

-- Базовые extensions, полезные для Dual-Loop.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";      -- UUID generation
CREATE EXTENSION IF NOT EXISTS "btree_gin";     -- индексы по JSONB

-- Отдельная схема для Dual-Loop, чтобы не мешать будущей миграции из SQLite.
CREATE SCHEMA IF NOT EXISTS dual_loop;

COMMENT ON SCHEMA dual_loop IS
  'Dual-Loop architecture tables. See docs/dual_loop/ for details.';

-- Health-check таблица для проверки что БД поднята.
CREATE TABLE IF NOT EXISTS dual_loop._health (
    id SERIAL PRIMARY KEY,
    booted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note TEXT
);

INSERT INTO dual_loop._health (note)
VALUES ('initial boot');
