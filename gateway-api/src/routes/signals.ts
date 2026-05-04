// Signal decision endpoints.
//
//   POST /api/v1/signals/:id/accept   body: {operator_id, idempotency_key, expected_version}
//   POST /api/v1/signals/:id/reject   body: {..., reason}
//   POST /api/v1/signals/:id/snooze   body: {..., snooze_until_unix_ms}
//
// Common semantics:
//   * Requires a valid PASETO token (request.user must match operator_id).
//   * 404 if signal is unknown.
//   * 409 if expected_version differs from the current server_version on the
//     signal.
//   * Idempotency: (operator_id + idempotency_key) is reserved in Redis; a
//     repeat call with the same key returns the stored response verbatim.
//   * On success: a SignalDecision is XADDed to `dl.v1.signals.decisions`,
//     the signal's `decision` field is filled, and an audit entry written.

import { randomUUID } from "node:crypto";
import type { FastifyInstance } from "fastify";
import type { AuditLog } from "../audit.ts";
import type { Config } from "../config.ts";
import { makeLogger } from "../logger.ts";
import type { BusClient } from "../bus/types.ts";
import { encodeProtoPublishPayload as defaultEncodeProtoPublishPayload } from "../bus/proto.ts";
import { STREAM_SIGNALS_DECISIONS } from "../bus/topics.ts";

/** Encoder signature; tests inject a stub to simulate codec unavailability. */
export type ProtoEncoder = typeof defaultEncodeProtoPublishPayload;

import type { IdempotencyStore } from "../middleware/idempotency.ts";
import { decisionsPublishedTotal } from "../metrics.ts";
import type { EventStore } from "../state/store.ts";
import type { DecisionAction } from "../types.ts";
import { requireAuth } from "./auth.ts";

interface BaseBody {
  operator_id: string;
  idempotency_key: string;
  expected_version: number;
}
interface AcceptBody extends BaseBody {}
interface RejectBody extends BaseBody {
  reason: string;
}
interface SnoozeBody extends BaseBody {
  snooze_until_unix_ms: number;
}

const BASE_PROPS = {
  operator_id: { type: "string", minLength: 1, maxLength: 64 },
  idempotency_key: { type: "string", minLength: 8, maxLength: 128 },
  expected_version: { type: "integer", minimum: 0 },
} as const;

export function registerSignalsRoutes(
  app: FastifyInstance,
  deps: {
    config: Config;
    store: EventStore;
    bus: BusClient | null;
    audit: AuditLog;
    idempotency: IdempotencyStore;
    encodeProto?: ProtoEncoder;
  },
): void {
  const { config, store, bus, audit, idempotency } = deps;
  const encodeProto = deps.encodeProto ?? defaultEncodeProtoPublishPayload;
  const protoLog = makeLogger("warn", { component: "signals-proto-publish" });

  async function decide(
    req: import("fastify").FastifyRequest<{
      Params: { id: string };
      Body: BaseBody & {
        reason?: string;
        snooze_until_unix_ms?: number;
      };
    }>,
    reply: import("fastify").FastifyReply,
    action: DecisionAction,
  ): Promise<{ status: number; body: Record<string, unknown> }> {
    const user = req.user;
    if (!user) {
      reply.code(401);
      return { status: 401, body: { error: "unauthorized" } };
    }
    if (user.operator_id !== req.body.operator_id) {
      reply.code(403);
      return {
        status: 403,
        body: { error: "operator_id_mismatch" },
      };
    }

    const cached = await idempotency.reserveOrGet(
      user.operator_id,
      req.body.idempotency_key,
      config.idempotencyTtlSeconds,
    );
    if (cached) {
      reply.code(cached.status);
      return { status: cached.status, body: cached.body };
    }

    const signalId = req.params.id;
    const state = store.getState();
    const signal = state.signals[signalId];
    if (!signal) {
      const body = { error: "signal_not_found", signal_id: signalId };
      reply.code(404);
      const out = { status: 404, body };
      await idempotency.save(
        user.operator_id,
        req.body.idempotency_key,
        out,
        config.idempotencyTtlSeconds,
      );
      return out;
    }

    if (
      typeof req.body.expected_version === "number" &&
      signal.server_version !== req.body.expected_version
    ) {
      const body = {
        error: "version_conflict",
        signal_id: signalId,
        expected_version: req.body.expected_version,
        actual_version: signal.server_version,
      };
      reply.code(409);
      const out = { status: 409, body };
      await idempotency.save(
        user.operator_id,
        req.body.idempotency_key,
        out,
        config.idempotencyTtlSeconds,
      );
      return out;
    }

    const decisionId = randomUUID();
    const decidedAt = Date.now();

    const payload: Record<string, unknown> = {
      message_id: decisionId,
      signal_id: signalId,
      expected_version: req.body.expected_version,
      idempotency_key: req.body.idempotency_key,
      action,
      operator_id: user.operator_id,
      decided_at_unix_ms: decidedAt,
    };
    if (action === "REJECT") payload.rejection_reason = req.body.reason ?? "";
    if (action === "SNOOZE")
      payload.snooze_until_unix_ms = req.body.snooze_until_unix_ms ?? 0;

    if (bus) {
      const payloadBytes = await encodeProto(
        STREAM_SIGNALS_DECISIONS,
        payload,
        protoLog,
      );
      if (!payloadBytes) {
        // Control-plane is protobuf-only on egress (gate #5). If codecs
        // are unavailable or the encoder rejects this payload we refuse
        // to publish rather than silently fall through to a JSON
        // envelope that downstream consumers may not accept.
        //
        // Importantly: do NOT save this 503 into the idempotency store.
        // The condition is transient (codecs may finish loading any
        // moment, or a deploy may roll out), and an operator hitting
        // retry with the same idempotency_key must be allowed to
        // re-attempt rather than getting a cached 503 for the full TTL.
        // The slot stays in RESERVED state; the next call with the same
        // key busy-waits briefly and then falls through to a fresh
        // attempt (see `IdempotencyStore.reserveOrGet`).
        protoLog.error(
          "decisions.proto encode unavailable; refusing publish",
          { stream: STREAM_SIGNALS_DECISIONS, signal_id: signalId, decision_id: decisionId },
        );
        reply.code(503);
        return { status: 503, body: { error: "proto_codecs_unavailable" } };
      }
      await bus.publish(STREAM_SIGNALS_DECISIONS, payload, payloadBytes);
    }
    decisionsPublishedTotal.inc({ action });

    store.apply("signals", (s) => {
      const cur = s.signals[signalId];
      if (!cur) return;
      s.signals[signalId] = {
        ...cur,
        server_version: cur.server_version + 1,
        decision: {
          action,
          operator_id: user.operator_id,
          reason: action === "REJECT" ? req.body.reason : undefined,
          snooze_until_unix_ms:
            action === "SNOOZE" ? req.body.snooze_until_unix_ms : undefined,
          decided_at_unix_ms: decidedAt,
        },
      };
    });

    await audit.append({
      ts_unix_ms: decidedAt,
      kind: "decision",
      operator_id: user.operator_id,
      action,
      detail: {
        signal_id: signalId,
        decision_id: decisionId,
        expected_version: req.body.expected_version,
        reason: req.body.reason,
        snooze_until_unix_ms: req.body.snooze_until_unix_ms,
      },
    });

    const body = { decision_id: decisionId, action };
    reply.code(202);
    const out = { status: 202, body };
    await idempotency.save(
      user.operator_id,
      req.body.idempotency_key,
      out,
      config.idempotencyTtlSeconds,
    );
    return out;
  }

  app.post<{ Params: { id: string }; Body: AcceptBody }>(
    "/api/v1/signals/:id/accept",
    {
      preHandler: requireAuth(app),
      schema: {
        params: {
          type: "object",
          required: ["id"],
          properties: { id: { type: "string", minLength: 1, maxLength: 128 } },
        },
        body: {
          type: "object",
          required: ["operator_id", "idempotency_key", "expected_version"],
          additionalProperties: false,
          properties: BASE_PROPS,
        },
      },
    },
    async (req, reply) => {
      const out = await decide(req, reply, "ACCEPT");
      return out.body;
    },
  );

  app.post<{ Params: { id: string }; Body: RejectBody }>(
    "/api/v1/signals/:id/reject",
    {
      preHandler: requireAuth(app),
      schema: {
        params: {
          type: "object",
          required: ["id"],
          properties: { id: { type: "string", minLength: 1, maxLength: 128 } },
        },
        body: {
          type: "object",
          required: [
            "operator_id",
            "idempotency_key",
            "expected_version",
            "reason",
          ],
          additionalProperties: false,
          properties: {
            ...BASE_PROPS,
            reason: { type: "string", minLength: 1, maxLength: 512 },
          },
        },
      },
    },
    async (req, reply) => {
      const out = await decide(req, reply, "REJECT");
      return out.body;
    },
  );

  app.post<{ Params: { id: string }; Body: SnoozeBody }>(
    "/api/v1/signals/:id/snooze",
    {
      preHandler: requireAuth(app),
      schema: {
        params: {
          type: "object",
          required: ["id"],
          properties: { id: { type: "string", minLength: 1, maxLength: 128 } },
        },
        body: {
          type: "object",
          required: [
            "operator_id",
            "idempotency_key",
            "expected_version",
            "snooze_until_unix_ms",
          ],
          additionalProperties: false,
          properties: {
            ...BASE_PROPS,
            snooze_until_unix_ms: { type: "integer", minimum: 1 },
          },
        },
      },
    },
    async (req, reply) => {
      const out = await decide(req, reply, "SNOOZE");
      return out.body;
    },
  );
}
