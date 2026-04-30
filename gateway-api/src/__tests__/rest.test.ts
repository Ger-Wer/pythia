// REST endpoint tests: accept/reject/snooze happy-path, idempotency, 409
// conflict, 404, auth. Uses Fastify's inject() for zero-network calls.

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createHmac } from "node:crypto";
import { STREAM_CONTROL_KILL, STREAM_CONTROL_KILL_ACK, STREAM_SIGNALS_DECISIONS } from "../bus/topics.ts";
import type { Signal } from "../types.ts";
import { makeTestContext, type TestContext } from "./helpers.ts";

const EXPECT_PROTO_PUBLISH = process.env.EXPECT_PROTO_PUBLISH === "1";

function sampleSignal(overrides: Partial<Signal> = {}): Signal {
  return {
    signal_id: "SIG-1",
    server_version: 1,
    market_id: "poly-1",
    ctf_address: "0xabc",
    market_question: "Will it rain tomorrow?",
    edge: 0.17,
    p_fundamental: 0.72,
    p_market: 0.55,
    confidence_low: 0.65,
    confidence_high: 0.78,
    expected_value_bps: 120,
    side_hint: "YES",
    suggested_position_micros: 5_000_000,
    expires_at_unix_ms: Date.now() + 3600_000,
    llm_rationale: "",
    evidence_urls: [],
    issued_at_unix_ms: Date.now(),
    priority: "NORMAL",
    ...overrides,
  };
}

describe("REST", () => {
  let ctx: TestContext;

  beforeEach(async () => {
    ctx = await makeTestContext();
    // Inject a sample signal via bus so the store has something to act on.
    await ctx.bus.publish("dl.v1.signals.emitted", {
      ...sampleSignal(),
    } as unknown as Record<string, unknown>);
    // Let the bridge handler finish.
    await new Promise((r) => setTimeout(r, 20));
  });
  afterEach(async () => {
    await ctx.close();
  });

  test("healthz returns 200", async () => {
    const res = await ctx.handle.app.inject({
      method: "GET",
      url: "/api/v1/healthz",
    });
    expect(res.statusCode).toBe(200);
    const body = res.json() as { status: string };
    expect(body.status).toBe("ok");
  });

  test("metrics endpoint exposes gateway_* series", async () => {
    const res = await ctx.handle.app.inject({
      method: "GET",
      url: "/metrics",
    });
    expect(res.statusCode).toBe(200);
    expect(res.body).toContain("gateway_http_requests_total");
  });

  test("auth token issue + snapshot protected", async () => {
    const noAuth = await ctx.handle.app.inject({
      method: "GET",
      url: "/api/v1/state/snapshot",
    });
    expect(noAuth.statusCode).toBe(401);

    const token = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/auth/token",
      payload: { operator_id: ctx.operatorId, password: ctx.password },
    });
    expect(token.statusCode).toBe(200);
    const tok = (token.json() as { token: string }).token;

    const withAuth = await ctx.handle.app.inject({
      method: "GET",
      url: "/api/v1/state/snapshot",
      headers: { authorization: `Bearer ${tok}` },
    });
    expect(withAuth.statusCode).toBe(200);
    const snap = withAuth.json() as {
      server_version: number;
      data: { signals: Record<string, Signal> };
    };
    expect(snap.data.signals["SIG-1"]).toBeDefined();
  });

  test("accept signal publishes decision and updates state", async () => {
    let lastPublished: unknown = null;
    let lastPayloadBytes: Uint8Array | undefined;
    const originalPublish = ctx.bus.publish.bind(ctx.bus);
    ctx.bus.publish = async (stream, payload, payloadBytes) => {
      if (stream === STREAM_SIGNALS_DECISIONS) {
        lastPublished = payload;
        lastPayloadBytes = payloadBytes;
      }
      return originalPublish(stream, payload, payloadBytes);
    };

    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-accept-1",
        expected_version: 1,
      },
    });
    expect(res.statusCode).toBe(202);
    const body = res.json() as { decision_id: string; action: string };
    expect(body.action).toBe("ACCEPT");
    expect(typeof body.decision_id).toBe("string");
    expect(lastPublished).toMatchObject({
      signal_id: "SIG-1",
      action: "ACCEPT",
      operator_id: ctx.operatorId,
    });
    if (EXPECT_PROTO_PUBLISH) {
      expect(lastPayloadBytes).toBeInstanceOf(Uint8Array);
      expect(lastPayloadBytes?.length).toBeGreaterThan(0);
    }

    const snap = ctx.handle.store.getState();
    expect(snap.signals["SIG-1"]?.decision?.action).toBe("ACCEPT");
  });

  test("kill command publishes proto bytes and returns executor ack", async () => {
    let lastPublished: unknown = null;
    let lastPayloadBytes: Uint8Array | undefined;
    const originalPublish = ctx.bus.publish.bind(ctx.bus);
    ctx.bus.publish = async (stream, payload, payloadBytes) => {
      if (stream === STREAM_CONTROL_KILL) {
        lastPublished = payload;
        lastPayloadBytes = payloadBytes;
        await originalPublish(stream, payload, payloadBytes);
        const commandId = String((payload as Record<string, unknown>).message_id ?? "");
        setTimeout(() => {
          void originalPublish(STREAM_CONTROL_KILL_ACK, {
            original_command_id: commandId,
            status: "COMPLETE",
            detail: "ok",
            active_triggers_cancelled: 0,
            pending_tx_cancelled: 0,
            ack_at_unix_ms: Date.now(),
          });
        }, 10);
        return "kill-stream-id";
      }
      return originalPublish(stream, payload, payloadBytes);
    };

    const issuedAt = Date.now();
    const signature = createHmac("sha256", ctx.config.killHmacSecret)
      .update(
        [
          ctx.operatorId,
          "NEW_TRIGGERS",
          "",
          String(77),
          String(issuedAt),
        ].join("|"),
      )
      .digest("hex");

    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/control/kill",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-kill-1",
        scope: "NEW_TRIGGERS",
        nonce: 77,
        issued_at_unix_ms: issuedAt,
        signature,
      },
    });

    expect(res.statusCode).toBe(202);
    const body = res.json() as { command_id: string; ack_status: string };
    expect(body.ack_status).toBe("COMPLETE");
    expect(lastPublished).toMatchObject({
      operator_id: ctx.operatorId,
      scope: "NEW_TRIGGERS",
      nonce: 77,
    });
    if (EXPECT_PROTO_PUBLISH) {
      expect(lastPayloadBytes).toBeInstanceOf(Uint8Array);
      expect(lastPayloadBytes?.length).toBeGreaterThan(0);
    }
  });

  test("reject requires reason", async () => {
    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/reject",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-reject-noreason",
        expected_version: 1,
      },
    });
    expect(res.statusCode).toBe(400);
  });

  test("snooze happy path", async () => {
    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/snooze",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-snooze-1",
        expected_version: 1,
        snooze_until_unix_ms: Date.now() + 900_000,
      },
    });
    expect(res.statusCode).toBe(202);
  });

  test("idempotency returns same response body", async () => {
    const payload = {
      operator_id: ctx.operatorId,
      idempotency_key: "idem-twice",
      expected_version: 1,
    };
    const r1 = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload,
    });
    const r2 = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload,
    });
    expect(r1.statusCode).toBe(r2.statusCode);
    expect(r1.json()).toEqual(r2.json());
  });

  test("expected_version mismatch → 409", async () => {
    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-conflict-1",
        expected_version: 999,
      },
    });
    expect(res.statusCode).toBe(409);
    const body = res.json() as { error: string; actual_version: number };
    expect(body.error).toBe("version_conflict");
    expect(body.actual_version).toBe(1);
  });

  test("404 for unknown signal", async () => {
    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/NOPE/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-404-1",
        expected_version: 0,
      },
    });
    expect(res.statusCode).toBe(404);
  });

  test("operator_id mismatch → 403", async () => {
    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-1/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: "someone-else",
        idempotency_key: "idem-403-1",
        expected_version: 1,
      },
    });
    expect(res.statusCode).toBe(403);
  });
});
