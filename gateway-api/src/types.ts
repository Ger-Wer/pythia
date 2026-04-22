// Domain types for gateway state exposed to the UI.
//
// Gateway maintains an in-memory projection of bus events. The shape is
// intentionally UI-friendly (JSON, readable field names) and stable — UI
// diffs via JSON Patch (RFC 6902) against these structures.
//
// See docs/dual_loop/05-command-center-ui.md for UI contract.
// See docs/dual_loop/04-bus-protocol.md for wire messages.

export type Topic = "signals" | "watchlist" | "triggers" | "exec_status";

export const ALL_TOPICS: Topic[] = [
  "signals",
  "watchlist",
  "triggers",
  "exec_status",
];

export type DecisionAction = "ACCEPT" | "REJECT" | "SNOOZE";
export type KillScope = "GLOBAL" | "MARKET" | "NEW_TRIGGERS";
export type Side = "YES" | "NO" | "UNSPECIFIED";
export type TriggerState = "IDLE" | "ARMED" | "FIRED" | "COOLDOWN";

export interface Signal {
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
  side_hint: Side;
  suggested_position_micros: number;
  expires_at_unix_ms: number;
  llm_rationale: string;
  evidence_urls: string[];
  issued_at_unix_ms: number;
  priority: "LOW" | "NORMAL" | "HIGH" | "URGENT" | "UNSPECIFIED";
  // Decision state (filled by gateway once operator acts).
  decision?: {
    action: DecisionAction;
    operator_id: string;
    reason?: string;
    snooze_until_unix_ms?: number;
    decided_at_unix_ms: number;
  };
}

export interface WatchlistEntry {
  market_id: string;
  ctf_address: string;
  version: number;
  edge: number;
  p_fundamental: number;
  confidence: number;
  side_hint: Side;
  max_position_usd_micros: number;
  max_slippage_bps: number;
  ttl_ms: number;
  issued_at_unix_ms: number;
}

export interface Trigger {
  trigger_id: string;
  market_id: string;
  kind:
    | "ORACLE_RESOLUTION"
    | "LARGE_FILL"
    | "ADMIN_ACTION"
    | "NEWS_CONFIRMED"
    | "UNSPECIFIED";
  state: TriggerState;
  eta_block: number;
  observed_at_unix_ms: number;
  expected_price: number;
  slippage_bps: number;
  source_count: number;
}

export interface ExecStatus {
  tx_hash: string;
  market_id: string;
  status: "SUBMITTED" | "FILLED" | "REVERTED";
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

export interface KillState {
  engaged: boolean;
  scope?: KillScope;
  last_command_id?: string;
  last_ack_status?:
    | "ACCEPTED"
    | "DRAINING"
    | "COMPLETE"
    | "REJECTED_BAD_SIGNATURE"
    | "REJECTED_STALE"
    | "REJECTED_REPLAY";
  last_ack_at_unix_ms?: number;
}

export interface GatewayState {
  // server_version is a monotonic counter incremented on every state mutation.
  // Used by the UI for conflict detection and by internal JSON-Patch batching.
  server_version: number;
  signals: Record<string, Signal>;
  watchlist: Record<string, WatchlistEntry>;
  triggers: Record<string, Trigger>;
  exec_status: Record<string, ExecStatus>;
  kill: KillState;
}

export function emptyState(): GatewayState {
  return {
    server_version: 0,
    signals: {},
    watchlist: {},
    triggers: {},
    exec_status: {},
    kill: { engaged: false },
  };
}

// Wire messages exchanged over the WSS /ws/v1 endpoint.
//
// Contract reference: docs/dual_loop/05-command-center-ui.md "WebSocket-протокол"
// and the task spec (Track B, section 3).

export type WsServerMessage =
  | {
      kind: "snapshot";
      server_version: number;
      data: Partial<GatewayState>;
      topics: Topic[];
    }
  | {
      kind: "patch";
      server_version: number;
      topic: Topic;
      patch: ReadonlyArray<JsonPatchOp>;
    }
  | { kind: "ping"; ts: number }
  | { kind: "error"; code: string; message: string };

export type WsClientMessage =
  | { kind: "subscribe"; topics: Topic[] }
  | { kind: "unsubscribe"; topics: Topic[] }
  | { kind: "pong"; ts: number };

// Minimal subset of RFC 6902 operations we emit.
export interface JsonPatchOp {
  op: "add" | "remove" | "replace";
  path: string;
  value?: unknown;
}
