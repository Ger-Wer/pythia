// HTTP client for `gateway-api` signal decision endpoints:
//
//   POST /api/v1/signals/:id/accept
//   POST /api/v1/signals/:id/reject
//   POST /api/v1/signals/:id/snooze
//
// All requests use Bearer token auth and pass `idempotency_key` +
// `expected_version` per the gateway contract. Errors are mapped to a
// discriminated `DecideError` so the UI can react (toast text, optional
// rollback) without re-parsing HTTP semantics each call site.

import type { CommandCenterAuth } from "./auth";

export type DecideAction = "accept" | "reject" | "snooze";

export interface DecideAcceptInput {
  signalId: string;
  expectedVersion: number;
}
export interface DecideRejectInput extends DecideAcceptInput {
  reason: string;
}
export interface DecideSnoozeInput extends DecideAcceptInput {
  snoozeUntilUnixMs: number;
}

export interface DecideOk {
  ok: true;
  decisionId: string;
  action: "ACCEPT" | "REJECT" | "SNOOZE";
}

export type DecideErrorKind =
  | "no_auth"
  | "version_conflict"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "rate_limited"
  | "network"
  | "server"
  | "unknown";

export interface DecideErr {
  ok: false;
  kind: DecideErrorKind;
  status?: number;
  message: string;
  /** Server-reported actual_version on 409, used to refresh expected_version on retry. */
  actualVersion?: number;
}

export type DecideResult = DecideOk | DecideErr;

export interface DecideClientOptions {
  auth: CommandCenterAuth;
  fetchImpl?: typeof fetch;
  /** Override idempotency key generation; useful in tests. */
  generateIdempotencyKey?: () => string;
  /** Override request timeout (ms). Defaults to 5000ms. */
  timeoutMs?: number;
}

function defaultIdempotencyKey(): string {
  // Prefer crypto.randomUUID when available; fall back to a coarse but
  // sufficiently-unique pseudo random string. The gateway only requires
  // 8..128 chars, no specific format.
  const c =
    typeof globalThis !== "undefined"
      ? (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
      : undefined;
  if (c?.randomUUID) return c.randomUUID();
  const rand = Math.random().toString(36).slice(2, 12);
  return `cc-${Date.now().toString(36)}-${rand}`;
}

function joinUrl(base: string, path: string): string {
  const trimmedBase = base.replace(/\/$/, "");
  const trimmedPath = path.startsWith("/") ? path : `/${path}`;
  return `${trimmedBase}${trimmedPath}`;
}

async function postDecision(
  options: DecideClientOptions,
  signalId: string,
  action: DecideAction,
  body: Record<string, unknown>,
): Promise<DecideResult> {
  const { auth, fetchImpl = fetch, timeoutMs = 5000 } = options;
  if (!auth.ready || !auth.apiBaseUrl || !auth.token || !auth.operatorId) {
    return {
      ok: false,
      kind: "no_auth",
      message:
        "Command Center HTTP auth is not configured (api url / token / operator_id).",
    };
  }

  const url = joinUrl(auth.apiBaseUrl, `/api/v1/signals/${encodeURIComponent(signalId)}/${action}`);
  const idempotencyKey =
    (body.idempotency_key as string | undefined) ??
    (options.generateIdempotencyKey ?? defaultIdempotencyKey)();

  const payload = {
    operator_id: auth.operatorId,
    idempotency_key: idempotencyKey,
    ...body,
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${auth.token}`,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } catch (err) {
    return {
      ok: false,
      kind: "network",
      message: err instanceof Error ? err.message : "network error",
    };
  } finally {
    clearTimeout(timer);
  }

  let parsed: unknown = undefined;
  try {
    parsed = await response.json();
  } catch {
    parsed = undefined;
  }

  if (response.ok || response.status === 202) {
    const data = (parsed ?? {}) as {
      decision_id?: string;
      action?: "ACCEPT" | "REJECT" | "SNOOZE";
    };
    return {
      ok: true,
      decisionId: data.decision_id ?? "",
      action:
        data.action ?? (action.toUpperCase() as "ACCEPT" | "REJECT" | "SNOOZE"),
    };
  }

  const errBody = (parsed ?? {}) as {
    error?: string;
    actual_version?: number;
  };

  if (response.status === 401) {
    return { ok: false, kind: "unauthorized", status: 401, message: errBody.error ?? "unauthorized" };
  }
  if (response.status === 403) {
    return { ok: false, kind: "forbidden", status: 403, message: errBody.error ?? "forbidden" };
  }
  if (response.status === 404) {
    return { ok: false, kind: "not_found", status: 404, message: errBody.error ?? "signal_not_found" };
  }
  if (response.status === 409) {
    return {
      ok: false,
      kind: "version_conflict",
      status: 409,
      message: errBody.error ?? "version_conflict",
      actualVersion: errBody.actual_version,
    };
  }
  if (response.status === 429) {
    return { ok: false, kind: "rate_limited", status: 429, message: errBody.error ?? "rate_limited" };
  }
  if (response.status >= 500) {
    return { ok: false, kind: "server", status: response.status, message: errBody.error ?? "server_error" };
  }
  return {
    ok: false,
    kind: "unknown",
    status: response.status,
    message: errBody.error ?? `unexpected ${response.status}`,
  };
}

export function decideAccept(
  input: DecideAcceptInput,
  options: DecideClientOptions,
): Promise<DecideResult> {
  return postDecision(options, input.signalId, "accept", {
    expected_version: input.expectedVersion,
  });
}

export function decideReject(
  input: DecideRejectInput,
  options: DecideClientOptions,
): Promise<DecideResult> {
  return postDecision(options, input.signalId, "reject", {
    expected_version: input.expectedVersion,
    reason: input.reason,
  });
}

export function decideSnooze(
  input: DecideSnoozeInput,
  options: DecideClientOptions,
): Promise<DecideResult> {
  return postDecision(options, input.signalId, "snooze", {
    expected_version: input.expectedVersion,
    snooze_until_unix_ms: input.snoozeUntilUnixMs,
  });
}
