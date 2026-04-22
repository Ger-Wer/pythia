// Fastify app factory.
//
// Wires together: state projection, bus bridge, audit log, REST routes,
// WebSocket endpoint, metrics. Exposed as `buildServer()` so tests can
// construct an isolated instance without touching global process state.

import Fastify, { type FastifyInstance } from "fastify";
import fastifyRateLimit from "@fastify/rate-limit";
import fastifyWebsocket from "@fastify/websocket";
import type { Redis } from "ioredis";
import type { Config } from "./config.ts";
import type { Logger } from "./logger.ts";
import { AuditLog } from "./audit.ts";
import type { BusClient } from "./bus/types.ts";
import { type BridgeHandle, startBridge } from "./bus/bridge.ts";
import {
  type IdempotencyStore,
  MemoryIdempotencyStore,
  RedisIdempotencyStore,
} from "./middleware/idempotency.ts";
import {
  httpRequestDurationSeconds,
  httpRequestsTotal,
  stateServerVersion,
} from "./metrics.ts";
import { installAuthErrorHandler, registerAuthRoutes } from "./routes/auth.ts";
import { registerControlRoutes } from "./routes/control.ts";
import { registerHealthRoutes } from "./routes/health.ts";
import { registerSignalsRoutes } from "./routes/signals.ts";
import { registerSnapshotRoutes } from "./routes/snapshot.ts";
import { registerWsRoute } from "./routes/ws.ts";
import { EventStore } from "./state/store.ts";

export interface ServerDeps {
  config: Config;
  log: Logger;
  redis: Redis | null;
  bus: BusClient | null;
}

export interface ServerHandle {
  app: FastifyInstance;
  store: EventStore;
  bridge: BridgeHandle | null;
  audit: AuditLog;
  idempotency: IdempotencyStore;
  stop(): Promise<void>;
}

export async function buildServer(deps: ServerDeps): Promise<ServerHandle> {
  const { config, log, redis, bus } = deps;

  const app = Fastify({
    logger: {
      level: config.logLevel,
      // Fastify's logger emits request logs automatically; disable in tests.
      transport: undefined,
    },
    trustProxy: true,
    disableRequestLogging: process.env.NODE_ENV === "test",
  });

  // Request-timing middleware.
  app.addHook("onResponse", async (req, reply) => {
    const route = req.routeOptions.url ?? req.url;
    const method = req.method;
    const status = String(reply.statusCode);
    httpRequestsTotal.inc({ method, route, status });
    httpRequestDurationSeconds.observe(
      { method, route, status },
      reply.elapsedTime / 1000,
    );
  });

  // Rate limiting. Scope is per-operator (falls back to IP for unauth routes).
  await app.register(fastifyRateLimit, {
    max: config.rateLimitPerSecond,
    timeWindow: "1 second",
    keyGenerator: (req) => {
      const auth = (req.user as { operator_id?: string } | undefined)
        ?.operator_id;
      return auth ?? req.ip;
    },
    // /metrics + /healthz should never throttle — skip them.
    skipOnError: true,
  });

  await app.register(fastifyWebsocket, {
    options: {
      maxPayload: 1024 * 1024, // 1 MiB
    },
  });

  // ----- State + bus bridge -----

  const store = new EventStore({
    log,
    redis,
    snapshotIntervalMs: config.stateSnapshotIntervalMs,
  });
  await store.start();

  store.subscribe((_topic, _patch, v) => {
    stateServerVersion.set(v);
  });

  let bridge: BridgeHandle | null = null;
  if (bus) {
    bridge = await startBridge({ bus, store, log });
  }

  const audit = new AuditLog(redis);
  const idempotency: IdempotencyStore = redis
    ? new RedisIdempotencyStore(redis)
    : new MemoryIdempotencyStore();

  // ----- Routes -----

  installAuthErrorHandler(app);
  registerHealthRoutes(app, { config, redis });
  registerAuthRoutes(app, { config, audit });
  registerSnapshotRoutes(app, { config, store });
  registerSignalsRoutes(app, {
    config,
    store,
    bus,
    audit,
    idempotency,
  });
  registerControlRoutes(app, {
    config,
    store,
    bus,
    bridge,
    audit,
    idempotency,
  });
  registerWsRoute(app, { config, store });

  const stop = async () => {
    if (bridge) await bridge.stop();
    await store.stop();
    // Forcibly drop any lingering WebSocket / keep-alive connections so
    // `app.close()` doesn't wait indefinitely.
    const server = app.server as unknown as {
      closeAllConnections?: () => void;
      closeIdleConnections?: () => void;
    };
    try {
      server.closeIdleConnections?.();
      server.closeAllConnections?.();
    } catch {
      /* ignore */
    }
    await app.close();
  };

  return { app, store, bridge, audit, idempotency, stop };
}
