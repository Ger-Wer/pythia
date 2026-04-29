import { applyPatch, type Operation } from "fast-json-patch";
import type {
  CommandCenterSnapshot,
  CommandCenterState,
  LocalDecision,
  Signal as CommandCenterSignal,
  TriggerEvent,
  TriggerState,
  WatchlistRow,
} from "../store/types";

export type GatewayTopic = "signals" | "watchlist" | "triggers" | "exec_status";

export const ALL_GATEWAY_TOPICS: GatewayTopic[] = [
  "signals",
  "watchlist",
  "triggers",
  "exec_status",
];

type GatewayDecisionAction = "ACCEPT" | "REJECT" | "SNOOZE";
type GatewaySide = "YES" | "NO" | "UNSPECIFIED";
type GatewayTriggerState = "IDLE" | "ARMED" | "FIRED" | "COOLDOWN";
type GatewayPriority = "LOW" | "NORMAL" | "HIGH" | "URGENT" | "UNSPECIFIED";
type GatewayExecStatus = "SUBMITTED" | "FILLED" | "REVERTED";

interface GatewaySignal {
  signal_id: string;
  server_version: number;
  market_id: string;
  ctf_address: string;
  market_question: string;
  edge: number;
  p_fundamental: number;
  p_market: number;
  confidence_low: number;
  confidence_high: number;
  expected_value_bps: number;
  side_hint: GatewaySide;
  suggested_position_micros: number;
  expires_at_unix_ms: number;
  llm_rationale: string;
  evidence_urls: string[];
  issued_at_unix_ms: number;
  priority: GatewayPriority;
  decision?: {
    action: GatewayDecisionAction;
  };
}

interface GatewayWatchlistEntry {
  market_id: string;
  ctf_address: string;
  version: number;
  edge: number;
  p_fundamental: number;
  confidence: number;
  side_hint: GatewaySide;
  max_position_usd_micros: number;
  max_slippage_bps: number;
  ttl_ms: number;
  issued_at_unix_ms: number;
}

interface GatewayTrigger {
  trigger_id: string;
  market_id: string;
  kind: string;
  state: GatewayTriggerState;
  eta_block: number;
  observed_at_unix_ms: number;
  expected_price: number;
  slippage_bps: number;
  source_count: number;
}

interface GatewayExecStatusEntry {
  tx_hash: string;
  market_id: string;
  status: GatewayExecStatus;
  realized_price_micros?: number;
  filled_size_micros?: number;
  gas_spent_usd_micros?: number;
  block_number?: number;
  reason?: string;
  submitted_at_unix_ms?: number;
  filled_at_unix_ms?: number;
  reverted_at_unix_ms?: number;
  paper_mode: boolean;
}

interface GatewayKillState {
  engaged: boolean;
}

interface GatewayState {
  server_version: number;
  signals: Record<string, GatewaySignal>;
  watchlist: Record<string, GatewayWatchlistEntry>;
  triggers: Record<string, GatewayTrigger>;
  exec_status: Record<string, GatewayExecStatusEntry>;
  kill: GatewayKillState;
}

export interface GatewaySnapshotFrame {
  kind: "snapshot";
  server_version: number;
  data: Partial<GatewayState>;
  topics: GatewayTopic[];
}

export interface GatewayPatchFrame {
  kind: "patch";
  server_version: number;
  topic: GatewayTopic;
  patch: ReadonlyArray<Operation>;
}

export interface ExecTransition {
  txHash: string;
  marketId: string;
  status: GatewayExecStatus;
}

export interface GatewayProjectionUpdate {
  snapshot: CommandCenterSnapshot;
  execTransitions: ExecTransition[];
}

interface GatewayProjection {
  applySnapshot(data: Partial<GatewayState>): GatewayProjectionUpdate;
  applyPatch(topic: GatewayTopic, patch: ReadonlyArray<Operation>): GatewayProjectionUpdate;
}

const MAX_EVENTS = 50;

function emptyGatewayState(): GatewayState {
  return {
    server_version: 0,
    signals: {},
    watchlist: {},
    triggers: {},
    exec_status: {},
    kill: { engaged: false },
  };
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function mapPriority(priority: GatewayPriority): CommandCenterSignal["priority"] {
  if (priority === "URGENT" || priority === "HIGH") return "CRIT";
  if (priority === "NORMAL") return "WARN";
  return "OK";
}

function mapDecision(action?: GatewayDecisionAction): LocalDecision {
  if (action === "ACCEPT") return "accept";
  if (action === "REJECT") return "reject";
  if (action === "SNOOZE") return "snooze";
  return null;
}

function signalConfidence(signal: GatewaySignal): number {
  const low = clamp01(signal.confidence_low);
  const high = clamp01(signal.confidence_high);
  if (low === 0 && high === 0) return 0;
  if (low === 0) return high;
  if (high === 0) return low;
  return clamp01((low + high) / 2);
}

function toLiquidityUsd(proxyMicros: number): number {
  // Gateway does not expose true order book liquidity yet, so for the
  // operator view we temporarily scale the watchlist notional into a stable
  // "k USD" visual proxy instead of pretending we have book depth.
  return Math.max(1_000, Math.round(proxyMicros / 1_000));
}

function relevantExecTs(exec: GatewayExecStatusEntry): number {
  return (
    exec.filled_at_unix_ms ??
    exec.reverted_at_unix_ms ??
    exec.submitted_at_unix_ms ??
    0
  );
}

function execEventTs(exec: GatewayExecStatusEntry): number {
  if (exec.status === "FILLED") return exec.filled_at_unix_ms ?? Date.now();
  if (exec.status === "REVERTED") return exec.reverted_at_unix_ms ?? Date.now();
  return exec.submitted_at_unix_ms ?? Date.now();
}

function priceSeries(signal?: GatewaySignal): number[] {
  if (!signal) return [0.5];
  const series = [signal.p_market, signal.p_fundamental]
    .filter((value) => Number.isFinite(value) && value > 0)
    .map(clamp01);
  return series.length > 0 ? series : [0.5];
}

function toTriggerState(state?: GatewayTriggerState): TriggerState {
  if (state === "ARMED" || state === "FIRED" || state === "COOLDOWN") return state;
  return "IDLE";
}

function execKind(status: GatewayExecStatus): TriggerEvent["kind"] {
  if (status === "FILLED") return "filled";
  if (status === "REVERTED") return "reverted";
  return "submitted";
}

function buildExecEvent(exec: GatewayExecStatusEntry): TriggerEvent {
  const realizedPrice =
    exec.realized_price_micros != null
      ? Number((exec.realized_price_micros / 1_000_000).toFixed(6))
      : undefined;
  return {
    id: `exec:${exec.tx_hash}:${exec.status}`,
    at: execEventTs(exec),
    market: exec.market_id,
    kind: execKind(exec.status),
    txHash: exec.tx_hash,
    realizedPrice,
    reason: exec.reason,
  };
}

function compareSignals(a: GatewaySignal, b: GatewaySignal): number {
  const priorityScore = (priority: GatewayPriority): number => {
    if (priority === "URGENT") return 4;
    if (priority === "HIGH") return 3;
    if (priority === "NORMAL") return 2;
    if (priority === "LOW") return 1;
    return 0;
  };

  return (
    priorityScore(b.priority) - priorityScore(a.priority) ||
    Math.abs(b.edge) - Math.abs(a.edge) ||
    b.issued_at_unix_ms - a.issued_at_unix_ms
  );
}

function compareMarkets(
  a: WatchlistRow,
  b: WatchlistRow,
): number {
  const execRank = (value?: WatchlistRow["executionStatus"]): number => {
    if (value === "SUBMITTED") return 0;
    if (value === "FILLED") return 1;
    if (value === "REVERTED") return 2;
    return 3;
  };

  const triggerRank = (value: TriggerState): number => {
    if (value === "FIRED") return 0;
    if (value === "ARMED") return 1;
    if (value === "COOLDOWN") return 2;
    return 3;
  };

  return (
    execRank(a.executionStatus) - execRank(b.executionStatus) ||
    triggerRank(a.trigger) - triggerRank(b.trigger) ||
    Math.abs(b.edge) - Math.abs(a.edge) ||
    a.market.localeCompare(b.market)
  );
}

export function createGatewayProjection(): GatewayProjection {
  const state = emptyGatewayState();
  const execEvents: TriggerEvent[] = [];
  const seenEventIds = new Set<string>();

  function appendExecTransitions(
    previous: Record<string, GatewayExecStatusEntry>,
    next: Record<string, GatewayExecStatusEntry>,
  ): ExecTransition[] {
    const transitions: ExecTransition[] = [];

    for (const [txHash, entry] of Object.entries(next)) {
      const prev = previous[txHash];
      if (prev?.status === entry.status) continue;

      const event = buildExecEvent(entry);
      if (!seenEventIds.has(event.id)) {
        seenEventIds.add(event.id);
        execEvents.unshift(event);
        if (execEvents.length > MAX_EVENTS) {
          execEvents.length = MAX_EVENTS;
        }
      }

      transitions.push({
        txHash,
        marketId: entry.market_id,
        status: entry.status,
      });
    }

    return transitions;
  }

  function project(): CommandCenterSnapshot {
    const sortedSignals = Object.values(state.signals).sort(compareSignals);
    const signals: Record<string, CommandCenterSignal> = {};

    for (const signal of sortedSignals) {
      const watch = state.watchlist[signal.market_id];
      signals[signal.signal_id] = {
        id: signal.signal_id,
        market: signal.market_id,
        question: signal.market_question || signal.market_id,
        priority: mapPriority(signal.priority),
        edge: signal.edge,
        confidence: signalConfidence(signal),
        positionSuggested: Number(
          (signal.suggested_position_micros / 1_000_000).toFixed(2),
        ),
        liquidityUsd: toLiquidityUsd(
          watch?.max_position_usd_micros ?? signal.suggested_position_micros,
        ),
        expiresAt: signal.expires_at_unix_ms,
        rationale: signal.llm_rationale,
        evidence: signal.evidence_urls,
        localDecision: mapDecision(signal.decision?.action),
      };
    }

    const latestTriggerByMarket = new Map<string, GatewayTrigger>();
    for (const trigger of Object.values(state.triggers)) {
      const current = latestTriggerByMarket.get(trigger.market_id);
      if (!current || trigger.observed_at_unix_ms >= current.observed_at_unix_ms) {
        latestTriggerByMarket.set(trigger.market_id, trigger);
      }
    }

    const latestExecByMarket = new Map<string, GatewayExecStatusEntry>();
    for (const exec of Object.values(state.exec_status)) {
      const current = latestExecByMarket.get(exec.market_id);
      if (!current || relevantExecTs(exec) >= relevantExecTs(current)) {
        latestExecByMarket.set(exec.market_id, exec);
      }
    }

    const marketIds = new Set<string>();
    Object.keys(state.watchlist).forEach((marketId) => marketIds.add(marketId));
    Object.values(state.signals).forEach((signal) => marketIds.add(signal.market_id));
    Object.values(state.triggers).forEach((trigger) => marketIds.add(trigger.market_id));
    Object.values(state.exec_status).forEach((exec) => marketIds.add(exec.market_id));

    const watchRows: WatchlistRow[] = [];
    for (const marketId of marketIds) {
      const watch = state.watchlist[marketId];
      const trigger = latestTriggerByMarket.get(marketId);
      const exec = latestExecByMarket.get(marketId);
      const signal =
        sortedSignals.find((item) => item.market_id === marketId) ?? undefined;
      const confidence = clamp01(
        watch?.confidence ?? (signal ? signalConfidence(signal) : 0),
      );

      watchRows.push({
        market: marketId,
        question: signal?.market_question || marketId,
        side:
          watch?.side_hint === "NO" || signal?.side_hint === "NO" ? "NO" : "YES",
        edge: watch?.edge ?? signal?.edge ?? 0,
        liquidityUsd: toLiquidityUsd(
          watch?.max_position_usd_micros ??
            signal?.suggested_position_micros ??
            0,
        ),
        depthPct: confidence,
        slippageBps: watch?.max_slippage_bps ?? 0,
        trigger: toTriggerState(trigger?.state),
        priceSeries: priceSeries(signal),
        executionStatus: exec?.status,
      });
    }

    watchRows.sort(compareMarkets);

    const watchlist: Record<string, WatchlistRow> = {};
    for (const row of watchRows) {
      watchlist[row.market] = row;
    }

    return {
      signals,
      signalOrder: sortedSignals.map((signal) => signal.signal_id),
      watchlist,
      watchlistOrder: watchRows.map((row) => row.market),
      triggers: [...execEvents],
      health: {
        killSwitchEnabled: state.kill.engaged,
      },
    };
  }

  return {
    applySnapshot(data) {
      const previousExec = structuredClone(state.exec_status);

      if (data.signals) state.signals = data.signals;
      if (data.watchlist) state.watchlist = data.watchlist;
      if (data.triggers) state.triggers = data.triggers;
      if (data.exec_status) state.exec_status = data.exec_status;
      if (data.kill) state.kill = { ...state.kill, ...data.kill };
      if (typeof data.server_version === "number") {
        state.server_version = data.server_version;
      }

      const execTransitions = appendExecTransitions(previousExec, state.exec_status);
      return { snapshot: project(), execTransitions };
    },

    applyPatch(topic, patch) {
      const previousExec = structuredClone(state.exec_status);
      const nextTopic = applyPatch(
        structuredClone(state[topic]),
        patch as Operation[],
        false,
        true,
      ).newDocument;

      if (topic === "signals") {
        state.signals = nextTopic as Record<string, GatewaySignal>;
      } else if (topic === "watchlist") {
        state.watchlist = nextTopic as Record<string, GatewayWatchlistEntry>;
      } else if (topic === "triggers") {
        state.triggers = nextTopic as Record<string, GatewayTrigger>;
      } else {
        state.exec_status = nextTopic as Record<string, GatewayExecStatusEntry>;
      }

      const execTransitions =
        topic === "exec_status"
          ? appendExecTransitions(previousExec, state.exec_status)
          : [];

      return { snapshot: project(), execTransitions };
    },
  };
}
