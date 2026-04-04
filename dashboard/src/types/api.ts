export interface OverviewResponse {
  valid_predictions: number;
  latest_run: {
    forecast_timestamp: string | null;
    markets_processed: number;
  };
}

export interface LatestRunItem {
  market_id: string;
  question: string;
  category?: string | null;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  edge?: number | null;
  volume?: number | null;
  end_date?: string | null;
}

export interface LatestRunResponse {
  forecast_timestamp: string | null;
  markets_processed: number;
  items: LatestRunItem[];
}

export interface MarketDetailsResponse {
  market_id: string;
  question: string;
  market_price?: number | null;
  research_probability?: number | null;
  naked_probability?: number | null;
  confidence?: string | null;
  reasoning?: string | null;
  key_risk?: string | null;
  base_rate?: string | null;
  volume?: number | null;
  end_date?: string | null;
  forecast_timestamp?: string | null;
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
