import type { Database } from './db';
import { ensureWatchlistEngineSchema } from './db';
import type { NewsResult } from './research_runner';

export interface MarketSnapshot {
  marketPrice: number | null;
  researchProbability: number | null;
  nakedProbability: number | null;
}

export interface NewsDigest {
  hits: NewsResult[];
  summary: string;
}

export interface OperatorFacingUpdate {
  severity?: 'info' | 'review' | 'warning' | 'critical';
  statusLabel: string;
  reasonSummary: string;
  actionSummary: string;
}

export interface MonitoringResult {
  monitoringStatus: string;
  thesisState: string;
  statusLabel: string;
  reasonSummary: string;
  actionSummary: string;
  shouldDeepReview: boolean;
  triggerFlags: Record<string, unknown>;
  operatorFacing?: OperatorFacingUpdate | null;
}

export interface DeepReviewResult {
  summary: string;
  primaryDriver?: string | null;
  invalidationSignal?: string | null;
  researchProbability?: number | null;
  nakedProbability?: number | null;
  confidence?: string | null;
  researchStatus?: string | null;
  thesisState?: string | null;
  nextReviewDueAt?: string | null;
  operatorFacing?: OperatorFacingUpdate | null;
}

export interface RefreshTarget {
  id: number;
  market_id: string;
  question: string | null;
  current_market_price: number | null;
  current_research_probability: number | null;
  current_naked_probability: number | null;
  current_confidence: string | null;
  current_research_status: string | null;
  current_thesis_summary: string | null;
  current_primary_driver: string | null;
  current_invalidation_signal: string | null;
  status: string;
  next_review_due_at: string | null;
  has_open_position: number;
}

export interface MonitoringInput {
  dossier: RefreshTarget;
  current: MarketSnapshot;
  newsDigest: NewsDigest;
}

export interface DeepReviewInput {
  dossier: RefreshTarget;
  current: MarketSnapshot;
  newsDigest: NewsDigest;
  monitoring: MonitoringResult;
}

export interface RefreshDependencies {
  fetchMarketSnapshot: (marketId: string) => Promise<MarketSnapshot>;
  fetchNewsDigest: (input: { marketId: string; question: string | null; thesisSummary: string | null }) => Promise<NewsDigest>;
  monitorThesis: (input: MonitoringInput) => Promise<MonitoringResult>;
  deepReview: (input: DeepReviewInput) => Promise<DeepReviewResult>;
}

export interface RefreshRunSummary {
  runId: number;
  marketsChecked: number;
  marketsChanged: number;
  deepReviewsTriggered: number;
  alertsCreated: number;
}

function nowIso(): string {
  return new Date().toISOString();
}

function toJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 0);
}

/**
 * Check if market has exceeded update limit (kill-switch)
 * Returns number of updates in last 24 hours
 */
function getRecentUpdateCount(db: Database, dossierId: number): number {
  const twentyFourHoursAgo = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();

  const result = db.query(`
    SELECT COUNT(*) as count
    FROM watch_updates
    WHERE watch_dossier_id = ?1
    AND created_at >= ?2
  `).get(dossierId, twentyFourHoursAgo) as { count: number } | undefined;

  return result?.count ?? 0;
}

/**
 * Check if market should be suppressed due to too many updates
 * Kill-switch: max 2 updates per market per 24h
 */
function shouldSuppressUpdate(db: Database, dossierId: number, maxUpdatesPerDay: number = 2): boolean {
  const recentCount = getRecentUpdateCount(db, dossierId);
  return recentCount >= maxUpdatesPerDay;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return String(error);
}

export function countRefreshTargets(db: Database): number {
  const row = db
    .query(
      `
        SELECT COUNT(*) as count
        FROM watch_dossiers
        WHERE status IN ('active', 'active_error')
      `,
    )
    .get() as { count: number } | null;

  return Number(row?.count ?? 0);
}

export function collectRefreshTargets(db: Database): RefreshTarget[] {
  return db
    .query(
      `
        SELECT
          d.id,
          d.market_id,
          (
            SELECT p.question
            FROM predictions p
            WHERE p.market_id = d.market_id
              AND COALESCE(p.is_valid, 1) = 1
            ORDER BY p.forecast_timestamp DESC, p.id DESC
            LIMIT 1
          ) AS question,
          d.current_market_price,
          d.current_research_probability,
          d.current_naked_probability,
          d.current_confidence,
          d.current_research_status,
          d.current_thesis_summary,
          d.current_primary_driver,
          d.current_invalidation_signal,
          d.status,
          d.next_review_due_at,
          CASE
            WHEN EXISTS (
              SELECT 1
              FROM positions p
              WHERE p.market_id = d.market_id
                AND p.status = 'open'
            ) THEN 1
            ELSE 0
          END AS has_open_position
        FROM watch_dossiers d
        WHERE d.status IN ('active', 'active_error')
        ORDER BY has_open_position DESC, d.updated_at DESC, d.market_id ASC
      `,
    )
    .all() as RefreshTarget[];
}

function mergeSnapshotWithDossier(dossier: RefreshTarget, current: MarketSnapshot): MarketSnapshot {
  return {
    marketPrice: current.marketPrice ?? dossier.current_market_price,
    researchProbability: current.researchProbability ?? dossier.current_research_probability,
    nakedProbability: current.nakedProbability ?? dossier.current_naked_probability,
  };
}

function persistTechnicalSnapshot(
  db: Database,
  input: {
    runId: number;
    dossierId: number;
    previousMarketPrice: number | null;
    current: MarketSnapshot;
    monitoring: MonitoringResult;
    newsDigest: NewsDigest;
  },
): void {
  const edgeVsMarket =
    input.current.researchProbability !== null && input.current.marketPrice !== null
      ? input.current.researchProbability - input.current.marketPrice
      : null;

  const priceDeltaSinceLast =
    input.previousMarketPrice !== null && input.current.marketPrice !== null
      ? input.current.marketPrice - input.previousMarketPrice
      : null;

  db.query(
    `
      INSERT INTO watch_refresh_snapshots (
        watch_dossier_id,
        refresh_run_id,
        captured_at,
        market_price,
        research_probability,
        naked_probability,
        edge_vs_market,
        price_delta_since_last,
        thesis_state,
        research_status,
        monitoring_status,
        trigger_flags_json,
        news_digest_json,
        monitoring_output_json
      ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)
    `,
  ).run(
    input.dossierId,
    input.runId,
    nowIso(),
    input.current.marketPrice,
    input.current.researchProbability,
    input.current.nakedProbability,
    edgeVsMarket,
    priceDeltaSinceLast,
    input.monitoring.thesisState,
    input.current.researchProbability !== null ? 'ON' : 'DEGRADED',
    input.monitoring.monitoringStatus,
    toJson(input.monitoring.triggerFlags),
    toJson({
      summary: input.newsDigest.summary,
      hits: input.newsDigest.hits,
    }),
    toJson({
      monitoringStatus: input.monitoring.monitoringStatus,
      thesisState: input.monitoring.thesisState,
      statusLabel: input.monitoring.statusLabel,
      reasonSummary: input.monitoring.reasonSummary,
      actionSummary: input.monitoring.actionSummary,
      shouldDeepReview: input.monitoring.shouldDeepReview,
    }),
  );
}

function updateDossierAfterMonitoring(
  db: Database,
  dossier: RefreshTarget,
  current: MarketSnapshot,
  monitoring: MonitoringResult,
): void {
  const updatedAt = nowIso();
  db.query(
    `
      UPDATE watch_dossiers
      SET status = 'active',
          updated_at = ?2,
          last_refresh_at = ?2,
          current_market_price = ?3,
          current_research_probability = ?4,
          current_naked_probability = ?5,
          current_research_status = ?6,
          thesis_state = ?7,
          last_status_label = ?8,
          last_reason_summary = ?9,
          last_action_summary = ?10
      WHERE id = ?1
    `,
  ).run(
    dossier.id,
    updatedAt,
    current.marketPrice,
    current.researchProbability ?? dossier.current_research_probability,
    current.nakedProbability ?? dossier.current_naked_probability,
    current.researchProbability !== null
      ? 'ON'
      : dossier.current_research_status ?? 'DEGRADED',
    monitoring.thesisState,
    monitoring.statusLabel,
    monitoring.reasonSummary,
    monitoring.actionSummary,
  );
}

function applyDeepReviewResult(
  db: Database,
  dossier: RefreshTarget,
  current: MarketSnapshot,
  review: DeepReviewResult,
): void {
  const updatedAt = nowIso();
  db.query(
    `
      UPDATE watch_dossiers
      SET status = 'active',
          updated_at = ?2,
          last_deep_review_at = ?2,
          next_review_due_at = COALESCE(?3, next_review_due_at),
          thesis_state = COALESCE(?4, thesis_state),
          current_market_price = ?5,
          current_research_probability = COALESCE(?6, current_research_probability),
          current_naked_probability = COALESCE(?7, current_naked_probability),
          current_confidence = COALESCE(?8, current_confidence),
          current_research_status = COALESCE(?9, current_research_status),
          current_thesis_summary = COALESCE(?10, current_thesis_summary),
          current_primary_driver = COALESCE(?11, current_primary_driver),
          current_invalidation_signal = COALESCE(?12, current_invalidation_signal)
      WHERE id = ?1
    `,
  ).run(
    dossier.id,
    updatedAt,
    review.nextReviewDueAt ?? null,
    review.thesisState ?? null,
    current.marketPrice,
    review.researchProbability ?? null,
    review.nakedProbability ?? null,
    review.confidence ?? null,
    review.researchStatus ?? null,
    review.summary || null,
    review.primaryDriver ?? null,
    review.invalidationSignal ?? null,
  );
}

function createWatchUpdate(
  db: Database,
  dossier: RefreshTarget,
  runId: number,
  current: MarketSnapshot,
  update: OperatorFacingUpdate,
): void {
  db.query(
    `
      INSERT INTO watch_updates (
        watch_dossier_id,
        refresh_run_id,
        created_at,
        severity,
        status_label,
        reason_summary,
        action_summary,
        market_price_before,
        market_price_after,
        research_probability_before,
        research_probability_after,
        change_summary_json,
        delivered_to_dashboard_at
      ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)
    `,
  ).run(
    dossier.id,
    runId,
    nowIso(),
    update.severity ?? 'review',
    update.statusLabel,
    update.reasonSummary,
    update.actionSummary,
    dossier.current_market_price,
    current.marketPrice,
    dossier.current_research_probability,
    current.researchProbability,
    toJson({
      from: {
        marketPrice: dossier.current_market_price,
        researchProbability: dossier.current_research_probability,
      },
      to: {
        marketPrice: current.marketPrice,
        researchProbability: current.researchProbability,
      },
    }),
    nowIso(),
  );
}

function appendRunError(db: Database, runId: number, marketId: string, error: unknown): void {
  const existing = db
    .query(
      `
        SELECT error_summary
        FROM watch_refresh_runs
        WHERE id = ?1
      `,
    )
    .get(runId) as { error_summary: string } | null;

  const message = `${marketId}: ${safeErrorMessage(error)}`;
  const nextSummary = existing?.error_summary ? `${existing.error_summary}\n${message}` : message;

  db.query(
    `
      UPDATE watch_refresh_runs
      SET error_summary = ?2
      WHERE id = ?1
    `,
  ).run(runId, nextSummary);
}

function markDossierRefreshError(db: Database, dossier: RefreshTarget, error: unknown): void {
  const message = safeErrorMessage(error);
  db.query(
    `
      UPDATE watch_dossiers
      SET status = 'active_error',
          updated_at = ?2,
          last_refresh_at = ?2,
          thesis_state = 'review_required',
          last_status_label = 'техническая ошибка',
          last_reason_summary = ?3,
          last_action_summary = 'Повторить refresh или открыть рынок вручную.'
      WHERE id = ?1
    `,
  ).run(dossier.id, nowIso(), message);
}

export async function runWatchlistRefresh(
  db: Database,
  deps: RefreshDependencies,
): Promise<RefreshRunSummary> {
  ensureWatchlistEngineSchema(db);

  const startedAt = nowIso();
  const marketsTotal = countRefreshTargets(db);
  db.query(
    `
      INSERT INTO watch_refresh_runs (
        started_at,
        status,
        markets_total
      ) VALUES (?1, 'running', ?2)
    `,
  ).run(startedAt, marketsTotal);
  const runId = Number(db.query('SELECT last_insert_rowid() AS id').get()?.id ?? 0);

  const dossiers = collectRefreshTargets(db);
  let marketsChecked = 0;
  let marketsChanged = 0;
  let deepReviewsTriggered = 0;
  let alertsCreated = 0;
  let hadErrors = false;

  for (const dossier of dossiers) {
    try {
      const fetchedCurrent = await deps.fetchMarketSnapshot(dossier.market_id);
      const current = mergeSnapshotWithDossier(dossier, fetchedCurrent);
      const newsDigest = await deps.fetchNewsDigest({
        marketId: dossier.market_id,
        question: dossier.question,
        thesisSummary: dossier.current_thesis_summary,
      });
      const monitoring = await deps.monitorThesis({
        dossier,
        current,
        newsDigest,
      });

      persistTechnicalSnapshot(db, {
        runId,
        dossierId: dossier.id,
        previousMarketPrice: dossier.current_market_price,
        current,
        monitoring,
        newsDigest,
      });
      updateDossierAfterMonitoring(db, dossier, current, monitoring);

      marketsChecked += 1;
      let effectiveUpdate = monitoring.operatorFacing ?? null;
      let updateSnapshot = current;

      if (monitoring.shouldDeepReview) {
        deepReviewsTriggered += 1;
        const review = await deps.deepReview({
          dossier,
          current,
          newsDigest,
          monitoring,
        });
        applyDeepReviewResult(db, dossier, current, review);
        effectiveUpdate = review.operatorFacing ?? effectiveUpdate;
        updateSnapshot = {
          marketPrice: current.marketPrice,
          researchProbability: review.researchProbability ?? current.researchProbability,
          nakedProbability: review.nakedProbability ?? current.nakedProbability,
        };
      }

      if (effectiveUpdate) {
        // Kill-switch: check if market has too many updates in last 24h
        const isSuppressed = shouldSuppressUpdate(db, dossier.id);

        if (isSuppressed) {
          const recentCount = getRecentUpdateCount(db, dossier.id);
          console.warn(
            `[Watchlist Kill-Switch] Suppressing update for market ${dossier.market_id}: ` +
            `${recentCount} updates in last 24h (limit: 2)`
          );

          // Log suppression event
          appendRunError(
            db,
            runId,
            dossier.market_id,
            new Error(`Update suppressed: ${recentCount} updates in 24h (kill-switch activated)`)
          );
        } else {
          marketsChanged += 1;
          alertsCreated += 1;
          createWatchUpdate(db, dossier, runId, updateSnapshot, effectiveUpdate);
        }
      }
    } catch (error) {
      hadErrors = true;
      appendRunError(db, runId, dossier.market_id, error);
      markDossierRefreshError(db, dossier, error);
    }
  }

  db.query(
    `
      UPDATE watch_refresh_runs
      SET finished_at = ?2,
          status = ?3,
          markets_checked = ?4,
          markets_changed = ?5,
          deep_reviews_triggered = ?6,
          alerts_created = ?7
      WHERE id = ?1
    `,
  ).run(
    runId,
    nowIso(),
    hadErrors ? 'partial_success' : 'success',
    marketsChecked,
    marketsChanged,
    deepReviewsTriggered,
    alertsCreated,
  );

  return {
    runId,
    marketsChecked,
    marketsChanged,
    deepReviewsTriggered,
    alertsCreated,
  };
}
