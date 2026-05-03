// Resolve HTTP API base URL, PASETO token, and operator_id used by the
// Command Center write-path (Accept / Reject / Snooze / Kill). Sources, in
// priority order:
//
// 1. explicit option overrides (test hooks),
// 2. URL query string (?token=, ?operator_id=, ?cc_api_url=, ?api_url=),
// 3. import.meta.env.VITE_COMMAND_CENTER_* build-time env vars,
// 4. derived from the WS URL when it is an http(s)/ws(s) URL pointing at
//    the same gateway-api host (we strip a trailing /ws/v1 path).
//
// When any of {api base, token, operator_id} is missing we return a
// degraded auth — callers must check `auth.ready` and fall back to
// optimistic-only behavior (mock feed / preview mode).

export interface CommandCenterAuth {
  ready: boolean;
  apiBaseUrl?: string;
  token?: string;
  operatorId?: string;
}

export interface ResolveAuthOptions {
  apiBaseUrl?: string;
  token?: string;
  operatorId?: string;
  wsUrl?: string;
  locationSearch?: string;
}

function readQuery(search: string | undefined, key: string): string | undefined {
  if (!search) return undefined;
  const params = new URLSearchParams(search);
  const v = params.get(key);
  return v ? v.trim() : undefined;
}

function readEnv(key: string): string | undefined {
  // Accessing import.meta.env outside a Vite-built bundle (e.g. raw Vitest
  // unit tests) may surface as `undefined`; treat missing as unset.
  const v = (import.meta.env as Record<string, string | undefined>)[key];
  return v ? v.trim() : undefined;
}

function deriveHttpBaseFromWs(wsUrl: string | undefined): string | undefined {
  if (!wsUrl) return undefined;
  try {
    const parsed = new URL(
      wsUrl,
      typeof window !== "undefined" ? window.location.href : "http://localhost",
    );
    if (parsed.protocol === "ws:") parsed.protocol = "http:";
    else if (parsed.protocol === "wss:") parsed.protocol = "https:";
    parsed.searchParams.delete("token");
    // Gateway routes WS at `/ws/v1` and REST under `/api/v1/*`; the API
    // base is just origin.
    parsed.pathname = "";
    parsed.search = "";
    parsed.hash = "";
    // Strip trailing slash for consistency.
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return undefined;
  }
}

export function resolveCommandCenterAuth(
  options: ResolveAuthOptions = {},
): CommandCenterAuth {
  const search =
    options.locationSearch ??
    (typeof window !== "undefined" ? window.location.search : "");

  const queryApi =
    readQuery(search, "cc_api_url") ?? readQuery(search, "api_url");
  const queryToken = readQuery(search, "token");
  const queryOperator = readQuery(search, "operator_id");

  const envApi = readEnv("VITE_COMMAND_CENTER_API_URL");
  const envToken = readEnv("VITE_COMMAND_CENTER_WS_TOKEN");
  const envOperator = readEnv("VITE_COMMAND_CENTER_OPERATOR_ID");

  const wsUrl =
    options.wsUrl ??
    readQuery(search, "cc_ws_url") ??
    readQuery(search, "ws_url") ??
    readEnv("VITE_COMMAND_CENTER_WS_URL");

  const apiBaseUrl =
    options.apiBaseUrl ?? queryApi ?? envApi ?? deriveHttpBaseFromWs(wsUrl);
  const token = options.token ?? queryToken ?? envToken;
  const operatorId = options.operatorId ?? queryOperator ?? envOperator;

  const ready = Boolean(apiBaseUrl && token && operatorId);
  return { ready, apiBaseUrl, token, operatorId };
}
