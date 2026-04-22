export type TriggerState = "IDLE" | "ARMED" | "FIRED" | "COOLDOWN";

export type SignalPriority = "CRIT" | "WARN" | "OK";

export type LocalDecision = "accept" | "reject" | "snooze" | null;

export interface Signal {
  id: string;
  market: string;
  question: string;
  priority: SignalPriority;
  edge: number; // [-1..1]
  confidence: number; // [0..1]
  positionSuggested: number;
  liquidityUsd: number;
  expiresAt: number; // epoch ms
  rationale: string;
  evidence: string[];
  localDecision: LocalDecision;
}

export interface WatchlistRow {
  market: string;
  question: string;
  side: "YES" | "NO";
  edge: number;
  liquidityUsd: number;
  depthPct: number; // order book depth quality [0..1]
  slippageBps: number;
  trigger: TriggerState;
  priceSeries: number[];
}

export type TriggerEventKind = "fired" | "blocked" | "submitted" | "filled" | "reverted";

export interface TriggerEvent {
  id: string;
  at: number; // epoch ms
  market: string;
  kind: TriggerEventKind;
  txHash?: string;
  realizedPrice?: number;
  gasGwei?: number;
  reason?: string;
}

export interface SystemHealth {
  redisPingMs: number;
  natsPingMs: number;
  polygonPingMs: number;
  llmBudgetRemaining: number; // 0..1
  operatorTimeToDecisionSec: number;
  killSwitchEnabled: boolean;
  wsConnected: boolean;
}

export interface LatencyBuckets {
  mempoolToBroadcast: number[];
  broadcastToMined: number[];
  gammaPoll: number[];
}

export interface CommandCenterState {
  signals: Record<string, Signal>;
  signalOrder: string[]; // display order
  watchlist: Record<string, WatchlistRow>;
  watchlistOrder: string[];
  triggers: TriggerEvent[];
  health: SystemHealth;
  latency: LatencyBuckets;
  cursor: { triageIndex: number };
  detailOpen: boolean;
  killModalOpen: boolean;
  helpOpen: boolean;
  perfHudOpen: boolean;
  now: number;
}
