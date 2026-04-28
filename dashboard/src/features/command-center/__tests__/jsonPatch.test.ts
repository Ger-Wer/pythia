import { afterEach, describe, expect, it } from "vitest";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { Signal } from "../store/types";

function makeSignal(id: string, edge: number): Signal {
  return {
    id,
    market: `mkt-${id}`,
    question: `Question ${id}`,
    priority: "OK",
    edge,
    confidence: 0.5,
    positionSuggested: 200,
    liquidityUsd: 50_000,
    expiresAt: Date.now() + 60_000,
    rationale: "",
    evidence: [],
    localDecision: null,
  };
}

describe("CommandCenter store — JSON Patch applier", () => {
  afterEach(() => {
    useCommandCenterStore.getState().reset();
  });

  it("applies replace ops to nested fields", () => {
    const s = useCommandCenterStore.getState();
    s.addSignal(makeSignal("a", 0.1));
    s.applyDelta([
      { op: "replace", path: "/signals/a/edge", value: 0.25 },
      { op: "replace", path: "/health/wsConnected", value: false },
    ]);
    const next = useCommandCenterStore.getState();
    expect(next.signals.a?.edge).toBeCloseTo(0.25);
    expect(next.health.wsConnected).toBe(false);
  });

  it("applies add ops to collections", () => {
    const s = useCommandCenterStore.getState();
    s.applyDelta([
      {
        op: "add",
        path: "/signals/b",
        value: makeSignal("b", 0.05),
      },
      { op: "add", path: "/signalOrder/-", value: "b" },
    ]);
    const next = useCommandCenterStore.getState();
    expect(next.signals.b).toBeDefined();
    expect(next.signalOrder).toContain("b");
  });

  it("handles delta without breaking other slices", () => {
    const s = useCommandCenterStore.getState();
    s.addSignal(makeSignal("x", 0));
    const originalHealth = s.health;
    s.applyDelta([{ op: "replace", path: "/signals/x/confidence", value: 0.9 }]);
    const next = useCommandCenterStore.getState();
    expect(next.signals.x?.confidence).toBeCloseTo(0.9);
    expect(next.health.redisPingMs).toBe(originalHealth.redisPingMs);
  });
});

describe("CommandCenter store — cursor math", () => {
  afterEach(() => {
    useCommandCenterStore.getState().reset();
  });

  it("clamps cursor inside [0, len-1]", () => {
    const s = useCommandCenterStore.getState();
    s.addSignal(makeSignal("a", 0.1));
    s.addSignal(makeSignal("b", 0.2));
    s.addSignal(makeSignal("c", 0.3));
    s.setCursor(0);
    s.moveCursor(5);
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(2);
    s.moveCursor(-99);
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(0);
  });

  it("keeps cursor at 0 when queue is empty", () => {
    const s = useCommandCenterStore.getState();
    s.moveCursor(3);
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(0);
  });

  it("local decision persists on a signal", () => {
    const s = useCommandCenterStore.getState();
    s.addSignal(makeSignal("a", 0));
    s.setLocalDecision("a", "accept");
    expect(useCommandCenterStore.getState().signals.a?.localDecision).toBe("accept");
  });
});
