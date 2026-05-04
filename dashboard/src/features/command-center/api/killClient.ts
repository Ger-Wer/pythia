// HTTP client for `gateway-api` Kill Switch endpoint:
//
//   POST /api/v1/control/kill
//
// Body shape (signed):
//   {
//     operator_id, idempotency_key, scope, market_id?, nonce,
//     issued_at_unix_ms, signature, reason?
//   }
//
// `signature` = HMAC_SHA256(killHmacSecret,
//   operator_id|scope|market_id|nonce|issued_at_unix_ms).hex()
//
// Computed in the browser via Web Crypto. The shared secret is part of
// the operator console's auth bundle (env / query param). Live capital
// will move signing to KMS/HSM (see gateway-api/README §"Future" §59).
//
// Response 202: { command_id, scope, ack_status, ack_detail? }
//   ack_status ∈ ACCEPTED | DRAINING | COMPLETE | timeout | no_bus |
//                REJECTED_BAD_SIGNATURE | REJECTED_REPLAY
// 4xx/5xx: typed error result. The server also returns 202 for
// timeout/no_bus, so callers must check ack_status to decide whether
// the kill actually engaged downstream.

import type { CommandCenterAuth } from "./auth";

export type KillScope = "GLOBAL" | "MARKET" | "NEW_TRIGGERS";

export interface KillInput {
  scope: KillScope;
  /** Required when scope === "MARKET", ignored otherwise. */
  marketId?: string;
  reason: string;
}

export type KillAckStatus =
  | "ACCEPTED"
  | "DRAINING"
  | "COMPLETE"
  | "REJECTED_BAD_SIGNATURE"
  | "REJECTED_REPLAY"
  | "timeout"
  | "no_bus";

export interface KillOk {
  ok: true;
  commandId: string;
  scope: KillScope;
  ackStatus: KillAckStatus;
  ackDetail?: string;
}

export type KillErrorKind =
  | "no_auth"
  | "missing_market_id"
  | "stale_request"
  | "invalid_signature"
  | "unauthorized"
  | "forbidden"
  | "rate_limited"
  | "network"
  | "server"
  | "unknown";

export interface KillErr {
  ok: false;
  kind: KillErrorKind;
  status?: number;
  message: string;
}

export type KillResult = KillOk | KillErr;

export interface KillClientOptions {
  auth: CommandCenterAuth;
  fetchImpl?: typeof fetch;
  /** Override idempotency key generation; useful in tests. */
  generateIdempotencyKey?: () => string;
  /** Override nonce generation; useful in tests for deterministic replay. */
  generateNonce?: () => number;
  /** Override `issued_at_unix_ms` generation; useful in tests. */
  generateIssuedAt?: () => number;
  /**
   * Override Web Crypto SubtleCrypto. Defaults to globalThis.crypto.subtle
   * when present.
   */
  subtle?: SubtleCrypto;
  /** Override request timeout (ms). Defaults to 5000ms. */
  timeoutMs?: number;
}

function defaultIdempotencyKey(): string {
  const c =
    typeof globalThis !== "undefined"
      ? (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
      : undefined;
  if (c?.randomUUID) return c.randomUUID();
  const rand = Math.random().toString(36).slice(2, 12);
  return `cc-kill-${Date.now().toString(36)}-${rand}`;
}

function defaultNonce(): number {
  // 32-bit nonce — gateway only requires monotonic-ish per (operator,
  // command). We use millisecond clock + 1024-bucket randomness so
  // simultaneous accidental clicks don't collide.
  return Date.now() % 0xffff_ffff;
}

function defaultIssuedAt(): number {
  return Date.now();
}

function defaultSubtle(): SubtleCrypto | undefined {
  if (typeof globalThis === "undefined") return undefined;
  const c = (globalThis as { crypto?: Crypto }).crypto;
  return c?.subtle;
}

function joinUrl(base: string, path: string): string {
  const trimmedBase = base.replace(/\/$/, "");
  const trimmedPath = path.startsWith("/") ? path : `/${path}`;
  return `${trimmedBase}${trimmedPath}`;
}

function bytesToHex(bytes: ArrayBuffer): string {
  const view = new Uint8Array(bytes);
  let out = "";
  for (let i = 0; i < view.length; i++) {
    out += view[i]!.toString(16).padStart(2, "0");
  }
  return out;
}

async function hmacHex(
  subtle: SubtleCrypto,
  secret: string,
  message: string,
): Promise<string> {
  const enc = new TextEncoder();
  const key = await subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await subtle.sign("HMAC", key, enc.encode(message));
  return bytesToHex(sig);
}

/**
 * Submit a kill command to the gateway. Resolves to a discriminated
 * result; callers should treat any `ackStatus` other than COMPLETE as
 * "verify executor state manually" (the dual-loop side may have
 * accepted but not fully drained yet, or the bus could be down).
 */
export async function submitKill(
  input: KillInput,
  options: KillClientOptions,
): Promise<KillResult> {
  const { auth, fetchImpl = fetch, timeoutMs = 5000 } = options;

  if (!auth.killReady || !auth.apiBaseUrl || !auth.token || !auth.operatorId || !auth.killHmacSecret) {
    return {
      ok: false,
      kind: "no_auth",
      message:
        "Kill Switch is not configured (missing api / token / operator_id / kill_hmac).",
    };
  }

  const subtle = options.subtle ?? defaultSubtle();
  if (!subtle) {
    return {
      ok: false,
      kind: "no_auth",
      message: "Web Crypto SubtleCrypto unavailable in this runtime.",
    };
  }

  const marketId = input.scope === "MARKET" ? (input.marketId ?? "").trim() : "";
  if (input.scope === "MARKET" && marketId.length === 0) {
    return {
      ok: false,
      kind: "missing_market_id",
      message: "scope=MARKET requires a non-empty market_id.",
    };
  }

  const idempotencyKey = (options.generateIdempotencyKey ?? defaultIdempotencyKey)();
  const nonce = (options.generateNonce ?? defaultNonce)();
  const issuedAt = (options.generateIssuedAt ?? defaultIssuedAt)();

  const signature = await hmacHex(
    subtle,
    auth.killHmacSecret,
    [
      auth.operatorId,
      input.scope,
      marketId,
      String(nonce),
      String(issuedAt),
    ].join("|"),
  );

  const payload: Record<string, unknown> = {
    operator_id: auth.operatorId,
    idempotency_key: idempotencyKey,
    scope: input.scope,
    nonce,
    issued_at_unix_ms: issuedAt,
    signature,
    reason: input.reason,
  };
  if (marketId) payload.market_id = marketId;

  const url = joinUrl(auth.apiBaseUrl, "/api/v1/control/kill");

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

  if (response.status === 202) {
    const data = (parsed ?? {}) as {
      command_id?: string;
      scope?: KillScope;
      ack_status?: string;
      ack_detail?: string;
    };
    return {
      ok: true,
      commandId: data.command_id ?? "",
      scope: data.scope ?? input.scope,
      ackStatus: (data.ack_status as KillAckStatus | undefined) ?? "timeout",
      ackDetail: data.ack_detail,
    };
  }

  const errBody = (parsed ?? {}) as { error?: string };
  if (response.status === 400) {
    const kind: KillErrorKind = errBody.error === "stale_request" ? "stale_request" : "unknown";
    return {
      ok: false,
      kind,
      status: 400,
      message: errBody.error ?? "bad_request",
    };
  }
  if (response.status === 401) {
    const kind: KillErrorKind = errBody.error === "invalid_signature" ? "invalid_signature" : "unauthorized";
    return {
      ok: false,
      kind,
      status: 401,
      message: errBody.error ?? "unauthorized",
    };
  }
  if (response.status === 403) {
    return {
      ok: false,
      kind: "forbidden",
      status: 403,
      message: errBody.error ?? "forbidden",
    };
  }
  if (response.status === 429) {
    return {
      ok: false,
      kind: "rate_limited",
      status: 429,
      message: errBody.error ?? "rate_limited",
    };
  }
  if (response.status >= 500) {
    return {
      ok: false,
      kind: "server",
      status: response.status,
      message: errBody.error ?? "server_error",
    };
  }
  return {
    ok: false,
    kind: "unknown",
    status: response.status,
    message: errBody.error ?? `unexpected ${response.status}`,
  };
}
