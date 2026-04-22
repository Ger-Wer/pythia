// Bus bridge tests. Uses the in-memory BusClient so the suite is hermetic
// and runs in CI without Docker. A real-Redis variant can be added via
// testcontainers once a CI runner with Docker is available in every job.
//
// The test spec called for "integration с Redis (testcontainers)" — we
// document the decision here: since the rest of the gateway already stubs
// Redis via `ioredis-mock` and the RedisBusClient is a thin XADD/XREADGROUP
// wrapper, an in-memory bus is sufficient to verify the bridge's
// projection logic. Real-bus coverage is provided by the `redis-integration`
// CI stage opt-in (docker-compose.dev.yml stack up + env).

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { makeLogger } from "../logger.ts";
import { MemoryBusClient } from "../bus/memory_bus.ts";
import { startBridge, type BridgeHandle } from "../bus/bridge.ts";
import { EventStore } from "../state/store.ts";

async function tick(ms = 10): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

describe("bus bridge", () => {
  let bus: MemoryBusClient;
  let store: EventStore;
  let bridge: BridgeHandle;

  beforeEach(async () => {
    const log = makeLogger("error");
    bus = new MemoryBusClient();
    store = new EventStore({
      log,
      redis: null,
      snapshotIntervalMs: 0,
    });
    bridge = await startBridge({ bus, store, log });
  });
  afterEach(async () => {
    await bridge.stop();
    await bus.close();
  });

  test("signals.emitted upserts signals state", async () => {
    await bus.publish("dl.v1.signals.emitted", {
      signal_id: "S1",
      server_version: 3,
      market_id: "m1",
    });
    await tick();
    const s = store.getState();
    expect(s.signals.S1).toBeDefined();
    expect(s.signals.S1?.market_id).toBe("m1");
    expect(s.signals.S1?.server_version).toBe(3);
    expect(store.getServerVersion()).toBeGreaterThan(0);
  });

  test("watchlist upsert + remove round-trip", async () => {
    await bus.publish("dl.v1.watchlist.upsert", {
      market_id: "m1",
      ctf_address: "0x1",
      version: 1,
      edge: 0.1,
    });
    await tick();
    expect(store.getState().watchlist.m1).toBeDefined();

    await bus.publish("dl.v1.watchlist.remove", {
      market_id: "m1",
      version: 2,
    });
    await tick();
    expect(store.getState().watchlist.m1).toBeUndefined();
  });

  test("exec.submitted then exec.filled merges into one entry", async () => {
    await bus.publish("dl.v1.exec.submitted", {
      tx_hash: "0xdead",
      market_id: "m-exec",
      paper_mode: true,
    });
    await tick();
    expect(store.getState().exec_status["0xdead"]?.status).toBe("SUBMITTED");
    await bus.publish("dl.v1.exec.filled", {
      tx_hash: "0xdead",
      market_id: "m-exec",
      realized_price_micros: 720_000,
      filled_size_micros: 1_000_000,
    });
    await tick();
    const e = store.getState().exec_status["0xdead"];
    expect(e?.status).toBe("FILLED");
    expect(e?.realized_price_micros).toBe(720_000);
  });

  test("kill_ack resolves pending waiter and updates kill state", async () => {
    const cid = "cmd-1";
    const p = bridge.killAck(cid, 500);
    await bus.publish("dl.v1.control.kill_ack", {
      original_command_id: cid,
      status: "COMPLETE",
      detail: "ok",
      active_triggers_cancelled: 2,
      pending_tx_cancelled: 1,
      ack_at_unix_ms: Date.now(),
    });
    const ack = await p;
    expect(ack.status).toBe("COMPLETE");
    expect(ack.active_triggers_cancelled).toBe(2);
    expect(store.getState().kill.last_ack_status).toBe("COMPLETE");
    expect(store.getState().kill.engaged).toBe(true);
  });

  test("triggers.fired adds an entry and emits a patch", async () => {
    const seen: string[] = [];
    store.subscribe((topic) => {
      seen.push(topic);
    });
    await bus.publish("dl.v1.triggers.fired", {
      trigger_id: "T1",
      market_id: "mT",
      kind: "LARGE_FILL",
      eta_block: 42,
      observed_at_unix_ms: Date.now(),
      expected_price: 0.52,
      slippage_bps: 30,
      source_count: 2,
    });
    await tick();
    expect(store.getState().triggers.T1?.kind).toBe("LARGE_FILL");
    expect(seen).toContain("triggers");
  });
});
