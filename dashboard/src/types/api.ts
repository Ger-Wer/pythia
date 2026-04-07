export type StatusLevel = "ON" | "DEGRADED" | "OFF";

export interface StatusSummary {
  system: StatusLevel;
  research: StatusLevel;
}

export interface LatestRunSummary {
  forecast_timestamp: string | null;
  markets_processed: number;
}

export interface DailyDigestItem {
  market_id: string;
  title: string;
  detail: string;
  reason_why_it_matters: string;
}

export interface DailyDigestResponse {
  headline: string;
  summary: string;
  items: DailyDigestItem[];
}

export interface LatestRunItem {
  market_id: string;
  question: string;
  category?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  confidence?: string | null;
  research_status?: StatusLevel | null;
  edge?: number | null;
  volume?: number | null;
  end_date?: string | null;
  operator_note?: string | null;
}

export interface ActionResponse {
  action: string | null;
  status: "success" | "failed" | "idle";
  summary: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  technical_log: string;
  returncode: number | null;
}

export interface QualityGlossaryItem {
  id: string;
  label: string;
  what: string;
  why: string;
  how: string;
}

export interface QualityMetricSummary {
  brier_research: number | null;
  brier_naked: number | null;
  brier_market: number | null;
  edge_research_vs_market: number | null;
  edge_naked_vs_market: number | null;
}

export interface QualityCategoryRow {
  category: string;
  resolved: number;
  brier_research?: number | null;
  brier_market?: number | null;
}

export interface QualityResponse {
  resolved_predictions: number;
  minimum_needed: number;
  status: "INSUFFICIENT_DATA" | "COMPLETE";
  decision: "COLLECT" | "GO" | "PIVOT" | "STOP";
  summary: string;
  metrics: QualityMetricSummary;
  category_breakdown: QualityCategoryRow[];
  calibration_image: string | null;
  glossary: QualityGlossaryItem[];
}

export interface OverviewResponse {
  statuses: StatusSummary;
  valid_predictions: number;
  resolved_predictions: number;
  watchlist_count: number;
  open_shadow_positions: number;
  open_operator_positions: number;
  due_reviews_count: number;
  latest_run: LatestRunSummary;
  top_signals: LatestRunItem[];
  attention_items: Array<{
    title: string;
    detail: string;
    market_id?: string | null;
    action_label?: string | null;
  }>;
  daily_digest: DailyDigestResponse | null;
  next_step: string;
  last_action: ActionResponse;
  research_warning: string;
}

export interface LatestRunResponse {
  forecast_timestamp: string | null;
  markets_processed: number;
  items: LatestRunItem[];
}

export interface HealthCheckItem {
  component: string;
  status: StatusLevel;
  detail: string;
}

export interface HealthResponse {
  system_status: StatusLevel;
  research_status: StatusLevel;
  research_warning: string;
  checks: HealthCheckItem[];
}

export interface LogsResponse {
  latest_file: string | null;
  excerpt: string;
}

export interface MarketDetailsResponse {
  market_id: string;
  question: string;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  confidence?: string | null;
  verdict_summary?: string;
  is_watched?: boolean;
  reasoning?: string | null;
  key_risk?: string | null;
  base_rate?: string | null;
  volume?: number | null;
  end_date?: string | null;
  forecast_timestamp?: string | null;
  last_updated?: string | null;
  history?: Array<{
    forecast_timestamp?: string | null;
    market_price?: number | null;
    research_probability?: number | null;
    naked_probability?: number | null;
  }>;
  transparency?: {
    facts?: string[];
    sources?: string[];
    falsifier?: string | null;
    updated_at?: string | null;
  };
  research: {
    summary?: string;
    key_facts?: string[];
    catalyst_type?: string;
    primary_driver?: string;
    second_order_effects?: string[];
    disconfirming_signal?: string;
    sources?: string[];
  };
}

export interface DecisionSignal {
  id: number;
  market_id: string;
  recommended_action: string;
  signal_strength: number | null;
  signal_edge?: number | null;
  entry_zone_min?: number | null;
  entry_zone_max?: number | null;
  confidence?: string | null;
  research_status?: StatusLevel | null;
  thesis_summary?: string | null;
  primary_driver?: string | null;
  disconfirming_signal?: string | null;
  forecast_timestamp?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
}

export interface PositionSummary {
  id: number;
  market_id: string;
  mode: "shadow" | "operator";
  side: "yes" | "no";
  status: string;
  entry_yes_price?: number | null;
  notional_usd?: number | null;
  review_due_at?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
  closed_yes_price?: number | null;
  close_reason?: string | null;
  pnl_usd?: number | null;
  pnl_percent?: number | null;
  thesis_cycle_number?: number | null;
  question?: string | null;
}

export interface ReviewSummary {
  id: number;
  position_id: number;
  market_id: string;
  review_type: string;
  due_at: string;
  status: string;
  review_note?: string | null;
  resolved_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DecisionSummary {
  id: number;
  signal_id: number;
  market_id: string;
  actor_type: string;
  decision_type: string;
  decision_reason: string;
  operator_confidence?: string | null;
  confirmation_criteria?: string | null;
  invalidation_criteria?: string | null;
  review_due_at?: string | null;
  status?: string | null;
  created_at: string;
  position_id?: number | null;
}

export interface PostmortemSummary {
  position_id: number;
  market_id?: string;
  outcome_label?: string | null;
  pnl_usd?: number | null;
  pnl_percent?: number | null;
  forecast_error?: number | null;
  entry_error?: number | null;
  timing_error?: number | null;
  management_error?: number | null;
  discipline_error?: number | null;
  operator_note?: string | null;
  system_note?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MarketWorkspaceResponse {
  market: MarketDetailsResponse;
  signal: DecisionSignal | null;
  open_positions: PositionSummary[];
  due_reviews: ReviewSummary[];
  recent_decisions: DecisionSummary[];
  latest_postmortem: PostmortemSummary | null;
  watch_dossier: WatchDossier | null;
  watch_updates: WatchUpdate[];
}

export interface OpenPositionsResponse {
  items: PositionSummary[];
}

export interface DueReviewsResponse {
  items: ReviewSummary[];
}

export interface ManualDecisionInput {
  signal_id: number;
  decision_type: string;
  decision_reason: string;
  operator_confidence?: string | null;
  confirmation_criteria?: string | null;
  invalidation_criteria?: string | null;
  open_position: boolean;
  review_due_at?: string | null;
}

export interface ManualDecisionResponse {
  ok: boolean;
  decision_id: number;
  position_id?: number | null;
  market_id: string;
}

export interface PositionCloseResponse {
  position_id: number;
  status: string;
  close_reason?: string | null;
  closed_yes_price?: number | null;
  pnl_usd?: number | null;
  pnl_percent?: number | null;
}

export interface ReviewCompleteResponse {
  ok: boolean;
  review_id: number;
  status: string;
  market_id: string;
}

export interface WatchlistItem {
  market_id: string;
  note: string;
  created_at?: string;
  updated_at?: string;
  question?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  forecast_timestamp?: string | null;
}

export interface WatchlistResponse {
  items: WatchlistItem[];
}

export interface WatchDossier {
  id: number;
  market_id: string;
  question?: string | null;
  status: string;
  updated_at?: string | null;
  next_review_due_at?: string | null;
  freshness_policy?: string | null;
  initial_snapshot_state?: string | null;
  thesis_state?: string | null;
  current_market_price?: number | null;
  current_research_probability?: number | null;
  current_naked_probability?: number | null;
  current_confidence?: string | null;
  current_research_status?: string | null;
  current_thesis_summary?: string | null;
  current_primary_driver?: string | null;
  current_invalidation_signal?: string | null;
  last_status_label?: string | null;
  last_reason_summary?: string | null;
  last_action_summary?: string | null;
  operator_note?: string | null;
}

export interface WatchUpdate {
  id: number;
  market_id: string;
  question?: string | null;
  created_at: string;
  severity: string;
  status_label: string;
  reason_summary: string;
  action_summary: string;
  market_price_before?: number | null;
  market_price_after?: number | null;
  research_probability_before?: number | null;
  research_probability_after?: number | null;
  delivered_to_dashboard_at?: string | null;
  delivered_to_telegram_at?: string | null;
}

export interface WatchDossiersResponse {
  items: WatchDossier[];
}

export interface WatchUpdatesResponse {
  items: WatchUpdate[];
}

export interface MeResponse {
  authenticated: boolean;
  username: string | null;
}
