PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS attention_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    action_label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    last_telegram_sent_at TEXT,
    FOREIGN KEY (market_id) REFERENCES watch_dossiers(market_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attention_item_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attention_item_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    created_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (attention_item_id) REFERENCES attention_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attention_items_market_category_status
ON attention_items(market_id, category, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_attention_items_priority_status
ON attention_items(priority, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_attention_items_telegram_delivery
ON attention_items(last_telegram_sent_at, priority, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_attention_item_events_item
ON attention_item_events(attention_item_id, created_at ASC);
