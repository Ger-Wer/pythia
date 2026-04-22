// Health + metrics endpoints.

import type { FastifyInstance } from "fastify";
import type { Redis } from "ioredis";
import type { Config } from "../config.ts";
import { registry } from "../metrics.ts";

export function registerHealthRoutes(
  app: FastifyInstance,
  deps: { config: Config; redis: Redis | null },
): void {
  const { redis } = deps;

  app.get(
    "/api/v1/healthz",
    {
      schema: {
        response: {
          200: {
            type: "object",
            required: ["status", "deps"],
            properties: {
              status: { type: "string" },
              deps: {
                type: "object",
                additionalProperties: { type: "string" },
              },
            },
          },
          503: { type: "object" },
        },
      },
      config: { rateLimit: false },
    },
    async (_req, reply) => {
      const deps: Record<string, string> = {};
      let redisOk = true;
      if (redis) {
        try {
          const pong = await redis.ping();
          deps.redis = pong === "PONG" ? "ok" : "degraded";
        } catch {
          deps.redis = "down";
          redisOk = false;
        }
      } else {
        deps.redis = "disabled";
      }
      // NATS liveness is a soft dependency — telemetry only — so it never
      // flips the overall status.
      deps.nats = "best-effort";
      if (!redisOk) reply.code(503);
      return { status: redisOk ? "ok" : "degraded", deps };
    },
  );

  app.get(
    "/metrics",
    { config: { rateLimit: false } },
    async (_req, reply) => {
      reply.header("content-type", registry.contentType);
      return registry.metrics();
    },
  );
}
