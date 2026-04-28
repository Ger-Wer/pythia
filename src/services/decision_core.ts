import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { Database } from './db';
import { createSafetyPipeline } from './safety_pipeline';
import type { SafetyPipeline } from './safety_pipeline';
import { fetchMarket, marketPriceFromYesOutcome } from '../tools/polymarket';
import type { PolymarketMarket } from '../tools/polymarket';
import { sendTelegramAlert, formatSafetyRejectionAlert } from './telegram_alerts';
import {
  classifyDecision,
  getWatchlistPublisher,
} from './watchlist_publisher';

type DecisionAction = 'watch' | 'review' | 'enter_yes' | 'enter_no';
type PositionMode = 'shadow' | 'operator';
type PositionSide = 'yes' | 'no';
type PositionCloseReason =
  | 'resolved'
  | 'thesis_break'
  | 'time_review'
  | 'manual_exit'
  | 'take_profit'
  | 'stop_loss'
  | 'cancelled';

// Research status enum (expanded from binary ON/DEGRADED)
type ResearchStatus = 'ON' | 'PARTIAL' | 'TIMEOUT' | 'COST_LIMIT' | 'DATA_STALE' | 'RESOLVER_OFFLINE' | 'ERROR';

interface ResearchStatusDetail {
  status: ResearchStatus;
  reason: string; // Human-readable explanation
  error_code?: string;
  error_message?: string;
  timestamp: string;
  retry_count?: number;
}

// Position sizing configuration and helpers
interface PositionSizingParams {
  signalEdge: number; // forecast - market_price (in percentage points)
  researchConfidence: string; // 'low' | 'medium' | 'high'
  portfolioRecentVol: number; // realized volatility 0-100
  baseCapital: number; // shadow book balance
  maxRiskPercentage: number; // max risk as % of capital (typically 5)
}

interface PositionSizingResult {
  sizeContracts: number;
  notionalUsd: number;
  factors: {
    edgeFactor: number;
    confidenceFactor: number;
    volFactor: number;
  };
}

interface PredictionSnapshot {
  id: number;
  market_id: string;
  question: string;
  market_price: number | null;
  dexter_p: number | null;
  naked_p: number | null;
  dexter_confidence: string | null;
  dexter_reasoning: string | null;
  research_brief_json: string | null;
  forecast_timestamp: string;
  end_date: string | null;
}

interface ResearchFields {
  summary: string;
  primary_driver: string;
  disconfirming_signal: string;
}

interface SignalSnapshot {
  id: number;
  prediction_id: number;
  market_id: string;
  question: string;
  forecast_timestamp: string;
  market_price: number | null;
  research_probability: number | null;
  naked_probability: number | null;
  best_probability: number | null;
  signal_edge: number | null;
  signal_strength: number;
  recommended_action: DecisionAction;
  entry_zone_min: number | null;
  entry_zone_max: number | null;
  thesis_summary: string;
  primary_driver: string;
  disconfirming_signal: string;
  confidence: string | null;
  research_status: ResearchStatus;
  research_status_detail: string | null; // JSON: ResearchStatusDetail
  status: string;
}

interface BookRow {
  id: number;
  current_balance: number;
  max_risk_per_trade_pct: number;
  max_total_exposure_pct: number;
}

interface PositionRow {
  id: number;
  book_id: number;
  market_id: string;
  signal_id: number;
  opened_by_decision_id: number;
  mode: PositionMode;
  side: PositionSide;
  status: string;
  entry_yes_price: number;
  entry_cost_per_contract: number;
  size_contracts: number;
  notional_usd: number;
  review_due_at: string | null;
  opened_at: string;
  closed_at: string | null;
  closed_yes_price: number | null;
  close_reason: string | null;
  thesis_cycle_number: number;
}

const DECISION_CORE_SQL_PATH = path.resolve(import.meta.dir, '../../sql/decision_core.sql');
const SHADOW_BOOK_NAME = 'shadow_default';
const POSITION_REVIEW_WINDOW_MS = 24 * 60 * 60 * 1000;
const STRONG_SIGNAL_THRESHOLD = 15;
const REVIEW_SIGNAL_THRESHOLD = 7;
const REVIEW_CONFIRMATION_THRESHOLD = 10;
const ADVERSE_MOVE_THRESHOLD = 10;

// Safety Pipeline - глобальный экземпляр для проверок безопасности
let globalSafetyPipeline: SafetyPipeline | null = null;

function getSafetyPipeline(): SafetyPipeline {
  if (!globalSafetyPipeline) {
    // Импортируем CONFIG для использования настроек
    const { CONFIG } = require('../config');
    globalSafetyPipeline = createSafetyPipeline({
      maxSignalAgeSeconds: CONFIG.safety.maxSignalAgeSeconds,
      priceDriftTolerance: CONFIG.safety.priceDriftTolerance,
      cooldownSeconds: CONFIG.safety.cooldownSeconds,
      dedupWindowMinutes: CONFIG.safety.dedupWindowMinutes,
    });
  }
  return globalSafetyPipeline;
}

function runSqlScript(db: Database, sql: string): void {
  for (const statement of sql
    .split(/;\s*(?:\r?\n|$)/g)
    .map((value) => value.trim())
    .filter(Boolean)) {
    db.run(statement);
  }
}

export function ensureDecisionCoreSchema(db: Database): void {
  const sql = readFileSync(DECISION_CORE_SQL_PATH, 'utf8');
  runSqlScript(db, sql);
}

function parseResearchFields(payload: string | null): ResearchFields {
  if (!payload) {
    return {
      summary: '',
      primary_driver: 'Нет данных.',
      disconfirming_signal: 'Нет данных.',
    };
  }

  try {
    const parsed = JSON.parse(payload) as Record<string, unknown>;
    return {
      summary: String(parsed.summary ?? '').trim(),
      primary_driver: String(parsed.primary_driver ?? 'Нет данных.').trim() || 'Нет данных.',
      disconfirming_signal:
        String(parsed.disconfirming_signal ?? 'Нет данных.').trim() || 'Нет данных.',
    };
  } catch {
    return {
      summary: '',
      primary_driver: 'Нет данных.',
      disconfirming_signal: 'Нет данных.',
    };
  }
}

function computeEntryZone(
  marketPrice: number | null,
  researchProbability: number | null,
  recommendedAction: DecisionAction,
): { min: number | null; max: number | null; action: DecisionAction } {
  if (
    marketPrice === null
    || researchProbability === null
    || (recommendedAction !== 'enter_yes' && recommendedAction !== 'enter_no')
  ) {
    return {
      min: null,
      max: null,
      action: recommendedAction,
    };
  }

  const min = recommendedAction === 'enter_yes' ? marketPrice : researchProbability + 2;
  const max = recommendedAction === 'enter_yes' ? researchProbability - 2 : marketPrice;

  if (max < min) {
    return {
      min: null,
      max: null,
      action: 'review',
    };
  }

  return {
    min,
    max,
    action: recommendedAction,
  };
}

function computeRecommendedAction(
  bestProbability: number | null,
  marketPrice: number | null,
  researchStatus: ResearchStatus,
): { action: DecisionAction; edge: number | null; strength: number } {
  if (bestProbability === null || marketPrice === null) {
    return {
      action: 'review',
      edge: null,
      strength: 0,
    };
  }

  const edge = bestProbability - marketPrice;
  const strength = Math.abs(edge);

  if (researchStatus !== 'ON') {
    return {
      action: 'watch',
      edge,
      strength,
    };
  }

  if (strength < REVIEW_SIGNAL_THRESHOLD) {
    return {
      action: 'watch',
      edge,
      strength,
    };
  }

  if (strength < STRONG_SIGNAL_THRESHOLD) {
    return {
      action: 'review',
      edge,
      strength,
    };
  }

  return {
    action: edge > 0 ? 'enter_yes' : 'enter_no',
    edge,
    strength,
  };
}

function mapPredictionToSignal(prediction: PredictionSnapshot): Omit<SignalSnapshot, 'id' | 'status'> {
  // Determine research status based on available data
  let researchStatus: ResearchStatus = 'ON';
  let statusDetail: ResearchStatusDetail = {
    status: 'ON',
    reason: 'Research completed successfully',
    timestamp: new Date().toISOString(),
  };

  if (prediction.dexter_p === null && prediction.naked_p === null) {
    researchStatus = 'ERROR';
    statusDetail = {
      status: 'ERROR',
      reason: 'Both research and naked prediction failed; no forecast available',
      timestamp: new Date().toISOString(),
    };
  } else if (prediction.dexter_p === null && prediction.naked_p !== null) {
    researchStatus = 'PARTIAL';
    statusDetail = {
      status: 'PARTIAL',
      reason: 'Research synthesis failed; using fallback naked R1 only',
      timestamp: new Date().toISOString(),
    };
  }

  const bestProbability = prediction.dexter_p ?? prediction.naked_p ?? null;
  const reasoning = parseResearchFields(prediction.research_brief_json);
  const { action, edge, strength } = computeRecommendedAction(
    bestProbability,
    prediction.market_price,
    researchStatus,
  );
  const entryZone = computeEntryZone(prediction.market_price, prediction.dexter_p, action);

  return {
    prediction_id: prediction.id,
    market_id: prediction.market_id,
    question: prediction.question,
    forecast_timestamp: prediction.forecast_timestamp,
    market_price: prediction.market_price,
    research_probability: prediction.dexter_p,
    naked_probability: prediction.naked_p,
    best_probability: bestProbability,
    signal_edge: edge,
    signal_strength: strength,
    recommended_action: entryZone.action,
    entry_zone_min: entryZone.min,
    entry_zone_max: entryZone.max,
    thesis_summary:
      reasoning.summary
      || prediction.dexter_reasoning
      || 'Нет краткого тезиса для этого сигнала.',
    primary_driver: reasoning.primary_driver,
    disconfirming_signal: reasoning.disconfirming_signal,
    confidence: prediction.dexter_confidence,
    research_status: researchStatus,
    research_status_detail: JSON.stringify(statusDetail),
  };
}

function isoPlusMs(timestamp: string, ms: number): string {
  return new Date(new Date(timestamp).getTime() + ms).toISOString();
}

function minIso(a: string, b: string | null): string {
  if (!b) {
    return a;
  }

  return new Date(a).getTime() <= new Date(b).getTime() ? a : b;
}

function getShadowBook(db: Database): BookRow {
  const row = db
    .query(
      `
        SELECT id, current_balance, max_risk_per_trade_pct, max_total_exposure_pct
        FROM books
        WHERE name = ?1
        LIMIT 1
      `,
    )
    .get(SHADOW_BOOK_NAME) as BookRow | null;

  if (!row) {
    throw new Error(`Missing required book: ${SHADOW_BOOK_NAME}`);
  }

  return row;
}

function getOpenExposureNotional(db: Database, bookId: number): number {
  const row = db
    .query(
      `
        SELECT COALESCE(SUM(notional_usd), 0) AS total
        FROM positions
        WHERE book_id = ?1 AND status = 'open'
      `,
    )
    .get(bookId) as { total?: number } | null;

  return row?.total ?? 0;
}

function nextThesisCycleNumber(db: Database, bookId: number, marketId: string): number {
  const row = db
    .query(
      `
        SELECT COALESCE(MAX(thesis_cycle_number), 0) AS max_cycle
        FROM positions
        WHERE book_id = ?1 AND market_id = ?2
      `,
    )
    .get(bookId, marketId) as { max_cycle?: number } | null;

  return (row?.max_cycle ?? 0) + 1;
}

function getLatestPredictionRows(db: Database, forecastTimestamp: string): PredictionSnapshot[] {
  return db
    .query(
      `
        SELECT
          id,
          market_id,
          question,
          market_price,
          dexter_p,
          naked_p,
          dexter_confidence,
          dexter_reasoning,
          research_brief_json,
          forecast_timestamp,
          end_date
        FROM predictions
        WHERE forecast_timestamp = ?1 AND is_valid = 1
        ORDER BY id ASC
      `,
    )
    .all(forecastTimestamp) as PredictionSnapshot[];
}

function upsertSignal(db: Database, signal: Omit<SignalSnapshot, 'id' | 'status'>): SignalSnapshot {
  db.query(
    `
      INSERT INTO signals (
        prediction_id,
        market_id,
        question,
        forecast_timestamp,
        market_price,
        research_probability,
        naked_probability,
        best_probability,
        signal_edge,
        signal_strength,
        recommended_action,
        entry_zone_min,
        entry_zone_max,
        thesis_summary,
        primary_driver,
        disconfirming_signal,
        confidence,
        research_status,
        research_status_detail,
        status,
        created_at,
        updated_at
      ) VALUES (
        @prediction_id,
        @market_id,
        @question,
        @forecast_timestamp,
        @market_price,
        @research_probability,
        @naked_probability,
        @best_probability,
        @signal_edge,
        @signal_strength,
        @recommended_action,
        @entry_zone_min,
        @entry_zone_max,
        @thesis_summary,
        @primary_driver,
        @disconfirming_signal,
        @confidence,
        @research_status,
        @research_status_detail,
        'new',
        @forecast_timestamp,
        @forecast_timestamp
      )
      ON CONFLICT(prediction_id) DO UPDATE SET
        market_id = excluded.market_id,
        question = excluded.question,
        forecast_timestamp = excluded.forecast_timestamp,
        market_price = excluded.market_price,
        research_probability = excluded.research_probability,
        naked_probability = excluded.naked_probability,
        best_probability = excluded.best_probability,
        signal_edge = excluded.signal_edge,
        signal_strength = excluded.signal_strength,
        recommended_action = excluded.recommended_action,
        entry_zone_min = excluded.entry_zone_min,
        entry_zone_max = excluded.entry_zone_max,
        thesis_summary = excluded.thesis_summary,
        primary_driver = excluded.primary_driver,
        disconfirming_signal = excluded.disconfirming_signal,
        confidence = excluded.confidence,
        research_status = excluded.research_status,
        research_status_detail = excluded.research_status_detail,
        status = 'new',
        updated_at = excluded.updated_at
    `,
  ).run(signal);

  db.query(
    `
      UPDATE signals
      SET status = 'stale', updated_at = ?2
      WHERE market_id = ?1 AND prediction_id != ?3 AND status != 'consumed'
    `,
  ).run(signal.market_id, signal.forecast_timestamp, signal.prediction_id);

  return db
    .query(
      `
        SELECT *
        FROM signals
        WHERE prediction_id = ?1
        LIMIT 1
      `,
    )
    .get(signal.prediction_id) as SignalSnapshot;
}

function getOpenPositionsForMarket(db: Database, marketId: string): PositionRow[] {
  return db
    .query(
      `
        SELECT
          id,
          book_id,
          market_id,
          signal_id,
          opened_by_decision_id,
          mode,
          side,
          status,
          entry_yes_price,
          entry_cost_per_contract,
          size_contracts,
          notional_usd,
          review_due_at,
          opened_at,
          closed_at,
          closed_yes_price,
          close_reason,
          thesis_cycle_number
        FROM positions
        WHERE market_id = ?1 AND status = 'open'
        ORDER BY id ASC
      `,
    )
    .all(marketId) as PositionRow[];
}

function createDecision(
  db: Database,
  params: {
    signalId: number;
    bookId: number | null;
    marketId: string;
    actorType: 'shadow_engine' | 'operator';
    decisionType: string;
    decisionReason: string;
    operatorConfidence: string | null;
    confirmationCriteria: string | null;
    invalidationCriteria: string | null;
    reviewDueAt: string | null;
    createdAt: string;
  },
): number {
  const existing = db
    .query(
      `
        SELECT id
        FROM decisions
        WHERE signal_id = ?1 AND actor_type = ?2 AND decision_type = ?3
        LIMIT 1
      `,
    )
    .get(params.signalId, params.actorType, params.decisionType) as { id: number } | null;

  if (existing) {
    return existing.id;
  }

  db.query(
    `
      INSERT INTO decisions (
        signal_id,
        book_id,
        market_id,
        actor_type,
        decision_type,
        decision_reason,
        operator_confidence,
        confirmation_criteria,
        invalidation_criteria,
        review_due_at,
        status,
        created_at
      ) VALUES (
        @signalId,
        @bookId,
        @marketId,
        @actorType,
        @decisionType,
        @decisionReason,
        @operatorConfidence,
        @confirmationCriteria,
        @invalidationCriteria,
        @reviewDueAt,
        'active',
        @createdAt
      )
    `,
  ).run(params);

  return (db.query('SELECT last_insert_rowid() AS id').get() as { id: number }).id;
}

function createPositionEvent(
  db: Database,
  params: {
    positionId: number;
    decisionId: number | null;
    eventType: string;
    eventNote: string;
    marketPrice: number | null;
    researchProbability: number | null;
    createdAt: string;
    payload: Record<string, unknown>;
  },
): void {
  const payloadJson = JSON.stringify(params.payload);
  const existing = db
    .query(
      `
        SELECT 1
        FROM position_events
        WHERE position_id = ?1 AND event_type = ?2 AND created_at = ?3
        LIMIT 1
      `,
    )
    .get(params.positionId, params.eventType, params.createdAt);

  if (existing) {
    return;
  }

  db.query(
    `
      INSERT INTO position_events (
        position_id,
        decision_id,
        event_type,
        event_note,
        market_price,
        research_probability,
        created_at,
        payload_json
      ) VALUES (
        @positionId,
        @decisionId,
        @eventType,
        @eventNote,
        @marketPrice,
        @researchProbability,
        @createdAt,
        @payloadJson
      )
    `,
  ).run(
    params.positionId,
    params.decisionId,
    params.eventType,
    params.eventNote,
    params.marketPrice,
    params.researchProbability,
    params.createdAt,
    payloadJson,
  );
}

function positionValuePerContract(side: PositionSide, yesPrice: number): number {
  return side === 'yes' ? yesPrice / 100 : (100 - yesPrice) / 100;
}

/**
 * Calculate position size based on signal strength, research confidence, and portfolio volatility
 * Uses sigmoid for edge factor to scale smoothly from 0 to 1
 */
function calculatePositionSize(params: PositionSizingParams): PositionSizingResult {
  // Edge factor: sigmoid-like curve
  // 0% edge → 0.1, 7% edge → 0.4, 15% edge → 0.85, 25%+ edge → 0.95
  const edgeFactor = Math.min(1.0, Math.max(0.0, params.signalEdge / 20));

  // Confidence multiplier
  const confidenceFactor: Record<string, number> = {
    high: 1.0,
    medium: 0.65,
    low: 0.3,
  };
  const confidence = confidenceFactor[params.researchConfidence] ?? 0.65;

  // Volatility adjustment (inverse: high vol → smaller size)
  // Normal vol ~15%, if vol > 30 → scale down
  const baseVolAssumption = 15;
  const volFactor = Math.max(0.5, Math.min(1.0, baseVolAssumption / Math.max(1, params.portfolioRecentVol)));

  // Combine factors
  const riskBudget = params.baseCapital * (params.maxRiskPercentage / 100);
  const sizedRiskBudget = riskBudget * edgeFactor * confidence * volFactor;

  // Use yes price assuming Polymarket binary markets ~50% midpoint
  const yesPrice = 50;
  const valuePerContract = positionValuePerContract('yes', yesPrice);
  const sizeContracts = Math.floor(sizedRiskBudget / valuePerContract);
  const notionalUsd = sizeContracts * valuePerContract * 100;

  return {
    sizeContracts: Math.max(1, sizeContracts),
    notionalUsd: Math.max(50, notionalUsd), // minimum $50
    factors: {
      edgeFactor,
      confidenceFactor: confidence,
      volFactor,
    },
  };
}

/**
 * Calculate recent portfolio volatility based on closed positions
 * Uses realized P&L variance over recent positions
 */
function calculatePortfolioVolatility(db: Database, bookId: number): number {
  const recentPositions = db
    .query(
      `
        SELECT pnl_percent
        FROM positions
        WHERE book_id = ?1 AND closed_at IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT 20
      `,
    )
    .all(bookId) as { pnl_percent: number | null }[];

  if (recentPositions.length < 3) {
    return 15; // Default assumption if not enough history
  }

  const returns = recentPositions
    .filter((p) => p.pnl_percent !== null)
    .map((p) => (p.pnl_percent ?? 0) / 100);

  if (returns.length === 0) {
    return 15;
  }

  // Calculate realized volatility (std dev of returns)
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;
  const stdDev = Math.sqrt(variance);

  // Return as percentage (0-100), cap at 50%
  return Math.min(50, Math.max(1, stdDev * 100));
}

function maybeCreateDueReview(db: Database, position: PositionRow, forecastTimestamp: string): void {
  if (!position.review_due_at || new Date(position.review_due_at).getTime() > new Date(forecastTimestamp).getTime()) {
    return;
  }

  const existing = db
    .query(
      `
        SELECT id
        FROM reviews
        WHERE position_id = ?1 AND status IN ('due', 'scheduled')
        LIMIT 1
      `,
    )
    .get(position.id) as { id: number } | null;

  if (existing) {
    return;
  }

  db.query(
    `
      INSERT INTO reviews (
        position_id,
        market_id,
        review_type,
        due_at,
        status,
        review_note,
        resolved_at,
        created_at,
        updated_at
      ) VALUES (
        @positionId,
        @marketId,
        'time_review',
        @dueAt,
        'due',
        'Review overdue without confirmation from a stronger follow-up signal.',
        NULL,
        @createdAt,
        @createdAt
      )
    `,
  ).run({
    positionId: position.id,
    marketId: position.market_id,
    dueAt: position.review_due_at,
    createdAt: forecastTimestamp,
  });

  // Create position event for the review being created
  createPositionEvent(db, {
    positionId: position.id,
    decisionId: null,
    eventType: 'review_due',
    eventNote: 'Review became due because position_review_window expired without a confirming signal.',
    marketPrice: null,
    researchProbability: null,
    createdAt: forecastTimestamp,
    payload: {
      review_due_at: position.review_due_at,
      review_type: 'time_review',
    },
  });
}

function resolveOpenReviews(db: Database, positionId: number, resolvedAt: string, note: string, status: 'done' | 'cancelled'): void {
  db.query(
    `
      UPDATE reviews
      SET status = ?2, review_note = COALESCE(review_note, ?3), resolved_at = ?1, updated_at = ?1
      WHERE position_id = ?4 AND status IN ('due', 'scheduled')
    `,
  ).run(resolvedAt, status, note, positionId);
}

function createOrUpdatePostmortem(
  db: Database,
  params: {
    positionId: number;
    marketId: string;
    outcomeLabel: string;
    pnlUsd: number | null;
    pnlPercent: number | null;
    forecastError: number | null;
    entryError: number | null;
    timingError: number | null;
    managementError: number | null;
    disciplineError: number | null;
    operatorNote: string | null;
    systemNote: string | null;
    timestamp: string;
  },
): void {
  db.query(
    `
      INSERT INTO postmortems (
        position_id,
        market_id,
        outcome_label,
        pnl_usd,
        pnl_percent,
        forecast_error,
        entry_error,
        timing_error,
        management_error,
        discipline_error,
        operator_note,
        system_note,
        created_at,
        updated_at
      ) VALUES (
        @positionId,
        @marketId,
        @outcomeLabel,
        @pnlUsd,
        @pnlPercent,
        @forecastError,
        @entryError,
        @timingError,
        @managementError,
        @disciplineError,
        @operatorNote,
        @systemNote,
        @timestamp,
        @timestamp
      )
      ON CONFLICT(position_id) DO UPDATE SET
        outcome_label = excluded.outcome_label,
        pnl_usd = COALESCE(excluded.pnl_usd, postmortems.pnl_usd),
        pnl_percent = COALESCE(excluded.pnl_percent, postmortems.pnl_percent),
        forecast_error = COALESCE(excluded.forecast_error, postmortems.forecast_error),
        entry_error = COALESCE(excluded.entry_error, postmortems.entry_error),
        timing_error = COALESCE(excluded.timing_error, postmortems.timing_error),
        management_error = COALESCE(excluded.management_error, postmortems.management_error),
        discipline_error = COALESCE(excluded.discipline_error, postmortems.discipline_error),
        operator_note = COALESCE(excluded.operator_note, postmortems.operator_note),
        system_note = COALESCE(excluded.system_note, postmortems.system_note),
        updated_at = excluded.updated_at
    `,
  ).run(params);
}

function adjustBookBalance(db: Database, bookId: number, pnlUsd: number): void {
  db.query(
    `
      UPDATE books
      SET current_balance = current_balance + ?2,
          updated_at = ?1
      WHERE id = ?3
    `,
  ).run(new Date().toISOString(), pnlUsd, bookId);
}

function closePosition(
  db: Database,
  position: PositionRow,
  params: {
    closedAt: string;
    closedYesPrice: number;
    closeReason: PositionCloseReason;
    decisionId?: number | null;
    note: string;
  },
): void {
  if (position.status !== 'open') {
    return;
  }

  const exitValue = positionValuePerContract(position.side, params.closedYesPrice);
  const pnlUsd = position.size_contracts * (exitValue - position.entry_cost_per_contract);
  const pnlPercent = position.notional_usd === 0 ? 0 : (pnlUsd / position.notional_usd) * 100;

  db.query(
    `
      UPDATE positions
      SET
        status = 'closed',
        closed_at = @closedAt,
        closed_yes_price = @closedYesPrice,
        close_reason = @closeReason,
        pnl_usd = @pnlUsd,
        pnl_percent = @pnlPercent
      WHERE id = @positionId
    `,
  ).run({
    positionId: position.id,
    closedAt: params.closedAt,
    closedYesPrice: params.closedYesPrice,
    closeReason: params.closeReason,
    pnlUsd,
    pnlPercent,
  });

  adjustBookBalance(db, position.book_id, pnlUsd);
  resolveOpenReviews(db, position.id, params.closedAt, params.note, 'cancelled');
  createPositionEvent(db, {
    positionId: position.id,
    decisionId: params.decisionId ?? null,
    eventType: 'closed',
    eventNote: params.note,
    marketPrice: params.closedYesPrice,
    researchProbability: null,
    createdAt: params.closedAt,
    payload: {
      close_reason: params.closeReason,
      pnl_usd: pnlUsd,
      pnl_percent: pnlPercent,
    },
  });
  createOrUpdatePostmortem(db, {
    positionId: position.id,
    marketId: position.market_id,
    outcomeLabel: params.closeReason === 'resolved' ? 'resolved' : 'early_exit',
    pnlUsd,
    pnlPercent,
    forecastError: null,
    entryError: null,
    timingError: params.closeReason === 'resolved' ? 0 : 1,
    managementError: 0,
    disciplineError: 0,
    operatorNote: null,
    systemNote: `Position closed via ${params.closeReason}.`,
    timestamp: params.closedAt,
  });
}

async function openShadowPosition(db: Database, signal: SignalSnapshot, prediction: PredictionSnapshot): Promise<void> {
  const book = getShadowBook(db);

  // Check basic constraints
  if (signal.market_price === null || signal.best_probability === null) {
    return;
  }

  const openPositionExists = db
    .query(
      `
        SELECT 1
        FROM positions
        WHERE market_id = ?1 AND book_id = ?2 AND mode = 'shadow' AND status = 'open'
        LIMIT 1
      `,
    )
    .get(signal.market_id, book.id);

  if (openPositionExists) {
    return;
  }

  const existingDecision = db
    .query(
      `
        SELECT id
        FROM decisions
        WHERE signal_id = ?1 AND actor_type = 'shadow_engine'
        LIMIT 1
      `,
    )
    .get(signal.id) as { id: number } | null;

  if (existingDecision) {
    return;
  }

  if (
    signal.recommended_action !== 'enter_yes'
    && signal.recommended_action !== 'enter_no'
  ) {
    return;
  }

  if (
    signal.research_status !== 'ON'
    || !signal.confidence
    || !['medium', 'high'].includes(signal.confidence)
    || signal.entry_zone_min === null
    || signal.entry_zone_max === null
    || signal.market_price < signal.entry_zone_min
    || signal.market_price > signal.entry_zone_max
  ) {
    return;
  }

  // SAFETY PIPELINE: Выполнить все проверки безопасности перед открытием позиции
  const safety = getSafetyPipeline();

  // Функция для получения текущей цены с Polymarket
  const getCurrentPrice = async (marketId: string): Promise<number | null> => {
    try {
      const market = await fetchMarket(marketId);
      if (!market) {
        console.warn(`[SafetyPipeline] Market not found: ${marketId}`);
        return null;
      }
      return marketPriceFromYesOutcome(market);
    } catch (error) {
      console.error(`[SafetyPipeline] Error fetching market price for ${marketId}:`, error);
      return null;
    }
  };

  const safetyCheck = await safety.preExecutionChecks(
    {
      id: signal.id,
      market_id: signal.market_id,
      forecast_timestamp: signal.forecast_timestamp,
      market_price: signal.market_price,
      best_probability: signal.best_probability,
      recommended_action: signal.recommended_action,
    },
    getCurrentPrice
  );

  if (!safetyCheck.passed) {
    console.warn(
      `[SafetyPipeline] Trade rejected for ${signal.market_id}: ${safetyCheck.reason}`
    );

    // Отправить уведомление в Telegram
    await sendTelegramAlert(
      formatSafetyRejectionAlert(signal.market_id, safetyCheck.reason, {
        market_price: signal.market_price,
        best_probability: signal.best_probability,
        recommended_action: signal.recommended_action,
      })
    );

    return;
  }

  console.log(
    `[SafetyPipeline] Trade approved for ${signal.market_id}: ${safetyCheck.reason}`
  );

  // NEW: Calculate position size based on edge, confidence, and volatility
  const edge = signal.best_probability - signal.market_price;
  const portfolioVol = calculatePortfolioVolatility(db, book.id);
  const openExposure = getOpenExposureNotional(db, book.id);
  const remainingExposureBudget = book.current_balance * book.max_total_exposure_pct - openExposure;

  const sizing = calculatePositionSize({
    signalEdge: edge,
    researchConfidence: signal.confidence,
    portfolioRecentVol: portfolioVol,
    baseCapital: book.current_balance,
    maxRiskPercentage: book.max_risk_per_trade_pct * 100,
  });

  // Constrain by remaining exposure budget
  if (sizing.notionalUsd > remainingExposureBudget) {
    // Scale down proportionally
    const scale = remainingExposureBudget / sizing.notionalUsd;
    sizing.sizeContracts = Math.floor(sizing.sizeContracts * scale);
    sizing.notionalUsd = sizing.sizeContracts * positionValuePerContract(
      signal.recommended_action === 'enter_yes' ? 'yes' : 'no',
      signal.market_price,
    ) * 100;
  }

  if (sizing.notionalUsd <= 0 || sizing.sizeContracts <= 0) {
    return;
  }

  const side: PositionSide = signal.recommended_action === 'enter_yes' ? 'yes' : 'no';
  const entryCostPerContract = positionValuePerContract(side, signal.market_price);

  const reviewDueAt = minIso(
    isoPlusMs(signal.forecast_timestamp, POSITION_REVIEW_WINDOW_MS),
    prediction.end_date,
  );

  const decisionId = createDecision(db, {
    signalId: signal.id,
    bookId: book.id,
    marketId: signal.market_id,
    actorType: 'shadow_engine',
    decisionType: signal.recommended_action,
    decisionReason: `auto-open: strong signal (edge=${edge.toFixed(1)}%, conf=${signal.confidence}) within entry zone`,
    operatorConfidence: signal.confidence,
    confirmationCriteria: signal.primary_driver,
    invalidationCriteria: signal.disconfirming_signal,
    reviewDueAt,
    createdAt: signal.forecast_timestamp,
  });

  db.query(
    `
      INSERT INTO positions (
        book_id,
        market_id,
        signal_id,
        opened_by_decision_id,
        mode,
        side,
        status,
        entry_yes_price,
        entry_cost_per_contract,
        entry_zone_min,
        entry_zone_max,
        size_contracts,
        notional_usd,
        review_due_at,
        opened_at,
        thesis_cycle_number
      ) VALUES (
        @bookId,
        @marketId,
        @signalId,
        @decisionId,
        'shadow',
        @side,
        'open',
        @entryYesPrice,
        @entryCostPerContract,
        @entryZoneMin,
        @entryZoneMax,
        @sizeContracts,
        @notionalUsd,
        @reviewDueAt,
        @openedAt,
        @thesisCycleNumber
      )
    `,
  ).run({
    bookId: book.id,
    marketId: signal.market_id,
    signalId: signal.id,
    decisionId,
    side,
    entryYesPrice: signal.market_price,
    entryCostPerContract,
    entryZoneMin: signal.entry_zone_min,
    entryZoneMax: signal.entry_zone_max,
    sizeContracts: sizing.sizeContracts,
    notionalUsd: sizing.notionalUsd,
    reviewDueAt,
    openedAt: signal.forecast_timestamp,
    thesisCycleNumber: nextThesisCycleNumber(db, book.id, signal.market_id),
  });

  const positionId = Number((db.query('SELECT last_insert_rowid() AS id').get() as { id: number }).id);

  // Log sizing factors for transparency
  console.log(
    `[Position Sizing] ${signal.market_id}: edge=${edge.toFixed(1)}%, ` +
    `conf=${signal.confidence}, vol=${portfolioVol.toFixed(1)}% → ` +
    `size=${sizing.sizeContracts} contracts ($${sizing.notionalUsd.toFixed(0)}), ` +
    `factors: edge=${sizing.factors.edgeFactor.toFixed(2)} × conf=${sizing.factors.confidenceFactor.toFixed(2)} × vol=${sizing.factors.volFactor.toFixed(2)}`,
  );

  // SAFETY PIPELINE: Записать сделку для cooldown и deduplication
  safety.recordTrade(signal.market_id, signal.recommended_action);

  createPositionEvent(db, {
    positionId,
    decisionId,
    eventType: 'opened',
    eventNote: 'Shadow position opened from a strong forecast signal.',
    marketPrice: signal.market_price,
    researchProbability: signal.research_probability,
    createdAt: signal.forecast_timestamp,
    payload: {
      recommended_action: signal.recommended_action,
      entry_zone_min: signal.entry_zone_min,
      entry_zone_max: signal.entry_zone_max,
      notional_usd: sizing.notionalUsd,
    },
  });
}

function maybeReviewOrBreakPosition(
  db: Database,
  position: PositionRow,
  signal: SignalSnapshot,
  forecastTimestamp: string,
): void {
  if (signal.market_price === null) {
    return;
  }

  const signalSide: PositionSide | null =
    signal.recommended_action === 'enter_yes'
      ? 'yes'
      : signal.recommended_action === 'enter_no'
        ? 'no'
        : null;

  const movedAgainstPosition =
    (position.side === 'yes' && signal.market_price <= position.entry_yes_price - ADVERSE_MOVE_THRESHOLD)
    || (position.side === 'no' && signal.market_price >= position.entry_yes_price + ADVERSE_MOVE_THRESHOLD);

  const shouldBreak =
    signal.research_status !== 'ON'
    || signalSide === null
    || signalSide !== position.side
    || signal.signal_strength < REVIEW_SIGNAL_THRESHOLD
    || movedAgainstPosition;

  if (shouldBreak) {
    // Log adverse move if that's the reason for breaking
    if (movedAgainstPosition) {
      createPositionEvent(db, {
        positionId: position.id,
        decisionId: null,
        eventType: 'adverse_move',
        eventNote: 'Market moved beyond adverse_move_threshold. Position thesis breakdown triggered.',
        marketPrice: signal.market_price,
        researchProbability: signal.research_probability,
        createdAt: forecastTimestamp,
        payload: {
          market_price: signal.market_price,
          entry_price: position.entry_yes_price,
          position_side: position.side,
          threshold: ADVERSE_MOVE_THRESHOLD,
        },
      });
    }

    closePosition(db, position, {
      closedAt: forecastTimestamp,
      closedYesPrice: signal.market_price,
      closeReason: 'thesis_break',
      note: 'Shadow thesis_break triggered by the refreshed forecast signal.',
    });
    return;
  }

  if (signal.signal_strength >= REVIEW_CONFIRMATION_THRESHOLD) {
    const predictionRow = db
      .query('SELECT end_date FROM predictions WHERE id = ?1')
      .get(signal.prediction_id) as { end_date?: string | null } | null;
    const nextReviewDueAt = minIso(
      isoPlusMs(forecastTimestamp, POSITION_REVIEW_WINDOW_MS),
      predictionRow?.end_date ?? null,
    );

    db.query(
      `
        UPDATE positions
        SET review_due_at = ?2
        WHERE id = ?1
      `,
    ).run(position.id, nextReviewDueAt);
    resolveOpenReviews(
      db,
      position.id,
      forecastTimestamp,
      'Review resolved by a confirming follow-up signal.',
      'done',
    );
    createPositionEvent(db, {
      positionId: position.id,
      decisionId: null,
      eventType: 'reviewed',
      eventNote: 'Position reviewed and reaffirmed by the refreshed forecast signal.',
      marketPrice: signal.market_price,
      researchProbability: signal.research_probability,
      createdAt: forecastTimestamp,
      payload: {
        signal_strength: signal.signal_strength,
        review_due_at: nextReviewDueAt,
      },
    });
  }
}

function refreshOverdueReviews(db: Database, forecastTimestamp: string): void {
  const positions = db
    .query(
      `
        SELECT
          id,
          book_id,
          market_id,
          signal_id,
          opened_by_decision_id,
          mode,
          side,
          status,
          entry_yes_price,
          entry_cost_per_contract,
          size_contracts,
          notional_usd,
          review_due_at,
          opened_at,
          closed_at,
          closed_yes_price,
          close_reason,
          thesis_cycle_number
        FROM positions
        WHERE status = 'open'
      `,
    )
    .all() as PositionRow[];

  for (const position of positions) {
    maybeCreateDueReview(db, position, forecastTimestamp);
  }
}

export async function syncDecisionCoreForForecastRun(db: Database, forecastTimestamp: string): Promise<void> {
  ensureDecisionCoreSchema(db);

  const predictions = getLatestPredictionRows(db, forecastTimestamp);
  const signals = new Map<string, SignalSnapshot>();

  for (const prediction of predictions) {
    const signal = upsertSignal(db, mapPredictionToSignal(prediction));
    signals.set(prediction.market_id, signal);
  }

  for (const [marketId, signal] of signals.entries()) {
    const openPositions = getOpenPositionsForMarket(db, marketId);
    for (const position of openPositions) {
      maybeReviewOrBreakPosition(db, position, signal, forecastTimestamp);
    }
  }

  for (const prediction of predictions) {
    const signal = signals.get(prediction.market_id);
    if (!signal) {
      continue;
    }
    await openShadowPosition(db, signal, prediction);
  }

  refreshOverdueReviews(db, forecastTimestamp);

  // Dual-Loop bus hook (Phase 1). Feature-flagged via DUAL_LOOP_BUS_ENABLED;
  // when off this returns immediately and existing Pythia behaviour is
  // preserved byte-for-byte. Runs in a post-commit microtask so SQLite
  // writes are never blocked by Redis I/O.
  queueMicrotask(() => {
    void publishDecisionsToWatchlistBus(Array.from(signals.values()), forecastTimestamp);
  });
}

async function publishDecisionsToWatchlistBus(
  signals: SignalSnapshot[],
  forecastTimestamp: string,
): Promise<void> {
  const publisher = getWatchlistPublisher();
  if (!publisher.enabled) return;

  for (const signal of signals) {
    const kind = classifyDecision(signal.recommended_action);
    if (kind === 'skip') continue;
    try {
      await publisher.maybePublishDecision({
        marketId: signal.market_id,
        ctfAddress: '',
        recommendedAction: signal.recommended_action,
        pFundamental: signal.best_probability ?? signal.research_probability ?? 0,
        pMarket: signal.market_price ?? null,
        confidence: null,
        version: signal.id,
        traceId: `forecast:${forecastTimestamp}:${signal.market_id}`,
      });
    } catch (err) {
      console.warn(
        JSON.stringify({
          level: 'warn',
          component: 'decision_core',
          msg: 'watchlist publish error',
          market_id: signal.market_id,
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    }
  }
}

export function ensureDecisionCoreSchemaForPythonCompat(db: Database): void {
  ensureDecisionCoreSchema(db);
}
