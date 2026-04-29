import { afterEach, describe, expect, it } from "vitest";
import {
  createGatewayProjection,
  type GatewayPatchFrame,
  type GatewaySnapshotFrame,
} from "../ws/gatewayAdapter";

const MARKET_ID = "paper-e2e-20260429c";
const TX_HASH = "0x" + "1".repeat(64);

function makeSnapshot(): GatewaySnapshotFrame {
  return {
    kind: "snapshot",
    server_version: 12,
    topics: ["signals", "watchlist", "triggers", "exec_status"],
    data: {
      signals: {
        "sig-paper-e2e-20260429c": {
          signal_id: "sig-paper-e2e-20260429c",
          server_version: 12,
          market_id: MARKET_ID,
          ctf_address: "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
          market_question: "Will the paper-mode market settle above 50%?",
          edge: 0.042,
          p_fundamental: 0.61,
          p_market: 0.54,
          confidence_low: 0.58,
          confidence_high: 0.82,
          expected_value_bps: 420,
          side_hint: "YES",
          suggested_position_micros: 2_500_000,
          expires_at_unix_ms: 1_777_000_000_000,
          llm_rationale: "Mock paper signal for the command center.",
          evidence_urls: ["https://example.com/evidence"],
          issued_at_unix_ms: 1_776_999_900_000,
          priority: "HIGH",
        },
      },
      watchlist: {
        [MARKET_ID]: {
          market_id: MARKET_ID,
          ctf_address: "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
          version: 2,
          edge: 0.042,
          p_fundamental: 0.61,
          confidence: 0.7,
          side_hint: "YES",
          max_position_usd_micros: 5_000_000,
          max_slippage_bps: 17,
          ttl_ms: 60000,
          issued_at_unix_ms: 1_776_999_900_000,
        },
      },
      triggers: {
        "trg-paper-e2e-20260429c": {
          trigger_id: "trg-paper-e2e-20260429c",
          market_id: MARKET_ID,
          kind: "LARGE_FILL",
          state: "FIRED",
          eta_block: 615,
          observed_at_unix_ms: 1_776_999_901_000,
          expected_price: 0.55,
          slippage_bps: 11,
          source_count: 2,
        },
      },
      exec_status: {
        [TX_HASH]: {
          tx_hash: TX_HASH,
          market_id: MARKET_ID,
          status: "SUBMITTED",
          submitted_at_unix_ms: 1_776_999_902_000,
          paper_mode: true,
        },
      },
      kill: { engaged: false },
    },
  };
}

describe("gatewayAdapter", () => {
  afterEach(() => {
    // adapter instances are per-test and hold no global state,
    // but keep afterEach for symmetry with store-driven tests.
  });

  it("projects gateway snapshot into command center state", () => {
    const adapter = createGatewayProjection();
    const update = adapter.applySnapshot(makeSnapshot().data);

    expect(update.snapshot.signalOrder).toEqual(["sig-paper-e2e-20260429c"]);
    expect(update.snapshot.signals?.["sig-paper-e2e-20260429c"]?.question).toContain(
      "paper-mode market",
    );
    expect(update.snapshot.watchlistOrder).toEqual([MARKET_ID]);
    expect(update.snapshot.watchlist?.[MARKET_ID]?.executionStatus).toBe("SUBMITTED");
    expect(update.snapshot.watchlist?.[MARKET_ID]?.trigger).toBe("FIRED");
    expect(update.snapshot.health?.killSwitchEnabled).toBe(false);
    expect(update.snapshot.triggers?.[0]).toMatchObject({
      market: MARKET_ID,
      kind: "submitted",
      txHash: TX_HASH,
    });
    expect(update.execTransitions).toEqual([
      {
        txHash: TX_HASH,
        marketId: MARKET_ID,
        status: "SUBMITTED",
      },
    ]);
  });

  it("captures exec-status patch transitions and keeps both events visible", () => {
    const adapter = createGatewayProjection();
    adapter.applySnapshot(makeSnapshot().data);

    const patch: GatewayPatchFrame = {
      kind: "patch",
      server_version: 13,
      topic: "exec_status",
      patch: [
        { op: "replace", path: `/${TX_HASH}/status`, value: "FILLED" },
        { op: "add", path: `/${TX_HASH}/filled_at_unix_ms`, value: 1_776_999_903_000 },
        { op: "add", path: `/${TX_HASH}/filled_size_micros`, value: 1_000_000 },
        { op: "add", path: `/${TX_HASH}/realized_price_micros`, value: 540000 },
      ],
    };

    const update = adapter.applyPatch(patch.topic, patch.patch);
    expect(update.snapshot.watchlist?.[MARKET_ID]?.executionStatus).toBe("FILLED");
    expect(update.execTransitions).toEqual([
      {
        txHash: TX_HASH,
        marketId: MARKET_ID,
        status: "FILLED",
      },
    ]);
    expect(update.snapshot.triggers?.map((item) => item.kind)).toEqual([
      "filled",
      "submitted",
    ]);
  });
});
