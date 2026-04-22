// WSS /ws/v1 — the single multiplexed WebSocket channel to the UI.
//
// Protocol (see 05-command-center-ui.md + task spec):
//   client → server:
//     {kind:"subscribe", topics:[...]}
//     {kind:"unsubscribe", topics:[...]}
//     {kind:"pong", ts}
//   server → client:
//     {kind:"snapshot", server_version, data, topics}
//     {kind:"patch", server_version, topic, patch}
//     {kind:"ping", ts}
//     {kind:"error", code, message}
//
// Auth: PASETO v4.local, passed either in `?token=...` (preferred for
// browsers) or `Sec-WebSocket-Protocol` header. Missing/invalid → close 4401.
//
// Heartbeat: server sends {kind:"ping"} every WS_HEARTBEAT_INTERVAL_MS;
// if two consecutive pongs are missed the connection is closed.
//
// Re-sync: server pushes a full {kind:"snapshot"} every
// WS_SNAPSHOT_INTERVAL_MS regardless of subscriptions.
//
// Backpressure: if buffered patches exceed WS_MAX_BUFFERED_PATCHES, the
// queue is flushed and a snapshot is sent instead.

import type { FastifyInstance, FastifyRequest } from "fastify";
import type { WebSocket } from "ws";
import type { Config } from "../config.ts";
import { verifyToken } from "../auth/paseto.ts";
import type { EventStore } from "../state/store.ts";
import type { JsonPatchOp, Topic, WsServerMessage } from "../types.ts";
import { ALL_TOPICS } from "../types.ts";
import {
  wsConnectionsActive,
  wsDisconnectsTotal,
  wsMessagesReceivedTotal,
  wsMessagesSentTotal,
} from "../metrics.ts";

function extractToken(req: FastifyRequest): string | undefined {
  const q = req.query as Record<string, unknown> | undefined;
  if (q && typeof q.token === "string") return q.token;
  const proto = req.headers["sec-websocket-protocol"];
  if (typeof proto === "string") {
    // "paseto, <token>" or just "<token>".
    const parts = proto.split(",").map((p) => p.trim());
    if (parts.length >= 2 && parts[0] === "paseto") return parts[1];
    if (parts.length === 1 && parts[0]) return parts[0];
  }
  const auth = req.headers.authorization;
  if (auth && auth.startsWith("Bearer ")) return auth.slice(7).trim();
  return undefined;
}

function buildSnapshot(
  store: EventStore,
  topics: ReadonlySet<Topic>,
): WsServerMessage {
  const s = store.getState();
  const data: Record<string, unknown> = {};
  if (topics.has("signals")) data.signals = s.signals;
  if (topics.has("watchlist")) data.watchlist = s.watchlist;
  if (topics.has("triggers")) data.triggers = s.triggers;
  if (topics.has("exec_status")) {
    data.exec_status = s.exec_status;
    data.kill = s.kill;
  }
  return {
    kind: "snapshot",
    server_version: s.server_version,
    data,
    topics: [...topics],
  };
}

export function registerWsRoute(
  app: FastifyInstance,
  deps: { config: Config; store: EventStore },
): void {
  const { config, store } = deps;

  app.get(
    "/ws/v1",
    { websocket: true, config: { rateLimit: false } },
    (socket: WebSocket, req: FastifyRequest) => {
      const token = extractToken(req);
      let operatorId: string | null = null;

      void (async () => {
        if (!token) {
          sendSafely(socket, {
            kind: "error",
            code: "unauthorized",
            message: "missing token",
          });
          socket.close(4401, "unauthorized");
          return;
        }
        try {
          const claims = await verifyToken(config.pasetoKey, token);
          operatorId = claims.sub;
        } catch (err) {
          sendSafely(socket, {
            kind: "error",
            code: "unauthorized",
            message: (err as Error).message,
          });
          socket.close(4401, "unauthorized");
          return;
        }

        wsConnectionsActive.inc();
        app.log.info({ operator_id: operatorId }, "ws connected");

        const subs = new Set<Topic>();
        let pending: {
          topic: Topic;
          patch: JsonPatchOp[];
          server_version: number;
        }[] = [];
        let missedPongs = 0;
        let pendingPing: number | null = null;

        // Initial snapshot with all topics (server-side default) until the
        // client explicitly subscribes. This gives minimal-code clients a
        // working view without any subscribe dance.
        for (const t of ALL_TOPICS) subs.add(t);
        sendMessage(socket, buildSnapshot(store, subs));

        // Per-client store subscription: enqueue patches by topic.
        const unsubscribe = store.subscribe((topic, patch, version) => {
          if (!subs.has(topic)) return;
          if (pending.length >= config.wsMaxBufferedPatches) {
            // Overflow: clear queue and send a fresh snapshot.
            pending = [];
            sendMessage(socket, buildSnapshot(store, subs));
            return;
          }
          pending.push({ topic, patch: [...patch], server_version: version });
          drainPending();
        });

        function drainPending(): void {
          // Fastify/ws buffers internally; we push one frame per logical
          // patch so the client can apply them in order.
          while (pending.length > 0 && socket.readyState === socket.OPEN) {
            const next = pending.shift();
            if (!next) break;
            sendMessage(socket, {
              kind: "patch",
              server_version: next.server_version,
              topic: next.topic,
              patch: next.patch,
            });
          }
        }

        // Heartbeat loop.
        const pingInterval = setInterval(() => {
          if (socket.readyState !== socket.OPEN) return;
          if (pendingPing !== null) {
            missedPongs += 1;
            if (missedPongs >= 2) {
              wsDisconnectsTotal.inc({ reason: "heartbeat_timeout" });
              socket.close(4408, "heartbeat timeout");
              return;
            }
          }
          pendingPing = Date.now();
          sendMessage(socket, { kind: "ping", ts: pendingPing });
        }, config.wsHeartbeatIntervalMs);

        // Periodic full snapshot re-sync.
        const snapshotInterval = setInterval(() => {
          if (socket.readyState !== socket.OPEN) return;
          sendMessage(socket, buildSnapshot(store, subs));
        }, config.wsSnapshotIntervalMs);

        socket.on("message", (raw: Buffer | ArrayBuffer | string) => {
          let parsed: unknown;
          try {
            const text =
              typeof raw === "string"
                ? raw
                : Buffer.from(raw as ArrayBuffer).toString("utf8");
            parsed = JSON.parse(text);
          } catch {
            sendSafely(socket, {
              kind: "error",
              code: "bad_json",
              message: "frame is not valid JSON",
            });
            return;
          }
          if (!parsed || typeof parsed !== "object") return;
          const msg = parsed as {
            kind?: string;
            topics?: unknown;
            ts?: unknown;
          };
          wsMessagesReceivedTotal.inc({ kind: msg.kind ?? "unknown" });
          switch (msg.kind) {
            case "subscribe": {
              const topics = Array.isArray(msg.topics) ? msg.topics : [];
              subs.clear();
              for (const t of topics) {
                if (typeof t === "string" && isTopic(t)) subs.add(t);
              }
              sendMessage(socket, buildSnapshot(store, subs));
              break;
            }
            case "unsubscribe": {
              const topics = Array.isArray(msg.topics) ? msg.topics : [];
              for (const t of topics) {
                if (typeof t === "string" && isTopic(t)) subs.delete(t);
              }
              break;
            }
            case "pong": {
              missedPongs = 0;
              pendingPing = null;
              break;
            }
            default:
              sendSafely(socket, {
                kind: "error",
                code: "unknown_kind",
                message: `unknown kind: ${msg.kind ?? ""}`,
              });
          }
        });

        const cleanup = (reason: string) => {
          clearInterval(pingInterval);
          clearInterval(snapshotInterval);
          unsubscribe();
          wsConnectionsActive.dec();
          wsDisconnectsTotal.inc({ reason });
          app.log.info({ operator_id: operatorId, reason }, "ws disconnected");
        };

        socket.on("close", () => cleanup("close"));
        socket.on("error", (err: Error) => {
          app.log.warn({ err: err.message }, "ws error");
          cleanup("error");
        });
      })();
    },
  );
}

function isTopic(s: string): s is Topic {
  return (
    s === "signals" ||
    s === "watchlist" ||
    s === "triggers" ||
    s === "exec_status"
  );
}

function sendMessage(socket: WebSocket, msg: WsServerMessage): void {
  if (socket.readyState !== socket.OPEN) return;
  socket.send(JSON.stringify(msg));
  wsMessagesSentTotal.inc({ kind: msg.kind });
}

function sendSafely(socket: WebSocket, msg: WsServerMessage): void {
  try {
    sendMessage(socket, msg);
  } catch {
    /* ignore */
  }
}
