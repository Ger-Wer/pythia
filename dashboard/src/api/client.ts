import type {
  ActionResponse,
  AttentionItemsResponse,
  AttentionTransitionResponse,
  DueReviewsResponse,
  HealthResponse,
  LatestRunResponse,
  LogsResponse,
  ManualDecisionInput,
  ManualDecisionResponse,
  MarketWorkspaceResponse,
  MeResponse,
  OpenPositionsResponse,
  OperatorVsShadowResponse,
  OverviewResponse,
  PositionCloseResponse,
  QualityResponse,
  ReviewCompleteResponse,
  ShadowPerformanceResponse,
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
  shadowPerformance: (params?: { mode?: string; from_date?: string; to_date?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.mode) searchParams.set("mode", params.mode);
    if (params?.from_date) searchParams.set("from_date", params.from_date);
    if (params?.to_date) searchParams.set("to_date", params.to_date);
    const query = searchParams.toString();
    return request<ShadowPerformanceResponse>(`/api/quality/shadow-performance${query ? `?${query}` : ""}`);
  },
  operatorVsShadow: (params?: { from_date?: string; to_date?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.from_date) searchParams.set("from_date", params.from_date);
    if (params?.to_date) searchParams.set("to_date", params.to_date);
    const query = searchParams.toString();
    return request<OperatorVsShadowResponse>(`/api/quality/operator-vs-shadow${query ? `?${query}` : ""}`);
  },
  logs: () => request<LogsResponse>("/api/logs"),
  latestRun: () => request<LatestRunResponse>("/api/runs/latest"),
  marketWorkspace: (marketId: string) => request<MarketWorkspaceResponse>(`/api/markets/${marketId}/workspace`),
  openPositions: () => request<OpenPositionsResponse>("/api/positions/open"),
  dueReviews: () => request<DueReviewsResponse>("/api/reviews/due"),
  watchlist: () => request<WatchlistResponse>("/api/watchlist"),
  watchlistDossiers: () => request<WatchDossiersResponse>("/api/watchlist/dossiers"),
  watchlistUpdates: () => request<WatchUpdatesResponse>("/api/watchlist/updates"),
  attentionItems: (priority?: "urgent" | "today" | "later", status: "open" | "all" = "open") =>
    request<AttentionItemsResponse>(
      `/api/attention-items?${new URLSearchParams(
        Object.fromEntries(
          Object.entries({
            ...(priority ? { priority } : {}),
            status,
          }).filter(([, value]) => value !== undefined),
        ) as Record<string, string>,
      ).toString()}`,
    ),
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
  transitionAttentionItem: (itemId: number, action: "start" | "close" | "cancel" | "bump_today" | "bump_later") =>
    request<AttentionTransitionResponse>(`/api/attention-items/${itemId}/transition`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
};
