/**
 * Postgres shadow schema for Pythia (Dual-Loop Phase 1 — Track D).
 *
 * This is the drizzle-orm schema that mirrors the SQLite tables currently
 * defined in:
 *   - `src/services/db.ts`          (predictions, + inline migrations)
 *   - `sql/decision_core.sql`       (books, signals, decisions, positions, ...)
 *   - `sql/watchlist_engine.sql`    (watch_dossiers, watch_refresh_runs, ...)
 *   - `sql/attention_engine.sql`    (attention_items, attention_item_events)
 *   - `python/operator_core.py`     (`watchlist` — lightweight operator table)
 *
 * Guiding principles for parity:
 *   - Primary keys: `bigserial` (= SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`).
 *   - Textual timestamps stay as `text` (SQLite stores ISO-8601 strings) so
 *     byte-level parity holds during the 7-day shadow window described in
 *     `docs/dual_loop/06-roadmap.md` (Phase 1 DoD).
 *   - All `REAL` → `double precision`.
 *   - All `INTEGER NOT NULL DEFAULT X` → `integer not null default X`.
 *   - Same UNIQUE + indexes as SQLite (reproduced below).
 *   - Foreign keys preserve ON DELETE semantics.
 *
 * NOTE: This schema lives in the default `public` schema of the `pythia_dev`
 * database — *not* under the `dual_loop` schema, which is reserved for the
 * new Dual-Loop bus-facing tables in later phases.
 */
import {
  bigserial,
  doublePrecision,
  foreignKey,
  index,
  integer,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
} from 'drizzle-orm/pg-core';

// ---------------------------------------------------------------------------
// predictions (src/services/db.ts)
// ---------------------------------------------------------------------------

export const predictions = pgTable(
  'predictions',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    market_id: text('market_id').notNull(),
    question: text('question').notNull(),
    category: text('category'),
    volume: doublePrecision('volume'),
    created_at: text('created_at'),
    end_date: text('end_date'),
    market_price: doublePrecision('market_price').notNull(),

    dexter_p: doublePrecision('dexter_p'),
    dexter_confidence: text('dexter_confidence'),
    dexter_reasoning: text('dexter_reasoning'),
    dexter_key_risk: text('dexter_key_risk'),
    dexter_base_rate: text('dexter_base_rate'),

    naked_p: doublePrecision('naked_p'),
    naked_reasoning: text('naked_reasoning'),
    research_brief_json: text('research_brief_json'),

    forecast_timestamp: text('forecast_timestamp').notNull(),
    is_valid: integer('is_valid').notNull().default(1),

    outcome: integer('outcome'),
    resolved_at: text('resolved_at'),

    brier_dexter: doublePrecision('brier_dexter'),
    brier_naked: doublePrecision('brier_naked'),
    brier_market: doublePrecision('brier_market'),

    api_cost_usd: doublePrecision('api_cost_usd').default(0),

    // Phase 1 shadow-write debugging column (not present in SQLite).
    _shadow_pg_written_at: timestamp('_shadow_pg_written_at', {
      withTimezone: true,
    }).defaultNow(),
  },
  (table) => ({
    uniqMarketForecast: uniqueIndex('predictions_market_forecast_uniq').on(
      table.market_id,
      table.forecast_timestamp,
    ),
    idxOutcome: index('idx_outcome').on(table.outcome),
    idxEndDate: index('idx_end_date').on(table.end_date),
    idxForecastTs: index('idx_forecast_ts').on(table.forecast_timestamp),
    idxPredictionsValid: index('idx_predictions_valid').on(table.is_valid),
  }),
);

// ---------------------------------------------------------------------------
// decision_core (sql/decision_core.sql)
// ---------------------------------------------------------------------------

export const books = pgTable('books', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  name: text('name').notNull().unique(),
  mode: text('mode').notNull(),
  currency: text('currency').notNull().default('USDC'),
  starting_balance: doublePrecision('starting_balance').notNull(),
  current_balance: doublePrecision('current_balance').notNull(),
  max_risk_per_trade_pct: doublePrecision('max_risk_per_trade_pct').notNull(),
  max_total_exposure_pct: doublePrecision('max_total_exposure_pct').notNull(),
  status: text('status').notNull().default('active'),
  created_at: text('created_at').notNull(),
  updated_at: text('updated_at').notNull(),
});

export const signals = pgTable(
  'signals',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    prediction_id: integer('prediction_id').notNull().unique(),
    market_id: text('market_id').notNull(),
    question: text('question').notNull(),
    forecast_timestamp: text('forecast_timestamp').notNull(),
    market_price: doublePrecision('market_price'),
    research_probability: doublePrecision('research_probability'),
    naked_probability: doublePrecision('naked_probability'),
    best_probability: doublePrecision('best_probability'),
    signal_edge: doublePrecision('signal_edge'),
    signal_strength: doublePrecision('signal_strength').notNull(),
    recommended_action: text('recommended_action').notNull(),
    entry_zone_min: doublePrecision('entry_zone_min'),
    entry_zone_max: doublePrecision('entry_zone_max'),
    thesis_summary: text('thesis_summary'),
    primary_driver: text('primary_driver'),
    disconfirming_signal: text('disconfirming_signal'),
    confidence: text('confidence'),
    research_status: text('research_status').notNull(),
    research_status_detail: text('research_status_detail'),
    status: text('status').notNull().default('new'),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (table) => ({
    idxSignalsMarketId: index('idx_signals_market_id').on(table.market_id),
    idxSignalsStatus: index('idx_signals_status').on(table.status),
    idxSignalsForecastTs: index('idx_signals_forecast_ts').on(
      table.forecast_timestamp,
    ),
  }),
);

export const decisions = pgTable(
  'decisions',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    signal_id: integer('signal_id').notNull(),
    book_id: integer('book_id'),
    market_id: text('market_id').notNull(),
    actor_type: text('actor_type').notNull(),
    decision_type: text('decision_type').notNull(),
    decision_reason: text('decision_reason').notNull(),
    operator_confidence: text('operator_confidence'),
    confirmation_criteria: text('confirmation_criteria'),
    invalidation_criteria: text('invalidation_criteria'),
    review_due_at: text('review_due_at'),
    status: text('status').notNull().default('active'),
    created_at: text('created_at').notNull(),
  },
  (table) => ({
    idxDecisionsMarketCreated: index('idx_decisions_market_created').on(
      table.market_id,
      table.created_at,
    ),
    idxDecisionsSignalId: index('idx_decisions_signal_id').on(table.signal_id),
    fkSignal: foreignKey({
      columns: [table.signal_id],
      foreignColumns: [signals.id],
      name: 'decisions_signal_id_fk',
    }).onDelete('cascade'),
    fkBook: foreignKey({
      columns: [table.book_id],
      foreignColumns: [books.id],
      name: 'decisions_book_id_fk',
    }),
  }),
);

export const positions = pgTable(
  'positions',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    book_id: integer('book_id').notNull(),
    market_id: text('market_id').notNull(),
    signal_id: integer('signal_id').notNull(),
    opened_by_decision_id: integer('opened_by_decision_id').notNull(),
    mode: text('mode').notNull(),
    side: text('side').notNull(),
    status: text('status').notNull().default('open'),
    entry_yes_price: doublePrecision('entry_yes_price').notNull(),
    entry_cost_per_contract: doublePrecision('entry_cost_per_contract').notNull(),
    entry_zone_min: doublePrecision('entry_zone_min'),
    entry_zone_max: doublePrecision('entry_zone_max'),
    size_contracts: doublePrecision('size_contracts').notNull(),
    notional_usd: doublePrecision('notional_usd').notNull(),
    review_due_at: text('review_due_at'),
    opened_at: text('opened_at').notNull(),
    closed_at: text('closed_at'),
    closed_yes_price: doublePrecision('closed_yes_price'),
    close_reason: text('close_reason'),
    thesis_cycle_number: integer('thesis_cycle_number').notNull().default(1),
    pnl_usd: doublePrecision('pnl_usd'),
    pnl_percent: doublePrecision('pnl_percent'),
  },
  (table) => ({
    idxPositionsMarketStatus: index('idx_positions_market_status').on(
      table.market_id,
      table.status,
    ),
    idxPositionsBookStatus: index('idx_positions_book_status').on(
      table.book_id,
      table.status,
    ),
    fkBook: foreignKey({
      columns: [table.book_id],
      foreignColumns: [books.id],
      name: 'positions_book_id_fk',
    }),
    fkSignal: foreignKey({
      columns: [table.signal_id],
      foreignColumns: [signals.id],
      name: 'positions_signal_id_fk',
    }),
    fkDecision: foreignKey({
      columns: [table.opened_by_decision_id],
      foreignColumns: [decisions.id],
      name: 'positions_opened_by_decision_id_fk',
    }),
  }),
);

export const positionEvents = pgTable(
  'position_events',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    position_id: integer('position_id').notNull(),
    decision_id: integer('decision_id'),
    event_type: text('event_type').notNull(),
    event_note: text('event_note'),
    market_price: doublePrecision('market_price'),
    research_probability: doublePrecision('research_probability'),
    created_at: text('created_at').notNull(),
    payload_json: text('payload_json'),
  },
  (table) => ({
    idxPositionEvents: index('idx_position_events_position_created').on(
      table.position_id,
      table.created_at,
    ),
    fkPosition: foreignKey({
      columns: [table.position_id],
      foreignColumns: [positions.id],
      name: 'position_events_position_id_fk',
    }).onDelete('cascade'),
    fkDecision: foreignKey({
      columns: [table.decision_id],
      foreignColumns: [decisions.id],
      name: 'position_events_decision_id_fk',
    }),
  }),
);

export const reviews = pgTable(
  'reviews',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    position_id: integer('position_id').notNull(),
    market_id: text('market_id').notNull(),
    review_type: text('review_type').notNull(),
    due_at: text('due_at').notNull(),
    status: text('status').notNull().default('due'),
    review_note: text('review_note'),
    resolved_at: text('resolved_at'),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (table) => ({
    idxReviewsStatusDue: index('idx_reviews_status_due_at').on(
      table.status,
      table.due_at,
    ),
    idxReviewsMarket: index('idx_reviews_market_id').on(table.market_id),
    fkPosition: foreignKey({
      columns: [table.position_id],
      foreignColumns: [positions.id],
      name: 'reviews_position_id_fk',
    }).onDelete('cascade'),
  }),
);

export const postmortems = pgTable(
  'postmortems',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    position_id: integer('position_id').notNull().unique(),
    market_id: text('market_id').notNull(),
    outcome_label: text('outcome_label'),
    pnl_usd: doublePrecision('pnl_usd'),
    pnl_percent: doublePrecision('pnl_percent'),
    forecast_error: integer('forecast_error'),
    entry_error: integer('entry_error'),
    timing_error: integer('timing_error'),
    management_error: integer('management_error'),
    discipline_error: integer('discipline_error'),
    operator_note: text('operator_note'),
    system_note: text('system_note'),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (table) => ({
    idxPostmortemsMarket: index('idx_postmortems_market_created').on(
      table.market_id,
      table.created_at,
    ),
    fkPosition: foreignKey({
      columns: [table.position_id],
      foreignColumns: [positions.id],
      name: 'postmortems_position_id_fk',
    }).onDelete('cascade'),
  }),
);

// ---------------------------------------------------------------------------
// watchlist_engine (sql/watchlist_engine.sql)
// ---------------------------------------------------------------------------

export const watchDossiers = pgTable(
  'watch_dossiers',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    market_id: text('market_id').notNull().unique(),
    status: text('status').notNull().default('active'),
    created_from_prediction_id: integer('created_from_prediction_id'),
    created_from_signal_id: integer('created_from_signal_id'),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
    first_watched_at: text('first_watched_at').notNull(),
    last_refresh_at: text('last_refresh_at'),
    last_deep_review_at: text('last_deep_review_at'),
    next_review_due_at: text('next_review_due_at'),
    freshness_policy: text('freshness_policy').notNull(),
    initial_snapshot_state: text('initial_snapshot_state')
      .notNull()
      .default('reused'),
    thesis_state: text('thesis_state').notNull().default('review_required'),
    current_market_price: doublePrecision('current_market_price'),
    current_research_probability: doublePrecision('current_research_probability'),
    current_naked_probability: doublePrecision('current_naked_probability'),
    current_confidence: text('current_confidence'),
    current_research_status: text('current_research_status'),
    current_thesis_summary: text('current_thesis_summary'),
    current_primary_driver: text('current_primary_driver'),
    current_invalidation_signal: text('current_invalidation_signal'),
    last_status_label: text('last_status_label'),
    last_reason_summary: text('last_reason_summary'),
    last_action_summary: text('last_action_summary'),
    operator_note: text('operator_note').notNull().default(''),
  },
  (table) => ({
    idxMarketId: index('idx_watch_dossiers_market_id').on(table.market_id),
    idxStatusUpdated: index('idx_watch_dossiers_status_updated_at').on(
      table.status,
      table.updated_at,
    ),
    fkPrediction: foreignKey({
      columns: [table.created_from_prediction_id],
      foreignColumns: [predictions.id],
      name: 'watch_dossiers_prediction_fk',
    }).onDelete('set null'),
    fkSignal: foreignKey({
      columns: [table.created_from_signal_id],
      foreignColumns: [signals.id],
      name: 'watch_dossiers_signal_fk',
    }).onDelete('set null'),
  }),
);

export const watchRefreshRuns = pgTable(
  'watch_refresh_runs',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    started_at: text('started_at').notNull(),
    finished_at: text('finished_at'),
    status: text('status').notNull(),
    markets_total: integer('markets_total').notNull().default(0),
    markets_checked: integer('markets_checked').notNull().default(0),
    markets_changed: integer('markets_changed').notNull().default(0),
    deep_reviews_triggered: integer('deep_reviews_triggered').notNull().default(0),
    alerts_created: integer('alerts_created').notNull().default(0),
    error_summary: text('error_summary').notNull().default(''),
  },
  (table) => ({
    idxStatusStarted: index('idx_watch_refresh_runs_status_started_at').on(
      table.status,
      table.started_at,
    ),
  }),
);

export const watchRefreshSnapshots = pgTable(
  'watch_refresh_snapshots',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    watch_dossier_id: integer('watch_dossier_id').notNull(),
    refresh_run_id: integer('refresh_run_id').notNull(),
    captured_at: text('captured_at').notNull(),
    market_price: doublePrecision('market_price'),
    research_probability: doublePrecision('research_probability'),
    naked_probability: doublePrecision('naked_probability'),
    edge_vs_market: doublePrecision('edge_vs_market'),
    price_delta_since_last: doublePrecision('price_delta_since_last'),
    thesis_state: text('thesis_state').notNull(),
    research_status: text('research_status'),
    monitoring_status: text('monitoring_status').notNull(),
    trigger_flags_json: text('trigger_flags_json').notNull().default('{}'),
    news_digest_json: text('news_digest_json').notNull().default('{}'),
    monitoring_output_json: text('monitoring_output_json').notNull().default('{}'),
  },
  (table) => ({
    idxDossierCaptured: index(
      'idx_watch_refresh_snapshots_dossier_captured_at',
    ).on(table.watch_dossier_id, table.captured_at),
    fkDossier: foreignKey({
      columns: [table.watch_dossier_id],
      foreignColumns: [watchDossiers.id],
      name: 'watch_refresh_snapshots_dossier_fk',
    }).onDelete('cascade'),
    fkRun: foreignKey({
      columns: [table.refresh_run_id],
      foreignColumns: [watchRefreshRuns.id],
      name: 'watch_refresh_snapshots_run_fk',
    }).onDelete('cascade'),
  }),
);

export const watchUpdates = pgTable(
  'watch_updates',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    watch_dossier_id: integer('watch_dossier_id').notNull(),
    refresh_run_id: integer('refresh_run_id').notNull(),
    created_at: text('created_at').notNull(),
    severity: text('severity').notNull(),
    status_label: text('status_label').notNull(),
    reason_summary: text('reason_summary').notNull(),
    action_summary: text('action_summary').notNull(),
    market_price_before: doublePrecision('market_price_before'),
    market_price_after: doublePrecision('market_price_after'),
    research_probability_before: doublePrecision('research_probability_before'),
    research_probability_after: doublePrecision('research_probability_after'),
    change_summary_json: text('change_summary_json').notNull().default('{}'),
    delivered_to_dashboard_at: text('delivered_to_dashboard_at'),
    delivered_to_telegram_at: text('delivered_to_telegram_at'),
  },
  (table) => ({
    idxDossierCreated: index('idx_watch_updates_dossier_created_at').on(
      table.watch_dossier_id,
      table.created_at,
    ),
    idxDelivery: index('idx_watch_updates_delivery').on(
      table.delivered_to_telegram_at,
      table.created_at,
    ),
    fkDossier: foreignKey({
      columns: [table.watch_dossier_id],
      foreignColumns: [watchDossiers.id],
      name: 'watch_updates_dossier_fk',
    }).onDelete('cascade'),
    fkRun: foreignKey({
      columns: [table.refresh_run_id],
      foreignColumns: [watchRefreshRuns.id],
      name: 'watch_updates_run_fk',
    }).onDelete('cascade'),
  }),
);

// ---------------------------------------------------------------------------
// attention_engine (sql/attention_engine.sql)
// ---------------------------------------------------------------------------

export const attentionItems = pgTable(
  'attention_items',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    market_id: text('market_id').notNull(),
    source_type: text('source_type').notNull(),
    source_id: integer('source_id'),
    category: text('category').notNull(),
    priority: text('priority').notNull(),
    status: text('status').notNull().default('new'),
    title: text('title').notNull(),
    detail: text('detail').notNull(),
    action_label: text('action_label').notNull().default(''),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
    last_seen_at: text('last_seen_at'),
    last_telegram_sent_at: text('last_telegram_sent_at'),
  },
  (table) => ({
    idxMarketCategoryStatus: index(
      'idx_attention_items_market_category_status',
    ).on(table.market_id, table.category, table.status, table.updated_at),
    idxPriorityStatus: index('idx_attention_items_priority_status').on(
      table.priority,
      table.status,
      table.updated_at,
    ),
    idxTelegramDelivery: index('idx_attention_items_telegram_delivery').on(
      table.last_telegram_sent_at,
      table.priority,
      table.updated_at,
    ),
  }),
);

export const attentionItemEvents = pgTable(
  'attention_item_events',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    attention_item_id: integer('attention_item_id').notNull(),
    action: text('action').notNull(),
    from_status: text('from_status'),
    to_status: text('to_status'),
    created_at: text('created_at').notNull(),
    note: text('note').notNull().default(''),
  },
  (table) => ({
    idxItem: index('idx_attention_item_events_item').on(
      table.attention_item_id,
      table.created_at,
    ),
    fkItem: foreignKey({
      columns: [table.attention_item_id],
      foreignColumns: [attentionItems.id],
      name: 'attention_item_events_item_fk',
    }).onDelete('cascade'),
  }),
);

// ---------------------------------------------------------------------------
// watchlist (python/operator_core.py — lightweight operator table)
// ---------------------------------------------------------------------------

export const watchlist = pgTable('watchlist', {
  market_id: text('market_id').primaryKey(),
  note: text('note').notNull().default(''),
  created_at: text('created_at').notNull(),
  updated_at: text('updated_at').notNull(),
});
