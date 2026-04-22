// Entrypoint: reads config, opens Redis/NATS clients, builds Fastify app,
// installs a graceful shutdown hook.

import IORedis from "ioredis";
import { loadConfig } from "./config.ts";
import { makeLogger } from "./logger.ts";
import { buildServer } from "./server.ts";
import { RedisBusClient } from "./bus/redis_bus.ts";
import { startNatsTelemetry } from "./bus/nats_bus.ts";

async function main(): Promise<void> {
  const config = loadConfig();
  const log = makeLogger(config.logLevel, { service: "gateway-api" });

  // ---- Redis client (bus + idempotency + state persistence). ----
  const redis = new IORedis(config.redisUrl, {
    maxRetriesPerRequest: null,
    lazyConnect: true,
  });
  redis.on("error", (err) => log.warn("redis error", { err: err.message }));
  try {
    await redis.connect();
  } catch (err) {
    log.warn("redis connect failed; continuing in degraded mode", {
      err: (err as Error).message,
    });
  }

  // Dedicated connection for the bus consumer loop so BLOCK-ing reads don't
  // back up other commands.
  const busRedis = redis.duplicate();
  try {
    await busRedis.connect();
  } catch {
    /* best-effort */
  }
  const bus = new RedisBusClient(busRedis, log);

  // ---- Build Fastify app ----
  const handle = await buildServer({
    config,
    log,
    redis,
    bus,
  });

  // ---- NATS telemetry (soft-fail) ----
  const nats = await startNatsTelemetry({
    url: config.natsUrl,
    log,
  });

  // ---- Listen ----
  await handle.app.listen({ host: config.host, port: config.port });
  log.info("gateway-api listening", {
    host: config.host,
    port: config.port,
  });

  // ---- Shutdown ----
  const shutdown = async (signal: string) => {
    log.info("shutdown signal", { signal });
    try {
      await handle.stop();
      await nats.stop();
      await bus.close();
      await busRedis.quit().catch(() => {});
      await redis.quit().catch(() => {});
    } catch (err) {
      log.warn("shutdown error", { err: (err as Error).message });
    }
    process.exit(0);
  };

  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("fatal: gateway-api failed to start", err);
  process.exit(1);
});
