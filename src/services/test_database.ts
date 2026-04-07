import { Database } from 'bun:sqlite';

function runStatements(db: Database, sql: string): void {
  for (const statement of sql
    .split(/;\s*(?:\r?\n|$)/g)
    .map((value) => value.trim())
    .filter(Boolean)) {
    db.run(statement);
  }
}

export function createTestDatabase(): Database {
  const db = new Database(':memory:');
  db.exec('PRAGMA foreign_keys = ON');
  runStatements(
    db,
    `
      CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id TEXT NOT NULL,
        question TEXT NOT NULL,
        category TEXT,
        market_price REAL,
        dexter_p REAL,
        naked_p REAL,
        dexter_confidence TEXT,
        dexter_reasoning TEXT,
        dexter_key_risk TEXT,
        dexter_base_rate TEXT,
        research_brief_json TEXT,
        naked_reasoning TEXT,
        volume REAL,
        created_at TEXT,
        end_date TEXT,
        forecast_timestamp TEXT NOT NULL,
        api_cost_usd REAL DEFAULT 0,
        is_valid INTEGER DEFAULT 1
      )
    `,
  );
  return db;
}

export function seedPrediction(
  db: Database,
  input: {
    market_id: string;
    question?: string;
    category?: string | null;
    market_price?: number | null;
    dexter_p?: number | null;
    naked_p?: number | null;
    dexter_confidence?: string | null;
    dexter_reasoning?: string | null;
    dexter_key_risk?: string | null;
    dexter_base_rate?: string | null;
    research_brief_json?: string | null;
    naked_reasoning?: string | null;
    volume?: number | null;
    created_at?: string | null;
    forecast_timestamp: string;
    end_date?: string | null;
    api_cost_usd?: number | null;
    is_valid?: number;
  },
): void {
  db.query(
    `
      INSERT INTO predictions (
        market_id, question, category, market_price, dexter_p, naked_p,
        dexter_confidence, dexter_reasoning, dexter_key_risk, dexter_base_rate,
        research_brief_json, naked_reasoning, volume, created_at, end_date, forecast_timestamp, api_cost_usd, is_valid
      ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18)
    `,
  ).run(
    input.market_id,
    input.question ?? `Question for ${input.market_id}`,
    input.category ?? 'tests',
    input.market_price ?? null,
    input.dexter_p ?? null,
    input.naked_p ?? null,
    input.dexter_confidence ?? null,
    input.dexter_reasoning ?? '',
    input.dexter_key_risk ?? '',
    input.dexter_base_rate ?? '',
    input.research_brief_json ?? '{}',
    input.naked_reasoning ?? '',
    input.volume ?? null,
    input.created_at ?? input.forecast_timestamp,
    input.end_date ?? null,
    input.forecast_timestamp,
    input.api_cost_usd ?? 0,
    input.is_valid ?? 1,
  );
}

export function seedWatchDossier(
  db: Database,
  input: {
    market_id: string;
    thesis_state?: string;
    current_market_price?: number | null;
    current_research_probability?: number | null;
    current_naked_probability?: number | null;
    current_confidence?: string | null;
    current_research_status?: string | null;
    current_thesis_summary?: string | null;
  },
): void {
  runStatements(
    db,
    `
      CREATE TABLE IF NOT EXISTS watch_dossiers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        first_watched_at TEXT NOT NULL,
        freshness_policy TEXT NOT NULL,
        initial_snapshot_state TEXT NOT NULL,
        thesis_state TEXT NOT NULL,
        current_market_price REAL,
        current_research_probability REAL,
        current_naked_probability REAL,
        current_confidence TEXT,
        current_research_status TEXT,
        current_thesis_summary TEXT,
        operator_note TEXT NOT NULL DEFAULT ''
      )
    `,
  );

  db.query(
    `
      INSERT INTO watch_dossiers (
        market_id, status, created_at, updated_at, first_watched_at,
        freshness_policy, initial_snapshot_state, thesis_state,
        current_market_price, current_research_probability, current_naked_probability,
        current_confidence, current_research_status, current_thesis_summary, operator_note
      ) VALUES (?1, 'active', ?2, ?2, ?2, 'normal_24h', 'reused', ?3, ?4, ?5, ?6, ?7, ?8, ?9, '')
    `,
  ).run(
    input.market_id,
    '2026-04-07T06:00:00.000Z',
    input.thesis_state ?? 'intact',
    input.current_market_price ?? null,
    input.current_research_probability ?? null,
    input.current_naked_probability ?? null,
    input.current_confidence ?? null,
    input.current_research_status ?? null,
    input.current_thesis_summary ?? null,
  );
}

export function seedOpenOperatorPosition(
  db: Database,
  input: {
    market_id: string;
  },
): void {
  runStatements(
    db,
    `
      CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        market_id TEXT NOT NULL,
        signal_id INTEGER NOT NULL,
        opened_by_decision_id INTEGER NOT NULL,
        mode TEXT NOT NULL,
        side TEXT NOT NULL,
        status TEXT NOT NULL,
        entry_yes_price REAL NOT NULL,
        entry_cost_per_contract REAL NOT NULL,
        size_contracts REAL NOT NULL,
        notional_usd REAL NOT NULL,
        review_due_at TEXT,
        opened_at TEXT NOT NULL,
        thesis_cycle_number INTEGER NOT NULL
      )
    `,
  );

  const prediction = db
    .query(
      `
        SELECT id
        FROM predictions
        WHERE market_id = ?1
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
      `,
    )
    .get(input.market_id) as { id: number } | null;

  if (!prediction) {
    seedPrediction(db, {
      market_id: input.market_id,
      question: `Question for ${input.market_id}`,
      market_price: 42,
      dexter_p: 55,
      naked_p: 49,
      dexter_confidence: 'medium',
      forecast_timestamp: '2026-04-07T06:00:00.000Z',
      end_date: '2026-05-01T12:00:00.000Z',
    });
  }

  const predictionId = (db
    .query(
      `
        SELECT id
        FROM predictions
        WHERE market_id = ?1
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
      `,
    )
    .get(input.market_id) as { id: number }).id;

  const book = db
    .query(
      `
        SELECT id
        FROM books
        WHERE name = 'operator_default'
        LIMIT 1
      `,
    )
    .get() as { id: number } | null;

  const bookId = book?.id ?? 1;

  db.query(
    `
      INSERT INTO signals (
        prediction_id, market_id, question, forecast_timestamp, market_price,
        research_probability, naked_probability, best_probability, signal_edge,
        signal_strength, recommended_action, entry_zone_min, entry_zone_max,
        thesis_summary, primary_driver, disconfirming_signal, confidence,
        research_status, status, created_at, updated_at
      ) VALUES (
        ?1, ?2, ?3, '2026-04-07T06:00:00.000Z', 42,
        55, 49, 55, 13, 13, 'enter_yes', 40, 53,
        'Fixture thesis', 'Fixture driver', 'Fixture invalidation', 'medium',
        'ON', 'new', '2026-04-07T06:00:00.000Z', '2026-04-07T06:00:00.000Z'
      )
    `,
  ).run(predictionId, input.market_id, `Question for ${input.market_id}`);

  const signalId = Number(db.query('SELECT last_insert_rowid() as id').get()?.id ?? 0);

  db.query(
    `
      INSERT INTO decisions (
        signal_id, book_id, market_id, actor_type, decision_type, decision_reason,
        operator_confidence, confirmation_criteria, invalidation_criteria, review_due_at, status, created_at
      ) VALUES (
        ?1, ?2, ?3, 'operator', 'enter_yes', 'Fixture operator decision',
        'medium', 'Fixture confirmation', 'Fixture invalidation', '2026-04-08T06:00:00.000Z', 'active', '2026-04-07T06:00:00.000Z'
      )
    `,
  ).run(signalId, bookId, input.market_id);

  const decisionId = Number(db.query('SELECT last_insert_rowid() as id').get()?.id ?? 0);

  db.query(
    `
      INSERT INTO positions (
        book_id, market_id, signal_id, opened_by_decision_id, mode, side, status,
        entry_yes_price, entry_cost_per_contract, size_contracts, notional_usd,
        review_due_at, opened_at, thesis_cycle_number
      ) VALUES (?1, ?2, ?3, ?4, 'operator', 'yes', 'open', 42, 0.42, 100, 42, ?5, ?6, 1)
    `,
  ).run(
    bookId,
    input.market_id,
    signalId,
    decisionId,
    '2026-04-08T06:00:00.000Z',
    '2026-04-07T06:00:00.000Z',
  );
}
