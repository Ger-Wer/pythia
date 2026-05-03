import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CommandCenterPage } from "../CommandCenterPage";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { Signal, WatchlistRow } from "../store/types";

function sig(id: string, priority: Signal["priority"] = "OK"): Signal {
  return {
    id,
    market: `mkt-${id}`,
    question: `Question ${id}`,
    priority,
    edge: 0.1,
    confidence: 0.5,
    positionSuggested: 200,
    liquidityUsd: 50_000,
    expiresAt: Date.now() + 60_000,
    rationale: "rationale",
    evidence: [],
    localDecision: null,
    serverVersion: 0,
  };
}

function row(market: string): WatchlistRow {
  return {
    market,
    question: `Q for ${market}`,
    side: "YES",
    edge: 0.05,
    liquidityUsd: 100_000,
    depthPct: 0.6,
    slippageBps: 12,
    trigger: "IDLE",
    priceSeries: [0.5, 0.51, 0.49, 0.5],
  };
}

function seedStore() {
  const s = useCommandCenterStore.getState();
  s.addSignal(sig("a", "CRIT"));
  s.addSignal(sig("b", "WARN"));
  s.addSignal(sig("c", "OK"));
  s.upsertWatchlistRow(row("mkt-a"));
  s.upsertWatchlistRow(row("mkt-b"));
  s.pushTrigger({
    id: "trg-1",
    at: Date.now(),
    market: "mkt-a",
    kind: "filled",
    txHash: "0x" + "a".repeat(64),
    realizedPrice: 0.52,
    gasGwei: 64,
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/command-center?preview=1"]}>
      <CommandCenterPage disableFeed />
    </MemoryRouter>,
  );
}

describe("CommandCenterPage — smoke", () => {
  afterEach(() => {
    cleanup();
    useCommandCenterStore.getState().reset();
  });

  it("renders all panel regions when accessible", () => {
    seedStore();
    renderPage();
    expect(screen.getByTestId("command-center-root")).toBeInTheDocument();
    expect(screen.getByText(/Triage Queue/i)).toBeInTheDocument();
    expect(screen.getByText(/Active Watchlist/i)).toBeInTheDocument();
    expect(screen.getByText(/Edge Heatmap/i)).toBeInTheDocument();
    expect(screen.getByText(/Execution/i)).toBeInTheDocument();
    expect(screen.getByText(/Latency/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/System status banner/i)).toBeInTheDocument();
    // Status strip + kill switch placeholder copy.
    expect(screen.getByText(/disabled in Phase 1/i)).toBeInTheDocument();
  });

  it("hides UI when flag disabled and no preview query", () => {
    render(
      <MemoryRouter initialEntries={["/command-center"]}>
        <CommandCenterPage disableFeed />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("command-center-root")).not.toBeInTheDocument();
    expect(screen.getByText(/Command Center disabled/i)).toBeInTheDocument();
  });

  it("j/k hotkeys move triage cursor", async () => {
    const user = userEvent.setup();
    seedStore();
    renderPage();
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(0);
    await user.keyboard("j");
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(1);
    await user.keyboard("j");
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(2);
    await user.keyboard("k");
    expect(useCommandCenterStore.getState().cursor.triageIndex).toBe(1);
  });

  it("A records local accept decision; R records reject", async () => {
    const user = userEvent.setup();
    seedStore();
    renderPage();
    await user.keyboard("a");
    const first = useCommandCenterStore.getState();
    const selectedId = first.signalOrder[first.cursor.triageIndex]!;
    expect(first.signals[selectedId]?.localDecision).toBe("accept");

    await user.keyboard("j");
    await user.keyboard("r");
    const s2 = useCommandCenterStore.getState();
    const sel2 = s2.signalOrder[s2.cursor.triageIndex]!;
    expect(s2.signals[sel2]?.localDecision).toBe("reject");
  });

  it("Space opens and closes Market Detail modal", async () => {
    const user = userEvent.setup();
    seedStore();
    renderPage();
    await user.keyboard(" ");
    expect(useCommandCenterStore.getState().detailOpen).toBe(true);
    expect(screen.getByRole("dialog", { name: /Market detail/i })).toBeInTheDocument();
    await user.keyboard(" ");
    expect(useCommandCenterStore.getState().detailOpen).toBe(false);
  });

  it("Ctrl+Shift+K opens kill switch disabled modal", async () => {
    const user = userEvent.setup();
    seedStore();
    renderPage();
    await user.keyboard("{Control>}{Shift>}k{/Shift}{/Control}");
    expect(useCommandCenterStore.getState().killModalOpen).toBe(true);
    expect(screen.getByRole("dialog", { name: /Kill switch disabled/i })).toBeInTheDocument();
  });

  it("Ctrl+/ toggles hotkey help overlay", async () => {
    const user = userEvent.setup();
    seedStore();
    renderPage();
    await user.keyboard("{Control>}/{/Control}");
    expect(useCommandCenterStore.getState().helpOpen).toBe(true);
    expect(screen.getByRole("dialog", { name: /Hotkey reference/i })).toBeInTheDocument();
    await user.keyboard("{Control>}/{/Control}");
    expect(useCommandCenterStore.getState().helpOpen).toBe(false);
  });

  function withAuthQueryString<T>(qs: string, fn: () => T): T {
    const original = window.location.search;
    window.history.replaceState(null, "", `/command-center${qs}`);
    try {
      return fn();
    } finally {
      window.history.replaceState(null, "", `/command-center${original}`);
    }
  }

  it("A hotkey POSTs to gateway accept endpoint when auth query params are present", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ decision_id: "dec-1", action: "ACCEPT" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchImpl);
    try {
      await withAuthQueryString(
        "?preview=1&token=tok-test&operator_id=op-1&cc_api_url=https%3A%2F%2Fgw.example",
        async () => {
          const user = userEvent.setup();
          seedStore();
          render(
            <MemoryRouter initialEntries={["/command-center?preview=1"]}>
              <CommandCenterPage disableFeed />
            </MemoryRouter>,
          );

          await user.keyboard("a");
          await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

          const [url, init] = fetchImpl.mock.calls[0]!;
          // seedStore() prepends a, b, c → signalOrder = ["c", "b", "a"], cursor=0 → "c".
          expect(url).toBe("https://gw.example/api/v1/signals/c/accept");
          const reqInit = init as RequestInit;
          const headers = reqInit.headers as Record<string, string>;
          expect(headers["Authorization"]).toBe("Bearer tok-test");
          const body = JSON.parse(reqInit.body as string);
          expect(body).toMatchObject({
            operator_id: "op-1",
            expected_version: 0,
          });
          expect(typeof body.idempotency_key).toBe("string");
          expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8);

          const state = useCommandCenterStore.getState();
          const id = state.signalOrder[state.cursor.triageIndex]!;
          expect(state.signals[id]?.localDecision).toBe("accept");
        },
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("A hotkey rolls back optimistic decision on 409 version_conflict", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: "version_conflict",
          actual_version: 5,
          expected_version: 0,
        }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchImpl);
    try {
      await withAuthQueryString(
        "?preview=1&token=tok-test&operator_id=op-1&cc_api_url=https%3A%2F%2Fgw.example",
        async () => {
          const user = userEvent.setup();
          seedStore();
          render(
            <MemoryRouter initialEntries={["/command-center?preview=1"]}>
              <CommandCenterPage disableFeed />
            </MemoryRouter>,
          );

          await user.keyboard("a");
          await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
          // After server rejects with 409, optimistic decision is rolled back
          // to its previous value (null in seedStore).
          await waitFor(() => {
            const s = useCommandCenterStore.getState();
            const id = s.signalOrder[s.cursor.triageIndex]!;
            expect(s.signals[id]?.localDecision).toBeNull();
          });
        },
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
