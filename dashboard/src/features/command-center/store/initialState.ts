import type { CommandCenterState } from "./types";

export function makeInitialState(): CommandCenterState {
  return {
    signals: {},
    signalOrder: [],
    watchlist: {},
    watchlistOrder: [],
    triggers: [],
    health: {
      redisPingMs: 3,
      natsPingMs: 4,
      polygonPingMs: 12,
      llmBudgetRemaining: 0.87,
      operatorTimeToDecisionSec: 18,
      killSwitchEnabled: false,
      wsConnected: true,
    },
    latency: {
      mempoolToBroadcast: Array.from({ length: 60 }, () => 0),
      broadcastToMined: Array.from({ length: 60 }, () => 0),
      gammaPoll: Array.from({ length: 60 }, () => 0),
    },
    cursor: { triageIndex: 0 },
    detailOpen: false,
    killModalOpen: false,
    helpOpen: false,
    perfHudOpen: false,
    now: Date.now(),
  };
}
