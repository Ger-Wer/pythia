CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USDC',
    starting_balance REAL NOT NULL,
    current_balance REAL NOT NULL,
    max_risk_per_trade_pct REAL NOT NULL,
    max_total_exposure_pct REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL UNIQUE,
    market_id TEXT NOT NULL,
    question TEXT NOT NULL,
    forecast_timestamp TEXT NOT NULL,
    market_price REAL,
    research_probability REAL,
    naked_probability REAL,
    best_probability REAL,
    signal_edge REAL,
    signal_strength REAL NOT NULL,
    recommended_action TEXT NOT NULL,
    entry_zone_min REAL,
    entry_zone_max REAL,
    thesis_summary TEXT,
    primary_driver TEXT,
    disconfirming_signal TEXT,
    confidence TEXT,
    research_status TEXT NOT NULL,
    research_status_detail TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    book_id INTEGER,
    market_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    operator_confidence TEXT,
    confirmation_criteria TEXT,
    invalidation_criteria TEXT,
    review_due_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    market_id TEXT NOT NULL,
    signal_id INTEGER NOT NULL,
    opened_by_decision_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    entry_yes_price REAL NOT NULL,
    entry_cost_per_contract REAL NOT NULL,
    entry_zone_min REAL,
    entry_zone_max REAL,
    size_contracts REAL NOT NULL,
    notional_usd REAL NOT NULL,
    review_due_at TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    closed_yes_price REAL,
    close_reason TEXT,
    thesis_cycle_number INTEGER NOT NULL DEFAULT 1,
    pnl_usd REAL,
    pnl_percent REAL,
    FOREIGN KEY (book_id) REFERENCES books(id),
    FOREIGN KEY (signal_id) REFERENCES signals(id),
    FOREIGN KEY (opened_by_decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS position_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    decision_id INTEGER,
    event_type TEXT NOT NULL,
    event_note TEXT,
    market_price REAL,
    research_probability REAL,
    created_at TEXT NOT NULL,
    payload_json TEXT,
    FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    market_id TEXT NOT NULL,
    review_type TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'due',
    review_note TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS postmortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL UNIQUE,
    market_id TEXT NOT NULL,
    outcome_label TEXT,
    pnl_usd REAL,
    pnl_percent REAL,
    forecast_error INTEGER,
    entry_error INTEGER,
    timing_error INTEGER,
    management_error INTEGER,
    discipline_error INTEGER,
    operator_note TEXT,
    system_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_signals_market_id ON signals(market_id);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_forecast_ts ON signals(forecast_timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_market_created ON decisions(market_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_signal_id ON decisions(signal_id);
CREATE INDEX IF NOT EXISTS idx_positions_market_status ON positions(market_id, status);
CREATE INDEX IF NOT EXISTS idx_positions_book_status ON positions(book_id, status);
CREATE INDEX IF NOT EXISTS idx_position_events_position_created ON position_events(position_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_reviews_status_due_at ON reviews(status, due_at);
CREATE INDEX IF NOT EXISTS idx_reviews_market_id ON reviews(market_id);
CREATE INDEX IF NOT EXISTS idx_postmortems_market_created ON postmortems(market_id, created_at DESC);

INSERT OR IGNORE INTO books (
    name,
    mode,
    currency,
    starting_balance,
    current_balance,
    max_risk_per_trade_pct,
    max_total_exposure_pct,
    status
) VALUES
    ('shadow_default', 'shadow', 'USDC', 10000, 10000, 0.02, 0.20, 'active'),
    ('operator_default', 'operator', 'USDC', 10000, 10000, 0.02, 0.20, 'active');
