// Canonical Redis Stream names (see task spec Phase 1 Track B §4).

export const STREAM_SIGNALS_EMITTED = "dl.v1.signals.emitted";
export const STREAM_SIGNALS_DECISIONS = "dl.v1.signals.decisions";
export const STREAM_WATCHLIST_UPSERT = "dl.v1.watchlist.upsert";
export const STREAM_WATCHLIST_REMOVE = "dl.v1.watchlist.remove";
export const STREAM_TRIGGERS_FIRED = "dl.v1.triggers.fired";
export const STREAM_EXEC_SUBMITTED = "dl.v1.exec.submitted";
export const STREAM_EXEC_FILLED = "dl.v1.exec.filled";
export const STREAM_EXEC_REVERTED = "dl.v1.exec.reverted";
export const STREAM_CONTROL_KILL = "dl.v1.control.kill";
export const STREAM_CONTROL_KILL_ACK = "dl.v1.control.kill_ack";

export const INBOUND_STREAMS = [
  STREAM_SIGNALS_EMITTED,
  STREAM_WATCHLIST_UPSERT,
  STREAM_WATCHLIST_REMOVE,
  STREAM_TRIGGERS_FIRED,
  STREAM_EXEC_SUBMITTED,
  STREAM_EXEC_FILLED,
  STREAM_EXEC_REVERTED,
  STREAM_CONTROL_KILL_ACK,
] as const;

export const CONSUMER_GROUP = "gateway";
