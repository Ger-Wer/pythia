// Gate #5 regression: when the protobuf codec is unavailable or the encoder
// rejects an envelope, the operator-facing routes (`signals.decisions` and
// `control.kill`) MUST refuse to publish — they used to silently fall
// through to a JSON envelope, which downstream consumers (sniper-executor,
// future strict consumers) do not accept.
//
// Drives both routes via `app.inject(...)` with a stub `encodeProto` that
// always returns `null`, then asserts (a) HTTP 503 with
// `proto_codecs_unavailable`, (b) the bus did not see a publish, (c) audit
// + state side-effects did not happen, (d) the 503 is NOT cached in the
// idempotency store — retry with the same key MUST re-attempt the encode
// path because proto-unavailable is a transient condition.

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
  let encodeAttempts: number;

  beforeEach(async () => {
    encodeAttempts = 0;
    // Inject a stub encoder that reports the codec as unavailable AND
    // tracks how many times it was called. The retry-not-cached assertion
    // depends on this counter incrementing across requests with the same
    // idempotency_key.
    ctx = await makeTestContext(
      {},
      {
        encodeProto: async () => {
          encodeAttempts += 1;
          return null;
        },
      },
    );
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
    expect(encodeAttempts).toBe(1);

    // Retry with the SAME idempotency_key must NOT return a cached 503 —
    // proto-unavailable is a transient condition and the operator must be
    // allowed to re-attempt. The encoder is called again; bus is still
    // not published to; the response is a fresh 503.
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
    expect(encodeAttempts).toBeGreaterThanOrEqual(2);

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

    // Capture baselines BEFORE the request. `ioredis-mock` shares its
    // in-memory store across instances, so prior tests in the same
    // process may have left rows in `gateway:audit:kill` AND a persisted
    // `gateway:state` snapshot with `kill.engaged: true` that the fresh
    // EventStore re-hydrates on boot.
    const beforeAudit = await ctx.handle.audit.recent("kill", 1024);
    const beforeKill = { ...ctx.handle.store.getState().kill };

    const issuedAt = Date.now();
    const sign = (nonce: number) =>
      createHmac("sha256", ctx.config.killHmacSecret)
        .update(
          [ctx.operatorId, "GLOBAL", "", String(nonce), String(issuedAt)].join("|"),
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
        signature: sign(99),
        reason: "regression: proto codecs unavailable",
      },
    });
    expect(res.statusCode).toBe(503);
    const body = res.json() as { error: string };
    expect(body.error).toBe("proto_codecs_unavailable");
    expect(killsPublished).toBe(0);
    expect(encodeAttempts).toBe(1);

    // Retry with the SAME idempotency_key MUST re-attempt encoding. The
    // kill switch is the most safety-critical operator action; caching a
    // transient 503 would block the operator from retrying for the full
    // idempotency TTL.
    const retry = await ctx.handle.app.inject({
      method: "POST",
      url: "/api/v1/control/kill",
      headers: { authorization: `Bearer ${ctx.token}` },
      payload: {
        operator_id: ctx.operatorId,
        idempotency_key: "idem-kill-proto-off",
        scope: "GLOBAL",
        nonce: 99,
        issued_at_unix_ms: issuedAt,
        signature: sign(99),
        reason: "regression: proto codecs unavailable",
      },
    });
    expect(retry.statusCode).toBe(503);
    expect(killsPublished).toBe(0);
    expect(encodeAttempts).toBeGreaterThanOrEqual(2);

    // Gateway refused the command before audit/state side-effects: no
    // new kill row was appended.
    const afterAudit = await ctx.handle.audit.recent("kill", 1024);
    expect(afterAudit.length).toBe(beforeAudit.length);

    // Kill state was not flipped by THIS request — every field is
    // unchanged from the baseline (which itself may already be engaged
    // if a prior test persisted such a snapshot to the shared mock).
    const snap = ctx.handle.store.getState();
    expect(snap.kill).toEqual(beforeKill);
  });
});
