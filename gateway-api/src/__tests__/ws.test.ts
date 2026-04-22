// WebSocket protocol tests: snapshot-on-open, patch delivery after bus event,
// heartbeat ping/pong, unauth close.

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import WebSocket from "ws";
import { makeTestContext, type TestContext } from "./helpers.ts";

function wsUrl(ctx: TestContext, token?: string): string {
  const addr = ctx.handle.app.server.address();
  if (!addr || typeof addr === "string") throw new Error("no address");
  const base = `ws://127.0.0.1:${addr.port}/ws/v1`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

interface Frame {
  kind: string;
  [k: string]: unknown;
}

async function nextFrame(ws: WebSocket, kind: string, timeoutMs = 1000): Promise<Frame> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      ws.off("message", handler);
      reject(new Error(`timeout waiting for ${kind}`));
    }, timeoutMs);
    function handler(raw: WebSocket.RawData): void {
      const msg = JSON.parse(raw.toString()) as Frame;
      if (msg.kind === kind) {
        clearTimeout(timer);
        ws.off("message", handler);
        resolve(msg);
      }
    }
    ws.on("message", handler);
  });
}

describe("WSS /ws/v1", () => {
  let ctx: TestContext;
  beforeEach(async () => {
    ctx = await makeTestContext({
      wsHeartbeatIntervalMs: 100,
      wsSnapshotIntervalMs: 60_000,
    });
  });
  afterEach(async () => {
    await ctx.close();
  });

  test("rejects missing token", async () => {
    const ws = new WebSocket(wsUrl(ctx));
    const code = await new Promise<number>((resolve) => {
      ws.on("close", (c) => resolve(c));
      ws.on("error", () => resolve(-1));
    });
    expect(code === 4401 || code === -1 || code === 1006).toBe(true);
  });

  test("sends snapshot on open", async () => {
    const ws = new WebSocket(wsUrl(ctx, ctx.token));
    await new Promise<void>((r) => ws.once("open", () => r()));
    const snap = await nextFrame(ws, "snapshot");
    expect(snap.kind).toBe("snapshot");
    expect(typeof snap.server_version).toBe("number");
    ws.close();
    await new Promise((r) => ws.once("close", () => r(null)));
  });

  test("delivers patch when bus emits a new signal", async () => {
    const ws = new WebSocket(wsUrl(ctx, ctx.token));
    await new Promise<void>((r) => ws.once("open", () => r()));
    await nextFrame(ws, "snapshot");
    // Fire a bus event. The bridge is synchronous via MemoryBusClient so we
    // just wait for the patch frame.
    void ctx.bus.publish("dl.v1.signals.emitted", {
      signal_id: "SIG-WS-1",
      server_version: 1,
      market_id: "poly-ws",
      issued_at_unix_ms: Date.now(),
    });
    const patch = await nextFrame(ws, "patch", 2_000);
    expect(patch.topic).toBe("signals");
    expect(Array.isArray(patch.patch)).toBe(true);
    ws.close();
    await new Promise((r) => ws.once("close", () => r(null)));
  });

  test("server pings; pong keeps the connection open", async () => {
    const ws = new WebSocket(wsUrl(ctx, ctx.token));
    await new Promise<void>((r) => ws.once("open", () => r()));
    await nextFrame(ws, "snapshot");
    const ping = await nextFrame(ws, "ping", 2_000);
    expect(typeof ping.ts).toBe("number");
    ws.send(JSON.stringify({ kind: "pong", ts: ping.ts }));
    // Should not close immediately — wait past two heartbeat intervals.
    await new Promise((r) => setTimeout(r, 250));
    expect(ws.readyState).toBe(WebSocket.OPEN);
    ws.close();
    await new Promise((r) => ws.once("close", () => r(null)));
  });

  test("subscribe narrows topics and re-sends snapshot", async () => {
    const ws = new WebSocket(wsUrl(ctx, ctx.token));
    await new Promise<void>((r) => ws.once("open", () => r()));
    await nextFrame(ws, "snapshot");
    ws.send(JSON.stringify({ kind: "subscribe", topics: ["signals"] }));
    const snap = await nextFrame(ws, "snapshot");
    expect(snap.topics).toEqual(["signals"]);
    expect(snap.data).toMatchObject({ signals: {} });
    expect((snap.data as Record<string, unknown>).watchlist).toBeUndefined();
    ws.close();
    await new Promise((r) => ws.once("close", () => r(null)));
  });
});
