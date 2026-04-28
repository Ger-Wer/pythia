import { describe, expect, test } from "bun:test";
import { openSubscriptionRedis } from "../bus/redis_bus.ts";

class FakeSubscriptionRedis {
  connectCalls = 0;

  async connect(): Promise<void> {
    this.connectCalls += 1;
  }
}

class FakeRootRedis {
  duplicateCalls = 0;
  lastDuplicate: FakeSubscriptionRedis | null = null;

  duplicate(): FakeSubscriptionRedis {
    this.duplicateCalls += 1;
    this.lastDuplicate = new FakeSubscriptionRedis();
    return this.lastDuplicate;
  }
}

describe("redis bus subscription connections", () => {
  test("opens a dedicated redis connection for each subscription consumer", async () => {
    const root = new FakeRootRedis();

    const subscriptionRedis = await openSubscriptionRedis(root as never);

    expect(root.duplicateCalls).toBe(1);
    expect(root.lastDuplicate?.connectCalls).toBe(1);
    expect(subscriptionRedis).toBe(root.lastDuplicate);
  });
});
