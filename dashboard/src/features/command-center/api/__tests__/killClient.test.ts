import { describe, expect, it, vi } from "vitest";
import { submitKill } from "../killClient";
import type { CommandCenterAuth } from "../auth";

const KILL_AUTH: CommandCenterAuth = {
  ready: true,
  killReady: true,
  apiBaseUrl: "https://gateway.example/",
  token: "v4.local.test-token",
  operatorId: "op-1",
  killHmacSecret: "test-hmac-secret",
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetchOk(body: unknown, status = 202): typeof fetch {
  return vi.fn(async () => jsonResponse(status, body)) as unknown as typeof fetch;
}

function mockFetchErr(status: number, body: unknown): typeof fetch {
  return vi.fn(async () => jsonResponse(status, body)) as unknown as typeof fetch;
}

function lastCall(fetchImpl: typeof fetch): { url: string; init: RequestInit } {
  const mock = (fetchImpl as unknown as { mock: { calls: unknown[][] } }).mock;
  const call = mock.calls[mock.calls.length - 1];
  if (!call) throw new Error("fetch was not called");
  return { url: call[0] as string, init: call[1] as RequestInit };
}

async function expectedHmacHex(secret: string, parts: string[]): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(parts.join("|")));
  const view = new Uint8Array(sig);
  let out = "";
  for (let i = 0; i < view.length; i++) {
    out += view[i]!.toString(16).padStart(2, "0");
  }
  return out;
}

describe("killClient.submitKill", () => {
  it("posts GLOBAL kill with operator_id|scope||nonce|issuedAt HMAC + reason", async () => {
    const fetchImpl = mockFetchOk({
      command_id: "cmd-1",
      scope: "GLOBAL",
      ack_status: "COMPLETE",
      ack_detail: "ok",
    });
    const result = await submitKill(
      { scope: "GLOBAL", reason: "test drill" },
      {
        auth: KILL_AUTH,
        fetchImpl,
        generateIdempotencyKey: () => "idem-fixed-kill",
        generateNonce: () => 77,
        generateIssuedAt: () => 1_770_000_000_000,
      },
    );

    expect(result).toEqual({
      ok: true,
      commandId: "cmd-1",
      scope: "GLOBAL",
      ackStatus: "COMPLETE",
      ackDetail: "ok",
    });

    const { url, init } = lastCall(fetchImpl);
    expect(url).toBe("https://gateway.example/api/v1/control/kill");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer v4.local.test-token");
    expect(headers["Content-Type"]).toBe("application/json");

    const body = JSON.parse(init.body as string);
    const expectedSig = await expectedHmacHex("test-hmac-secret", [
      "op-1",
      "GLOBAL",
      "",
      "77",
      "1770000000000",
    ]);
    expect(body).toEqual({
      operator_id: "op-1",
      idempotency_key: "idem-fixed-kill",
      scope: "GLOBAL",
      nonce: 77,
      issued_at_unix_ms: 1_770_000_000_000,
      signature: expectedSig,
      reason: "test drill",
    });
    expect(body.market_id).toBeUndefined();
  });

  it("posts MARKET kill with market_id field included", async () => {
    const fetchImpl = mockFetchOk({
      command_id: "cmd-2",
      scope: "MARKET",
      ack_status: "ACCEPTED",
    });
    const result = await submitKill(
      { scope: "MARKET", marketId: "poly-42", reason: "thin liq" },
      {
        auth: KILL_AUTH,
        fetchImpl,
        generateIdempotencyKey: () => "idem-fixed-kill-2",
        generateNonce: () => 1,
        generateIssuedAt: () => 1_770_000_001_000,
      },
    );
    expect(result.ok).toBe(true);

    const { init } = lastCall(fetchImpl);
    const body = JSON.parse(init.body as string);
    const expectedSig = await expectedHmacHex("test-hmac-secret", [
      "op-1",
      "MARKET",
      "poly-42",
      "1",
      "1770000001000",
    ]);
    expect(body).toMatchObject({
      scope: "MARKET",
      market_id: "poly-42",
      signature: expectedSig,
    });
  });

  it("rejects MARKET scope with empty market_id without calling fetch", async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const result = await submitKill(
      { scope: "MARKET", marketId: "", reason: "x" },
      { auth: KILL_AUTH, fetchImpl },
    );
    expect(result.ok).toBe(false);
    expect((result as { kind: string }).kind).toBe("missing_market_id");
    expect(
      (fetchImpl as unknown as { mock: { calls: unknown[][] } }).mock.calls.length,
    ).toBe(0);
  });

  it("returns no_auth without calling fetch when killReady is false", async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const result = await submitKill(
      { scope: "GLOBAL", reason: "x" },
      {
        auth: {
          ready: true,
          killReady: false,
          apiBaseUrl: "https://gw",
          token: "t",
          operatorId: "op",
          killHmacSecret: undefined,
        },
        fetchImpl,
      },
    );
    expect(result.ok).toBe(false);
    expect((result as { kind: string }).kind).toBe("no_auth");
    expect(
      (fetchImpl as unknown as { mock: { calls: unknown[][] } }).mock.calls.length,
    ).toBe(0);
  });

  it("returns 202 with ack_status=timeout as success result so UI can warn", async () => {
    const fetchImpl = mockFetchOk({
      command_id: "cmd-t",
      scope: "GLOBAL",
      ack_status: "timeout",
    });
    const result = await submitKill(
      { scope: "GLOBAL", reason: "x" },
      { auth: KILL_AUTH, fetchImpl },
    );
    expect(result.ok).toBe(true);
    expect((result as { ackStatus: string }).ackStatus).toBe("timeout");
  });

  it("maps 400 stale_request, 401 invalid_signature, 401 unauthorized, 403, 429, 5xx", async () => {
    const cases: { status: number; body: { error: string }; kind: string }[] = [
      { status: 400, body: { error: "stale_request" }, kind: "stale_request" },
      { status: 400, body: { error: "other" }, kind: "unknown" },
      { status: 401, body: { error: "invalid_signature" }, kind: "invalid_signature" },
      { status: 401, body: { error: "unauthorized" }, kind: "unauthorized" },
      { status: 403, body: { error: "operator_id_mismatch" }, kind: "forbidden" },
      { status: 429, body: { error: "rate_limited" }, kind: "rate_limited" },
      { status: 503, body: { error: "boom" }, kind: "server" },
    ];
    for (const { status, body, kind } of cases) {
      const fetchImpl = mockFetchErr(status, body);
      const result = await submitKill(
        { scope: "GLOBAL", reason: "x" },
        { auth: KILL_AUTH, fetchImpl },
      );
      expect(result.ok).toBe(false);
      expect((result as { kind: string }).kind).toBe(kind);
    }
  });

  it("maps fetch rejection to network error", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    const result = await submitKill(
      { scope: "GLOBAL", reason: "x" },
      { auth: KILL_AUTH, fetchImpl },
    );
    expect(result.ok).toBe(false);
    expect((result as { kind: string; message: string }).kind).toBe("network");
    expect((result as { message: string }).message).toBe("fetch failed");
  });
});
