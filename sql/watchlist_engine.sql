PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS watch_dossiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    created_from_prediction_id INTEGER,
    created_from_signal_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    first_watched_at TEXT NOT NULL,
    last_refresh_at TEXT,
    last_deep_review_at TEXT,
    next_review_due_at TEXT,
    freshness_policy TEXT NOT NULL,
    initial_snapshot_state TEXT NOT NULL DEFAULT 'reused',
    thesis_state TEXT NOT NULL DEFAULT 'review_required',
    current_market_price REAL,
    current_research_probability REAL,
    current_naked_probability REAL,
    current_confidence TEXT,
    current_research_status TEXT,
    current_thesis_summary TEXT,
    current_primary_driver TEXT,
    current_invalidation_signal TEXT,
    last_status_label TEXT,
    last_reason_summary TEXT,
    last_action_summary TEXT,
    operator_note TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (created_from_prediction_id) REFERENCES predictions(id) ON DELETE SET NULL,
    FOREIGN KEY (created_from_signal_id) REFERENCES signals(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS watch_refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    markets_total INTEGER NOT NULL DEFAULT 0,
    markets_checked INTEGER NOT NULL DEFAULT 0,
    markets_changed INTEGER NOT NULL DEFAULT 0,
    deep_reviews_triggered INTEGER NOT NULL DEFAULT 0,
    alerts_created INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watch_refresh_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_dossier_id INTEGER NOT NULL,
    refresh_run_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    market_price REAL,
    research_probability REAL,
    naked_probability REAL,
    edge_vs_market REAL,
    price_delta_since_last REAL,
    thesis_state TEXT NOT NULL,
    research_status TEXT,
    monitoring_status TEXT NOT NULL,
    trigger_flags_json TEXT NOT NULL DEFAULT '{}',
    news_digest_json TEXT NOT NULL DEFAULT '{}',
    monitoring_output_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (watch_dossier_id) REFERENCES watch_dossiers(id) ON DELETE CASCADE,
    FOREIGN KEY (refresh_run_id) REFERENCES watch_refresh_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS watch_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_dossier_id INTEGER NOT NULL,
    refresh_run_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    status_label TEXT NOT NULL,
    reason_summary TEXT NOT NULL,
    action_summary TEXT NOT NULL,
    market_price_before REAL,
    market_price_after REAL,
    research_probability_before REAL,
    research_probability_after REAL,
    change_summary_json TEXT NOT NULL DEFAULT '{}',
    delivered_to_dashboard_at TEXT,
    delivered_to_telegram_at TEXT,
    FOREIGN KEY (watch_dossier_id) REFERENCES watch_dossiers(id) ON DELETE CASCADE,
    FOREIGN KEY (refresh_run_id) REFERENCES watch_refresh_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_watch_dossiers_market_id ON watch_dossiers(market_id);
CREATE INDEX IF NOT EXISTS idx_watch_dossiers_status_updated_at ON watch_dossiers(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_refresh_runs_status_started_at ON watch_refresh_runs(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_refresh_snapshots_dossier_captured_at ON watch_refresh_snapshots(watch_dossier_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_updates_dossier_created_at ON watch_updates(watch_dossier_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_updates_delivery ON watch_updates(delivered_to_telegram_at, created_at ASC);
