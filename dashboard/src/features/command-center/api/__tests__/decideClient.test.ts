import { describe, expect, it, vi } from "vitest";
import {
  decideAccept,
  decideReject,
  decideSnooze,
} from "../decideClient";
import { resolveCommandCenterAuth } from "../auth";

const READY_AUTH = {
  ready: true,
  killReady: false,
  apiBaseUrl: "https://gateway.example/",
  token: "v4.local.test-token",
  operatorId: "op-1",
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

describe("decideClient", () => {
  it("posts accept body to gateway with operator_id, idempotency_key, expected_version", async () => {
    const fetchImpl = mockFetchOk({ decision_id: "dec-1", action: "ACCEPT" });
    const result = await decideAccept(
      { signalId: "sig-1", expectedVersion: 7 },
      {
        auth: READY_AUTH,
        fetchImpl,
        generateIdempotencyKey: () => "idem-fixed-1",
      },
    );

    expect(result).toEqual({ ok: true, decisionId: "dec-1", action: "ACCEPT" });
    const { url, init } = lastCall(fetchImpl);
    expect(url).toBe("https://gateway.example/api/v1/signals/sig-1/accept");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer v4.local.test-token");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      operator_id: "op-1",
      idempotency_key: "idem-fixed-1",
      expected_version: 7,
    });
  });

  it("posts reject body with required reason field", async () => {
    const fetchImpl = mockFetchOk({ decision_id: "dec-2", action: "REJECT" });
    const result = await decideReject(
      { signalId: "sig-2", expectedVersion: 3, reason: "thin liquidity" },
      {
        auth: READY_AUTH,
        fetchImpl,
        generateIdempotencyKey: () => "idem-fixed-2",
      },
    );

    expect(result.ok).toBe(true);
    const { init } = lastCall(fetchImpl);
    expect(JSON.parse(init.body as string)).toMatchObject({
      operator_id: "op-1",
      idempotency_key: "idem-fixed-2",
      expected_version: 3,
      reason: "thin liquidity",
    });
  });

  it("posts snooze body with snooze_until_unix_ms", async () => {
    const fetchImpl = mockFetchOk({ decision_id: "dec-3", action: "SNOOZE" });
    const result = await decideSnooze(
      { signalId: "sig-3", expectedVersion: 0, snoozeUntilUnixMs: 1_770_000_000_000 },
      {
        auth: READY_AUTH,
        fetchImpl,
        generateIdempotencyKey: () => "idem-fixed-3",
      },
    );

    expect(result.ok).toBe(true);
    const { init } = lastCall(fetchImpl);
    expect(JSON.parse(init.body as string)).toMatchObject({
      snooze_until_unix_ms: 1_770_000_000_000,
    });
  });

  it("maps 409 to version_conflict with actual_version", async () => {
    const fetchImpl = mockFetchErr(409, {
      error: "version_conflict",
      signal_id: "sig-1",
      expected_version: 4,
      actual_version: 6,
    });
    const result = await decideAccept(
      { signalId: "sig-1", expectedVersion: 4 },
      { auth: READY_AUTH, fetchImpl },
    );
    expect(result).toEqual({
      ok: false,
      kind: "version_conflict",
      status: 409,
      message: "version_conflict",
      actualVersion: 6,
    });
  });

  it("maps 401 / 403 / 404 / 429 / 5xx to dedicated error kinds", async () => {
    const cases: { status: number; kind: string }[] = [
      { status: 401, kind: "unauthorized" },
      { status: 403, kind: "forbidden" },
      { status: 404, kind: "not_found" },
      { status: 429, kind: "rate_limited" },
      { status: 503, kind: "server" },
    ];
    for (const { status, kind } of cases) {
      const fetchImpl = mockFetchErr(status, { error: "x" });
      const result = await decideAccept(
        { signalId: "sig-1", expectedVersion: 0 },
        { auth: READY_AUTH, fetchImpl },
      );
      expect(result.ok).toBe(false);
      expect((result as { kind: string }).kind).toBe(kind);
    }
  });

  it("maps fetch rejection to network error", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    const result = await decideAccept(
      { signalId: "sig-1", expectedVersion: 0 },
      { auth: READY_AUTH, fetchImpl },
    );
    expect(result).toEqual({ ok: false, kind: "network", message: "fetch failed" });
  });

  it("returns no_auth without calling fetch when auth is incomplete", async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const result = await decideAccept(
      { signalId: "sig-1", expectedVersion: 0 },
      {
        auth: {
          ready: false,
          killReady: false,
          apiBaseUrl: undefined,
          token: undefined,
          operatorId: undefined,
        },
        fetchImpl,
      },
    );
    expect(result.ok).toBe(false);
    expect((result as { kind: string }).kind).toBe("no_auth");
    expect((fetchImpl as unknown as { mock: { calls: unknown[][] } }).mock.calls.length).toBe(0);
  });
});

describe("resolveCommandCenterAuth", () => {
  it("prefers explicit overrides", () => {
    const auth = resolveCommandCenterAuth({
      apiBaseUrl: "https://api.example",
      token: "tok",
      operatorId: "op-x",
      locationSearch: "?token=other&operator_id=other-op",
    });
    expect(auth).toEqual({
      ready: true,
      killReady: false,
      apiBaseUrl: "https://api.example",
      token: "tok",
      operatorId: "op-x",
      killHmacSecret: undefined,
    });
  });

  it("includes killHmacSecret + killReady when explicit override + decision auth supplied", () => {
    const auth = resolveCommandCenterAuth({
      apiBaseUrl: "https://api.example",
      token: "tok",
      operatorId: "op-x",
      killHmacSecret: "shared-hmac",
    });
    expect(auth.ready).toBe(true);
    expect(auth.killReady).toBe(true);
    expect(auth.killHmacSecret).toBe("shared-hmac");
  });

  it("reads kill_hmac from query string", () => {
    const auth = resolveCommandCenterAuth({
      apiBaseUrl: "https://api.example",
      token: "t",
      operatorId: "op",
      locationSearch: "?kill_hmac=q-secret",
    });
    expect(auth.killReady).toBe(true);
    expect(auth.killHmacSecret).toBe("q-secret");
  });

  it("killReady false when decision auth missing even if kill_hmac present", () => {
    const auth = resolveCommandCenterAuth({
      locationSearch: "?kill_hmac=q-secret",
    });
    expect(auth.ready).toBe(false);
    expect(auth.killReady).toBe(false);
    expect(auth.killHmacSecret).toBe("q-secret");
  });

  it("reads token + operator_id from query string", () => {
    const auth = resolveCommandCenterAuth({
      apiBaseUrl: "https://api.example",
      locationSearch: "?token=qtok&operator_id=qop",
    });
    expect(auth.ready).toBe(true);
    expect(auth.token).toBe("qtok");
    expect(auth.operatorId).toBe("qop");
  });

  it("derives http base from a ws URL when api base is unset", () => {
    const auth = resolveCommandCenterAuth({
      wsUrl: "wss://gateway.example/ws/v1",
      token: "t",
      operatorId: "op",
    });
    expect(auth.ready).toBe(true);
    expect(auth.apiBaseUrl).toBe("https://gateway.example");
  });

  it("not ready when any field is missing", () => {
    const auth = resolveCommandCenterAuth({ apiBaseUrl: "https://api.example", token: "t" });
    expect(auth.ready).toBe(false);
    expect(auth.operatorId).toBeUndefined();
  });
});
