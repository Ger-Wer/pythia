import type {
  ActionResponse,
  DueReviewsResponse,
  HealthResponse,
  LatestRunResponse,
  LogsResponse,
  ManualDecisionInput,
  ManualDecisionResponse,
  MarketWorkspaceResponse,
  MeResponse,
  OpenPositionsResponse,
  OverviewResponse,
  PositionCloseResponse,
  QualityResponse,
  ReviewCompleteResponse,
  WatchlistResponse,
  WatchDossiersResponse,
  WatchUpdatesResponse,
} from "../types/api";

async function request<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return (await response.json()) as T;
}

export const api = {
  me: () => request<MeResponse>("/api/auth/me"),
  overview: () => request<OverviewResponse>("/api/overview"),
  health: () => request<HealthResponse>("/api/health"),
  quality: () => request<QualityResponse>("/api/quality"),
  logs: () => request<LogsResponse>("/api/logs"),
  latestRun: () => request<LatestRunResponse>("/api/runs/latest"),
  marketWorkspace: (marketId: string) => request<MarketWorkspaceResponse>(`/api/markets/${marketId}/workspace`),
  openPositions: () => request<OpenPositionsResponse>("/api/positions/open"),
  dueReviews: () => request<DueReviewsResponse>("/api/reviews/due"),
  watchlist: () => request<WatchlistResponse>("/api/watchlist"),
  watchlistDossiers: () => request<WatchDossiersResponse>("/api/watchlist/dossiers"),
  watchlistUpdates: () => request<WatchUpdatesResponse>("/api/watchlist/updates"),
  login: (username: string, password: string) =>
    request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  runAction: (action: "forecast" | "resolve" | "weekly") =>
    request<ActionResponse>(`/api/actions/${action}`, { method: "POST" }),
  createManualDecision: (payload: ManualDecisionInput) =>
    request<ManualDecisionResponse>("/api/decisions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  closePosition: (positionId: number, note: string) =>
    request<PositionCloseResponse>(`/api/positions/${positionId}/close`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  completeReview: (reviewId: number, note: string) =>
    request<ReviewCompleteResponse>(`/api/reviews/${reviewId}/complete`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  addWatchlist: (marketId: string, note: string) =>
    request<{ ok: boolean }>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ market_id: marketId, note }),
    }),
  removeWatchlist: (marketId: string) =>
    request<{ ok: boolean }>(`/api/watchlist/${marketId}`, { method: "DELETE" }),
};
