-- Pythia Capital: Virtual Portfolio + Dual-LLM Tracking
-- Created: 2026-06-27 for Pythia Capital rebrand

-- Virtual portfolio positions (one per published prophecy)
CREATE TABLE IF NOT EXISTS portfolio_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prophecy_id INTEGER REFERENCES oracle_prophecies(id),
  entry_price REAL NOT NULL,        -- 0-1 (market price at time of pick)
  stake_fraction REAL NOT NULL,     -- % of AUM (Kelly-derived)
  stake_usd REAL NOT NULL,          -- virtual $ amount
  direction TEXT NOT NULL,          -- 'YES' | 'NO'
  status TEXT DEFAULT 'open',       -- 'open' | 'closed' | 'expired'
  exit_price REAL,                  -- price at resolution
  realized_pnl REAL,                -- ROI in $ (positive=win, negative=loss)
  opened_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio_positions(status);
CREATE INDEX IF NOT EXISTS idx_portfolio_prophecy ON portfolio_positions(prophecy_id);

-- Portfolio state singleton (current AUM, cash, etc.)
CREATE TABLE IF NOT EXISTS portfolio_state (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  starting_aum REAL DEFAULT 1000.0,
  current_aum REAL DEFAULT 1000.0,
  cash REAL DEFAULT 1000.0,
  positions_value REAL DEFAULT 0.0,
  total_realized_pnl REAL DEFAULT 0.0,
  total_trades INTEGER DEFAULT 0,
  winning_trades INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO portfolio_state (id) VALUES (1);

-- Dual-LLM prediction tracking (A/B comparison)
ALTER TABLE predictions ADD COLUMN deepseek_v4_pro_p REAL;
ALTER TABLE predictions ADD COLUMN deepseek_v4_pro_reasoning TEXT;
ALTER TABLE predictions ADD COLUMN mistral_small_p REAL;
ALTER TABLE predictions ADD COLUMN mistral_small_reasoning TEXT;
ALTER TABLE predictions ADD COLUMN ensemble_p REAL;
ALTER TABLE predictions ADD COLUMN llm_disagreement REAL;
