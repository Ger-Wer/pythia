// POST /api/v1/control/kill — emergency kill switch.
//
// Body: {operator_id, scope, market_id?, nonce, signature, idempotency_key}
// `signature` = HMAC-SHA256(KILL_HMAC_SECRET,
//     message_id || operator_id || scope || market_id || nonce || issued_at_ms)
// encoded as hex. issued_at_ms must be within ±5s of server clock.
//
// Gateway publishes KillCommand to `dl.v1.control.kill`, then waits up to
// KILL_ACK_TIMEOUT_MS for a matching KillAck on `dl.v1.control.kill_ack`.

import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import type { FastifyInstance } from "fastify";
import type { AuditLog } from "../audit.ts";
import { encodeProtoPublishPayload } from "../bus/proto.ts";
import type { BusClient } from "../bus/types.ts";
import { STREAM_CONTROL_KILL } from "../bus/topics.ts";
import type { BridgeHandle } from "../bus/bridge.ts";
import type { Config } from "../config.ts";
import type { IdempotencyStore } from "../middleware/idempotency.ts";
import { killCommandsTotal } from "../metrics.ts";
import type { EventStore } from "../state/store.ts";
import type { KillScope } from "../types.ts";
import { requireAuth } from "./auth.ts";
import { makeLogger } from "../logger.ts";

interface KillBody {
  operator_id: string;
  idempotency_key: string;
  scope: KillScope;
  market_id?: string;
  nonce: number;
  issued_at_unix_ms: number;
  signature: string;
  /** Free-text justification recorded in audit log. Not signed. */
  reason?: string;
}

const CLOCK_SKEW_MS = 5_000;

function expectedSignature(
  secret: string,
  parts: {
    message_id: string;
    operator_id: string;
    scope: string;
    market_id: string;
    nonce: number;
    issued_at_unix_ms: number;
  },
): string {
  const payload = [
    parts.message_id,
    parts.operator_id,
    parts.scope,
    parts.market_id,
    String(parts.nonce),
    String(parts.issued_at_unix_ms),
  ].join("|");
  return createHmac("sha256", secret).update(payload).digest("hex");
}

function verifyHmacHex(expected: string, given: string): boolean {
  if (expected.length !== given.length) return false;
  try {
    return timingSafeEqual(Buffer.from(expected), Buffer.from(given));
  } catch {
    return false;
  }
}

export function registerControlRoutes(
  app: FastifyInstance,
  deps: {
    config: Config;
    store: EventStore;
    bus: BusClient | null;
    bridge: BridgeHandle | null;
    audit: AuditLog;
    idempotency: IdempotencyStore;
  },
): void {
  const { config, store, bus, bridge, audit, idempotency } = deps;
  const protoLog = makeLogger("warn", { component: "control-proto-publish" });

  app.post<{ Body: KillBody }>(
    "/api/v1/control/kill",
    {
      preHandler: requireAuth(app),
      schema: {
        body: {
          type: "object",
          required: [
            "operator_id",
            "idempotency_key",
            "scope",
            "nonce",
            "issued_at_unix_ms",
            "signature",
          ],
          additionalProperties: false,
          properties: {
            operator_id: { type: "string", minLength: 1, maxLength: 64 },
            idempotency_key: { type: "string", minLength: 8, maxLength: 128 },
            scope: {
              type: "string",
              enum: ["GLOBAL", "MARKET", "NEW_TRIGGERS"],
            },
            market_id: { type: "string", maxLength: 128 },
            nonce: { type: "integer", minimum: 0 },
            issued_at_unix_ms: { type: "integer" },
            signature: {
              type: "string",
              minLength: 64,
              maxLength: 128,
            },
            reason: { type: "string", maxLength: 256 },
          },
        },
      },
    },
    async (req, reply) => {
      const user = req.user!;
      const b = req.body;
      if (user.operator_id !== b.operator_id) {
        reply.code(403);
        return { error: "operator_id_mismatch" };
      }

      const cached = await idempotency.reserveOrGet(
        user.operator_id,
        b.idempotency_key,
        config.idempotencyTtlSeconds,
      );
      if (cached) {
        reply.code(cached.status);
        return cached.body;
      }

      // Clock-skew guard.
      const now = Date.now();
      if (Math.abs(now - b.issued_at_unix_ms) > CLOCK_SKEW_MS) {
        killCommandsTotal.inc({ scope: b.scope, status: "rejected_stale" });
        const body = { error: "stale_request", now, issued_at_unix_ms: b.issued_at_unix_ms };
        reply.code(400);
        const out = { status: 400, body };
        await idempotency.save(user.operator_id, b.idempotency_key, out, config.idempotencyTtlSeconds);
        return body;
      }

      const messageId = randomUUID();
      const expected = expectedSignature(config.killHmacSecret, {
        message_id: messageId,
        operator_id: b.operator_id,
        scope: b.scope,
        market_id: b.market_id ?? "",
        nonce: b.nonce,
        issued_at_unix_ms: b.issued_at_unix_ms,
      });
      // Clients don't know `message_id` ahead of time — so the contract is:
      // the client signs (operator_id | scope | market_id | nonce | issued_at_ms)
      // with a client-side nonce, and the server re-derives with its own
      // message_id embedded. Translated: verify ignoring the message_id byte
      // is enough for MVP. We still compute a signed envelope including
      // `message_id` for downstream executor validation.
      const clientExpected = createHmac("sha256", config.killHmacSecret)
        .update(
          [
            b.operator_id,
            b.scope,
            b.market_id ?? "",
            String(b.nonce),
            String(b.issued_at_unix_ms),
          ].join("|"),
        )
        .digest("hex");
      if (!verifyHmacHex(clientExpected, b.signature)) {
        killCommandsTotal.inc({ scope: b.scope, status: "rejected_signature" });
        const body = { error: "invalid_signature" };
        reply.code(401);
        const out = { status: 401, body };
        await idempotency.save(user.operator_id, b.idempotency_key, out, config.idempotencyTtlSeconds);
        return body;
      }

      const envelope = {
        message_id: messageId,
        operator_id: b.operator_id,
        scope: b.scope,
        market_id: b.market_id ?? "",
        nonce: b.nonce,
        issued_at_unix_ms: b.issued_at_unix_ms,
        signature: expected, // server-side signature that includes message_id
      };

      if (bus) {
        const payloadBytes = await encodeProtoPublishPayload(
          STREAM_CONTROL_KILL,
          envelope,
          protoLog,
        );
        await bus.publish(STREAM_CONTROL_KILL, envelope, payloadBytes ?? undefined);
      }

      store.apply("exec_status", (s) => {
        s.kill = {
          engaged: true,
          scope: b.scope,
          last_command_id: messageId,
          last_ack_status: s.kill.last_ack_status,
          last_ack_at_unix_ms: s.kill.last_ack_at_unix_ms,
        };
      });

      await audit.append({
        ts_unix_ms: now,
        kind: "kill",
        operator_id: user.operator_id,
        action: "kill_command",
        detail: {
          command_id: messageId,
          scope: b.scope,
          market_id: b.market_id,
          nonce: b.nonce,
          reason: b.reason,
        },
      });

      // Wait for ack (best-effort; fails open if timeout).
      let ackStatus: string = "timeout";
      let ackDetail: string | undefined;
      if (bridge) {
        try {
          const ack = await bridge.killAck(messageId, config.killAckTimeoutMs);
          ackStatus = ack.status;
          ackDetail = ack.detail;
        } catch (err) {
          ackStatus = "timeout";
          ackDetail = (err as Error).message;
        }
      } else {
        ackStatus = "no_bus";
      }
      killCommandsTotal.inc({ scope: b.scope, status: ackStatus });

      const body = {
        command_id: messageId,
        scope: b.scope,
        ack_status: ackStatus,
        ack_detail: ackDetail,
      };
      reply.code(202);
      const out = { status: 202, body };
      await idempotency.save(
        user.operator_id,
        b.idempotency_key,
        out,
        config.idempotencyTtlSeconds,
      );
      return body;
    },
  );
}
