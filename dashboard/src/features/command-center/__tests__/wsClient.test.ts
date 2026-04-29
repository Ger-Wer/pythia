import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startCommandCenterFeed } from "../ws/client";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

const MARKET_ID = "paper-e2e-20260429c";
const TX_HASH = "0x" + "2".repeat(64);

type Listener = (event?: { data?: string }) => void;

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readonly sent: string[] = [];
  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    const current = this.listeners.get(type) ?? [];
    current.push(listener);
    this.listeners.set(type, current);
  }

  send(payload: string) {
    this.sent.push(payload);
  }

  close() {
    this.emit("close");
  }

  emit(type: string, event?: { data?: string }) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function snapshotFrame() {
  return {
    kind: "snapshot",
    server_version: 10,
    topics: ["signals", "watchlist", "exec_status"],
    data: {
      signals: {
        "sig-paper-e2e-20260429c": {
          signal_id: "sig-paper-e2e-20260429c",
          server_version: 10,
          market_id: MARKET_ID,
          ctf_address: "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
          market_question: "Will the WS client project live exec state?",
          edge: 0.031,
          p_fundamental: 0.63,
          p_market: 0.57,
          confidence_low: 0.6,
          confidence_high: 0.76,
          expected_value_bps: 310,
          side_hint: "YES",
          suggested_position_micros: 2_000_000,
          expires_at_unix_ms: 1_777_000_001_000,
          llm_rationale: "WS snapshot test signal",
          evidence_urls: [],
          issued_at_unix_ms: 1_776_999_905_000,
          priority: "URGENT",
        },
      },
      watchlist: {
        [MARKET_ID]: {
          market_id: MARKET_ID,
          ctf_address: "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
          version: 1,
          edge: 0.031,
          p_fundamental: 0.63,
          confidence: 0.68,
          side_hint: "YES",
          max_position_usd_micros: 4_000_000,
          max_slippage_bps: 15,
          ttl_ms: 60000,
          issued_at_unix_ms: 1_776_999_905_000,
        },
      },
      exec_status: {
        [TX_HASH]: {
          tx_hash: TX_HASH,
          market_id: MARKET_ID,
          status: "SUBMITTED",
          submitted_at_unix_ms: 1_776_999_906_000,
          paper_mode: true,
        },
      },
      kill: { engaged: false },
    },
  };
}

describe("startCommandCenterFeed", () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    Object.assign(globalThis, {
      WebSocket: FakeWebSocket as unknown as typeof WebSocket,
    });
  });

  afterEach(() => {
    useCommandCenterStore.getState().reset();
    FakeWebSocket.instances = [];
    Object.assign(globalThis, { WebSocket: originalWebSocket });
  });

  it("subscribes to gateway topics, answers ping, and projects exec updates", () => {
    const logger = { info: vi.fn(), warn: vi.fn() };

    const handle = startCommandCenterFeed({
      wsUrl: "ws://127.0.0.1:9103/ws/v1",
      wsToken: "test-token",
      debug: true,
      logger,
      startMockFeed: () => ({ mode: "mock", stop: vi.fn() }),
    });

    const socket = FakeWebSocket.instances[0];
    expect(socket).toBeDefined();
    expect(socket?.url).toContain("token=test-token");

    socket?.emit("open");
    expect(JSON.parse(socket?.sent[0] ?? "{}")).toMatchObject({
      kind: "subscribe",
      topics: ["signals", "watchlist", "triggers", "exec_status"],
    });

    socket?.emit("message", { data: JSON.stringify(snapshotFrame()) });
    expect(useCommandCenterStore.getState().watchlist[MARKET_ID]?.executionStatus).toBe(
      "SUBMITTED",
    );

    socket?.emit("message", { data: JSON.stringify({ kind: "ping", ts: 1234 }) });
    expect(JSON.parse(socket?.sent.at(-1) ?? "{}")).toEqual({ kind: "pong", ts: 1234 });

    socket?.emit("message", {
      data: JSON.stringify({
        kind: "patch",
        server_version: 11,
        topic: "exec_status",
        patch: [
          { op: "replace", path: `/${TX_HASH}/status`, value: "FILLED" },
          { op: "add", path: `/${TX_HASH}/filled_at_unix_ms`, value: 1_776_999_907_000 },
        ],
      }),
    });

    const state = useCommandCenterStore.getState();
    expect(state.watchlist[MARKET_ID]?.executionStatus).toBe("FILLED");
    expect(state.triggers.slice(0, 2).map((item) => item.kind)).toEqual(["filled", "submitted"]);
    expect(logger.info).toHaveBeenCalledWith(
      "[command-center] exec status applied",
      expect.objectContaining({
        marketId: MARKET_ID,
        status: "FILLED",
        txHash: TX_HASH,
      }),
    );

    handle.stop();
  });
});
