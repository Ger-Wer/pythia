-- Oracle Prophecies Table
-- Purpose: Track all public predictions published to Telegram Oracle channel
-- Created: 2026-06-26 for Oracle Public Channel feature

CREATE TABLE IF NOT EXISTS oracle_prophecies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  
  -- Market identification
  market_id TEXT NOT NULL,
  market_question TEXT NOT NULL,
  market_category TEXT,
  market_closes_at TEXT NOT NULL,           -- ISO timestamp
  
  -- Link to existing data
  signal_id INTEGER,                        -- FK to signals.id (if exists)
  prediction_id INTEGER,                    -- FK to predictions.id
  
  -- Oracle prediction
  bet_direction TEXT NOT NULL CHECK(bet_direction IN ('YES', 'NO')),
  pythia_prob REAL NOT NULL CHECK(pythia_prob >= 0 AND pythia_prob <= 1),
  polymarket_prob REAL NOT NULL CHECK(polymarket_prob >= 0 AND polymarket_prob <= 1),
  edge REAL NOT NULL,                       -- pythia_prob - polymarket_prob
  confidence_level INTEGER NOT NULL CHECK(confidence_level BETWEEN 1 AND 5),
  
  -- LLM-generated content
  oracle_reasoning TEXT NOT NULL,           -- 2-3 sentences for public post
  oracle_key_risk TEXT,                     -- brief risk statement
  llm_provider TEXT NOT NULL,               -- 'mistral', 'deepseek', etc
  llm_model TEXT NOT NULL,                  -- exact model name
  raw_llm_output TEXT,                      -- full LLM response for audit
  
  -- Publication tracking
  published_at TEXT NOT NULL,               -- ISO timestamp
  telegram_message_id INTEGER,              -- ID of message in channel
  telegram_channel_username TEXT,           -- '@channelname' for audit
  
  -- Resolution tracking
  resolved_at TEXT,                         -- NULL until resolved
  actual_outcome INTEGER CHECK(actual_outcome IN (0, 1)),  -- 0=NO, 1=YES, NULL=pending
  result TEXT CHECK(result IN ('WIN', 'LOSS', 'PUSH')),
  
  -- Performance metrics (computed fields)
  brier_score REAL,                         -- (pythia_prob - outcome)^2
  contributed_to_streak INTEGER DEFAULT 0,  -- 1 if part of active win streak
  
  -- Flags
  is_disputed INTEGER DEFAULT 0,            -- UMA resolution dispute
  is_backfilled INTEGER DEFAULT 0,          -- retrospective vs live
  
  -- Timestamps
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_oracle_prophecies_market_id 
  ON oracle_prophecies(market_id);

CREATE INDEX IF NOT EXISTS idx_oracle_prophecies_published_at 
  ON oracle_prophecies(published_at);

CREATE INDEX IF NOT EXISTS idx_oracle_prophecies_result 
  ON oracle_prophecies(result);

CREATE INDEX IF NOT EXISTS idx_oracle_prophecies_resolved_at 
  ON oracle_prophecies(resolved_at);

CREATE INDEX IF NOT EXISTS idx_oracle_prophecies_pending 
  ON oracle_prophecies(resolved_at) 
  WHERE resolved_at IS NULL;

-- Trigger to update updated_at on changes
CREATE TRIGGER IF NOT EXISTS oracle_prophecies_updated_at
  AFTER UPDATE ON oracle_prophecies
  FOR EACH ROW
BEGIN
  UPDATE oracle_prophecies 
  SET updated_at = datetime('now') 
  WHERE id = NEW.id;
END;

-- Oracle metrics summary table (for fast access to current stats)
CREATE TABLE IF NOT EXISTS oracle_metrics (
  id INTEGER PRIMARY KEY CHECK(id = 1),     -- singleton table
  
  -- Accuracy metrics
  accuracy_7d REAL,
  accuracy_30d REAL,
  accuracy_all_time REAL,
  
  -- Volume metrics
  total_prophecies INTEGER DEFAULT 0,
  total_resolved INTEGER DEFAULT 0,
  total_wins INTEGER DEFAULT 0,
  total_losses INTEGER DEFAULT 0,
  
  -- Streak tracking
  current_streak INTEGER DEFAULT 0,         -- positive = wins, negative = losses
  max_win_streak INTEGER DEFAULT 0,
  
  -- Performance
  avg_brier_7d REAL,
  avg_brier_30d REAL,
  avg_brier_all_time REAL,
  avg_edge_all_time REAL,
  
  -- Timestamps
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Initialize metrics row
INSERT OR IGNORE INTO oracle_metrics (id) VALUES (1);

-- Comments for documentation
-- This schema supports:
-- 1. Immutable audit trail (telegram_message_id proves publication)
-- 2. Multi-LLM comparison (llm_provider + llm_model fields)
-- 3. Backfill transparency (is_backfilled flag)
-- 4. Dispute handling (is_disputed flag)
-- 5. Fast metrics access (oracle_metrics summary table)
