import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { Signal, TriggerEvent, TriggerState, WatchlistRow } from "../store/types";
import {
  MOCK_MARKET_IDS,
  MOCK_MARKET_QUESTIONS,
  seedInitialSignals,
  seedInitialWatchlist,
} from "./seed";

interface MockFeedHandle {
  stop: () => void;
}

const PRIORITIES = ["CRIT", "WARN", "OK"] as const;
const TRIGGER_STATES: TriggerState[] = ["IDLE", "ARMED", "FIRED", "COOLDOWN"];
const TRIGGER_KINDS = ["fired", "blocked", "submitted", "filled", "reverted"] as const;

function rnd() {
  return Math.random();
}

function randomTxHash(): string {
  let out = "0x";
  for (let i = 0; i < 64; i += 1) out += "0123456789abcdef"[Math.floor(rnd() * 16)];
  return out;
}

function randomMarketId(): string {
  return MOCK_MARKET_IDS[Math.floor(rnd() * MOCK_MARKET_IDS.length)]!;
}

function buildRandomSignal(): Signal {
  const market = randomMarketId();
  const priority = PRIORITIES[Math.floor(rnd() * PRIORITIES.length)]!;
  const edge = (rnd() - 0.35) * 0.6;
  const now = Date.now();
  return {
    id: `sig-${market}-${now}-${Math.floor(rnd() * 1e4)}`,
    market,
    question: MOCK_MARKET_QUESTIONS[market] ?? market,
    priority,
    edge,
    confidence: 0.3 + rnd() * 0.65,
    positionSuggested: Math.round(100 + rnd() * 2000),
    liquidityUsd: Math.round(5_000 + rnd() * 800_000),
    expiresAt: now + Math.round((30 + rnd() * 180) * 1000),
    rationale:
      "Mock: LLM synthesis suggests consensus drift versus internal probability estimate; review evidence before acting.",
    evidence: [
      "https://polymarket.com/market/example",
      "https://example.com/article",
    ],
    localDecision: null,
    serverVersion: 0,
  };
}

export function startMockFeed(options?: { seed?: boolean }): MockFeedHandle {
  const store = useCommandCenterStore.getState();

  if (options?.seed !== false) {
    const now = Date.now();
    const signals = seedInitialSignals(now);
    const watch = seedInitialWatchlist();
    signals.forEach((s) => store.addSignal(s));
    watch.forEach((w) => store.upsertWatchlistRow(w));
  }

  // Clock tick — cheap, drives countdowns.
  const clockId = window.setInterval(() => {
    useCommandCenterStore.getState().setNow(Date.now());
  }, 1000);

  // Health / status tick.
  const healthId = window.setInterval(() => {
    const s = useCommandCenterStore.getState();
    s.setHealth({
      redisPingMs: 2 + Math.floor(rnd() * 6),
      natsPingMs: 2 + Math.floor(rnd() * 7),
      polygonPingMs: 8 + Math.floor(rnd() * 15),
      llmBudgetRemaining: Math.max(0, s.health.llmBudgetRemaining - 0.0005),
      operatorTimeToDecisionSec: 5 + Math.floor(rnd() * 25),
      wsConnected: rnd() > 0.02,
    });
    s.pushLatencySample("mempoolToBroadcast", Math.round(2 + rnd() * 10));
    s.pushLatencySample("broadcastToMined", Math.round(200 + rnd() * 800));
    s.pushLatencySample("gammaPoll", Math.round(80 + rnd() * 160));
  }, 1000);

  // New signal every ~5s.
  const signalId = window.setInterval(() => {
    const s = useCommandCenterStore.getState();
    if (s.signalOrder.length > 40) {
      const drop = s.signalOrder[s.signalOrder.length - 1]!;
      s.removeSignal(drop);
    }
    s.addSignal(buildRandomSignal());
  }, 5000);

  // Mutate random watchlist edge every 10s (and trigger).
  const edgeId = window.setInterval(() => {
    const s = useCommandCenterStore.getState();
    const market = randomMarketId();
    const row: WatchlistRow | undefined = s.watchlist[market];
    if (!row) return;
    s.upsertWatchlistRow({
      ...row,
      edge: row.edge + (rnd() - 0.5) * 0.05,
      depthPct: Math.max(0, Math.min(1, row.depthPct + (rnd() - 0.5) * 0.1)),
      slippageBps: Math.max(0, row.slippageBps + Math.round((rnd() - 0.5) * 6)),
      trigger: TRIGGER_STATES[Math.floor(rnd() * TRIGGER_STATES.length)]!,
      priceSeries: [...row.priceSeries.slice(-39), row.priceSeries.at(-1)! + (rnd() - 0.5) * 0.04],
    });
  }, 10_000);

  // Trigger event every 15s.
  const triggerId = window.setInterval(() => {
    const kind = TRIGGER_KINDS[Math.floor(rnd() * TRIGGER_KINDS.length)]!;
    const ev: TriggerEvent = {
      id: `trg-${Date.now()}-${Math.floor(rnd() * 1e4)}`,
      at: Date.now(),
      market: randomMarketId(),
      kind,
      txHash: kind === "blocked" ? undefined : randomTxHash(),
      realizedPrice: kind === "filled" ? 0.3 + rnd() * 0.5 : undefined,
      gasGwei: kind === "submitted" || kind === "filled" ? Math.round(40 + rnd() * 80) : undefined,
      reason: kind === "blocked" ? "risk: position cap" : undefined,
    };
    useCommandCenterStore.getState().pushTrigger(ev);
  }, 15_000);

  return {
    stop: () => {
      clearInterval(clockId);
      clearInterval(healthId);
      clearInterval(signalId);
      clearInterval(edgeId);
      clearInterval(triggerId);
    },
  };
}
