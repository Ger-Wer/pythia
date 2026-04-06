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

export interface MeResponse {
  authenticated: boolean;
  username: string | null;
}
