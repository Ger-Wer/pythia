import type {
  ActionResponse,
  LatestRunResponse,
  MarketDetailsResponse,
  MeResponse,
  OverviewResponse,
  WatchlistResponse,
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
  latestRun: () => request<LatestRunResponse>("/api/runs/latest"),
  marketDetails: (marketId: string) => request<MarketDetailsResponse>(`/api/markets/${marketId}`),
  watchlist: () => request<WatchlistResponse>("/api/watchlist"),
  login: (username: string, password: string) =>
    request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  runAction: (action: "forecast" | "resolve" | "weekly") =>
    request<ActionResponse>(`/api/actions/${action}`, { method: "POST" }),
  addWatchlist: (marketId: string, note: string) =>
    request<{ ok: boolean }>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ market_id: marketId, note }),
    }),
  removeWatchlist: (marketId: string) =>
    request<{ ok: boolean }>(`/api/watchlist/${marketId}`, { method: "DELETE" }),
};
