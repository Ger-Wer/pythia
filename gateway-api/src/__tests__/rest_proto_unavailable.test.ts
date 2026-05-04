// Gate #5 regression: when the protobuf codec is unavailable or the encoder
// rejects an envelope, the operator-facing routes (`signals.decisions` and
// `control.kill`) MUST refuse to publish — they used to silently fall
// through to a JSON envelope, which downstream consumers (sniper-executor,
// future strict consumers) do not accept.
//
// Drives both routes via `app.inject(...)` with a stub `encodeProto` that
// always returns `null`, then asserts (a) HTTP 503 with
// `proto_codecs_unavailable`, (b) the bus did not see a publish, (c) the
// audit log did not record the action, (d) signal/store state is not
// mutated, (e) idempotency is preserved across retries with the same key.

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { createHmac } from "node:crypto";
import { STREAM_CONTROL_KILL, STREAM_SIGNALS_DECISIONS } from "../bus/topics.ts";
import type { Signal } from "../types.ts";
import { makeTestContext, type TestContext } from "./helpers.ts";

function sampleSignal(overrides: Partial<Signal> = {}): Signal {
  return {
    signal_id: "SIG-PROTO-OFF",
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
    expires_at_unix_ms: Date.now() + 3_600_000,
    llm_rationale: "",
    evidence_urls: [],
    issued_at_unix_ms: Date.now(),
    priority: "NORMAL",
    ...overrides,
  };
}

describe("REST proto-unavailable (gate #5)", () => {
  let ctx: TestContext;

  beforeEach(async () => {
    // Inject a stub encoder that always reports the codec as unavailable.
    ctx = await makeTestContext({}, { encodeProto: async () => null });
    await ctx.bus.publish("dl.v1.signals.emitted", {
      ...sampleSignal(),
    } as unknown as Record<string, unknown>);
    await new Promise((r) => setTimeout(r, 20));
  });
  afterEach(async () => {
    await ctx.close();
  });

  test("accept signal returns 503 + does not publish when proto encode is null", async () => {
    let decisionsPublished = 0;
    const originalPublish = ctx.bus.publish.bind(ctx.bus);
    ctx.bus.publish = async (stream, payload, payloadBytes) => {
      if (stream === STREAM_SIGNALS_DECISIONS) decisionsPublished += 1;
      return originalPublish(stream, payload, payloadBytes);
    };

    const res = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-PROTO-OFF/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-proto-off-1",
        expected_version: 1,
      },
    });
    expect(res.statusCode).toBe(503);
    const body = res.json() as { error: string };
    expect(body.error).toBe("proto_codecs_unavailable");
    expect(decisionsPublished).toBe(0);

    // Idempotency: retrying the same key returns the same 503 + still no
    // double publish.
    const retry = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/signals/SIG-PROTO-OFF/accept",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-proto-off-1",
        expected_version: 1,
      },
    });
    expect(retry.statusCode).toBe(503);
    expect(decisionsPublished).toBe(0);

    // Signal state was NOT advanced — server_version stays at 1, no decision.
    const snap = ctx.handle.store.getState();
    expect(snap.signals["SIG-PROTO-OFF"]?.server_version).toBe(1);
    expect(snap.signals["SIG-PROTO-OFF"]?.decision).toBeUndefined();
  });

  test("kill command returns 503 + does not publish when proto encode is null", async () => {
    let killsPublished = 0;
    const originalPublish = ctx.bus.publish.bind(ctx.bus);
    ctx.bus.publish = async (stream, payload, payloadBytes) => {
      if (stream === STREAM_CONTROL_KILL) killsPublished += 1;
      return originalPublish(stream, payload, payloadBytes);
    };

    const issuedAt = Date.now();
    const signature = createHmac("sha256", ctx.config.killHmacSecret)
      .update(
        [
          ctx.operatorId,
          "GLOBAL",
          "",
          String(99),
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
        idempotency_key: "idem-kill-proto-off",
        scope: "GLOBAL",
        nonce: 99,
        issued_at_unix_ms: issuedAt,
        signature,
        reason: "regression: proto codecs unavailable",
      },
    });
    expect(res.statusCode).toBe(503);
    const body = res.json() as { error: string };
    expect(body.error).toBe("proto_codecs_unavailable");
    expect(killsPublished).toBe(0);

    // No kill audit recorded — gateway refused the command before the
    // audit/state side-effects.
    const killAudit = await ctx.handle.audit.recent("kill", 1);
    expect(killAudit.length).toBe(0);
  });
});
