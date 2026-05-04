// Shared test helpers: build an isolated gateway server with in-memory bus
// and a fake Redis for idempotency/audit/state persistence.

import { randomBytes } from "node:crypto";
import RedisMock from "ioredis-mock";
import type { Config } from "../config.ts";
import { makeLogger } from "../logger.ts";
import { buildServer, type ServerHandle, type ServerDeps } from "../server.ts";
import { issueToken } from "../auth/paseto.ts";
import { hashPassword } from "../auth/password.ts";
import { MemoryBusClient } from "../bus/memory_bus.ts";

export interface TestContext {
  handle: ServerHandle;
  config: Config;
  bus: MemoryBusClient;
  redis: InstanceType<typeof RedisMock>;
  operatorId: string;
  password: string;
  token: string;
  close(): Promise<void>;
}

const sharedPasetoKey: Buffer = randomBytes(32);

export async function makeTestContext(
  overrides: Partial<Config> = {},
  serverOverrides: Partial<Pick<ServerDeps, "encodeProto">> = {},
): Promise<TestContext> {
  const operatorId = "test-operator";
  const password = "testpass";
  const passwordHash = await hashPassword(password);

  const config: Config = {
    host: "127.0.0.1",
    port: 0,
    logLevel: "error",
    redisUrl: "redis://mock",
    natsUrl: "nats://mock",
    pasetoKey: sharedPasetoKey,
    operatorId,
    operatorPasswordHash: passwordHash,
    killHmacSecret: "test-hmac-secret",
    tokenTtlSeconds: 3600,
    rateLimitPerSecond: 1000,
    wsMaxBufferedPatches: 4,
    wsSnapshotIntervalMs: 60_000,
    wsHeartbeatIntervalMs: 60_000,
    stateSnapshotIntervalMs: 60_000,
    killAckTimeoutMs: 500,
    idempotencyTtlSeconds: 60,
    ...overrides,
  };

  const log = makeLogger("error", { service: "gateway-api-test" });
  const redis = new RedisMock();
  const bus = new MemoryBusClient();
  const handle = await buildServer({
    config,
    log,
    redis: redis as unknown as import("ioredis").Redis,
    bus,
    encodeProto: serverOverrides.encodeProto,
  });
  await handle.app.listen({ host: "127.0.0.1", port: 0 });

  const { token } = await issueToken(
    config.pasetoKey,
    operatorId,
    config.tokenTtlSeconds,
  );

  return {
    handle,
    config,
    bus,
    redis,
    operatorId,
    password,
    token,
    close: async () => {
      await handle.stop();
      await redis.disconnect();
    },
  };
}

export function baseUrl(ctx: TestContext): string {
  const addr = ctx.handle.app.server.address();
  if (!addr || typeof addr === "string") {
    throw new Error("server has no address");
  }
  return `http://127.0.0.1:${addr.port}`;
}
