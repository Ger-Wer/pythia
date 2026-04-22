import type { Signal, WatchlistRow } from "../store/types";

const MARKETS = [
  { id: "mkt-btc-130k", q: "Will BTC close above $130k on 2026-05-01?" },
  { id: "mkt-eth-7k", q: "Will ETH exceed $7,000 by end of May 2026?" },
  { id: "mkt-fed-cut-june", q: "Will the Fed cut rates at the June 2026 FOMC?" },
  { id: "mkt-nba-lakers-champ", q: "Lakers win NBA Finals 2026?" },
  { id: "mkt-oscar-film", q: "Will 'The Cartographer' win Best Picture 2026?" },
  { id: "mkt-gdp-q1", q: "US Q1 2026 GDP growth above 2.5%?" },
  { id: "mkt-spx-6500", q: "Will S&P 500 close above 6500 by 2026-06-30?" },
  { id: "mkt-nvda-earnings", q: "NVDA beat Q1 EPS consensus by >10%?" },
  { id: "mkt-tsla-delivery", q: "Tesla Q2 deliveries > 500k?" },
  { id: "mkt-election-governor-tx", q: "Texas governor race won by challenger?" },
];

const PRIORITIES = ["CRIT", "WARN", "OK"] as const;

function pseudoRandom(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

export function seedInitialSignals(now: number): Signal[] {
  const rnd = pseudoRandom(42);
  return MARKETS.slice(0, 6).map((m, i) => {
    const pr = PRIORITIES[i % PRIORITIES.length];
    const edge = (rnd() - 0.35) * 0.6; // -0.21..+0.39
    return {
      id: `sig-${m.id}-${i}`,
      market: m.id,
      question: m.q,
      priority: pr,
      edge,
      confidence: 0.4 + rnd() * 0.55,
      positionSuggested: Math.round(100 + rnd() * 1500),
      liquidityUsd: Math.round(5_000 + rnd() * 500_000),
      expiresAt: now + Math.round((30 + rnd() * 180) * 1000),
      rationale:
        "LLM synthesis: recent polling aggregator shift + on-chain oracle agreement within tolerance. See evidence below.",
      evidence: [
        "https://polymarket.com/market/example",
        "https://example.com/news/aggregator",
        "https://example.com/analyst-report",
      ],
      localDecision: null,
    };
  });
}

export function seedInitialWatchlist(): WatchlistRow[] {
  const rnd = pseudoRandom(7);
  return MARKETS.map((m, i) => ({
    market: m.id,
    question: m.q,
    side: i % 2 === 0 ? "YES" : "NO",
    edge: (rnd() - 0.4) * 0.5,
    liquidityUsd: Math.round(8_000 + rnd() * 600_000),
    depthPct: rnd(),
    slippageBps: Math.round(rnd() * 40),
    trigger: (["IDLE", "ARMED", "COOLDOWN"] as const)[i % 3],
    priceSeries: Array.from({ length: 40 }, () => 0.3 + rnd() * 0.5),
  }));
}

export const MOCK_MARKET_IDS = MARKETS.map((m) => m.id);
export const MOCK_MARKET_QUESTIONS: Record<string, string> = Object.fromEntries(
  MARKETS.map((m) => [m.id, m.q]),
);
