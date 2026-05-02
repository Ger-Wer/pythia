// Bus → State bridge.
//
// Consumes Redis Stream messages and folds them into the EventStore.
// On `main`, inbound stream entries may carry either raw protobuf bytes or
// legacy JSON envelopes in the `payload` field. The bridge normalizes both
// into the same UI-friendly state projection; unknown/malformed fields are
// logged and skipped.

import type { Logger } from "../logger.ts";
import type { EventStore } from "../state/store.ts";
import type { GatewayState, Side } from "../types.ts";
import type { BusClient, BusMessage, BusSubscription } from "./types.ts";
import { decodeProtoPayload } from "./proto.ts";
import {
  CONSUMER_GROUP,
  STREAM_EXEC_FILLED,
  STREAM_EXEC_REVERTED,
  STREAM_EXEC_SUBMITTED,
  STREAM_SIGNALS_EMITTED,
  STREAM_TRIGGERS_FIRED,
  STREAM_WATCHLIST_REMOVE,
  STREAM_WATCHLIST_UPSERT,
  STREAM_CONTROL_KILL_ACK,
} from "./topics.ts";

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}
function num(v: unknown, fallback = 0): number {
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.length > 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}
function side(v: unknown): Side {
  const s = str(v).toUpperCase();
  if (s === "YES" || s === "NO") return s;
  return "UNSPECIFIED";
}

async function resolvePayload(
  stream: string,
  msg: BusMessage,
  log: Logger,
): Promise<Record<string, unknown> | null> {
  if (msg.payloadBytes && msg.payloadBytes.length > 0) {
    const decoded = await decodeProtoPayload(stream, msg.payloadBytes, log);
    if (decoded) return decoded;
  }
  if (Object.keys(msg.payload).length > 0) return msg.payload;
  return null;
}

export interface BridgeHandle {
  stop(): Promise<void>;
  killAck: (commandId: string, timeoutMs: number) => Promise<KillAckResult>;
}

export interface KillAckResult {
  command_id: string;
  status: NonNullable<GatewayState["kill"]["last_ack_status"]>;
  detail?: string;
  active_triggers_cancelled?: number;
  pending_tx_cancelled?: number;
  ack_at_unix_ms: number;
}

type KillAckWaiter = (r: KillAckResult) => void;

export async function startBridge(opts: {
  bus: BusClient;
  store: EventStore;
  log: Logger;
  consumer?: string;
}): Promise<BridgeHandle> {
  const { bus, store } = opts;
  const log = opts.log.child({ component: "bridge" });
  const consumer = opts.consumer ?? "gateway-0";

  const killWaiters = new Map<string, KillAckWaiter>();

  const handlers: Record<string, (m: BusMessage) => Promise<void>> = {
    [STREAM_SIGNALS_EMITTED]: async (m) => {
      const p = await resolvePayload(STREAM_SIGNALS_EMITTED, m, log);
      if (!p) return;
      const sid = str(p.signal_id);
      if (!sid) return;
      store.apply("signals", (s) => {
        s.signals[sid] = {
          signal_id: sid,
          server_version: num(p.server_version, 1),
          market_id: str(p.market_id ?? p.polymarket_id),
          ctf_address: str(p.ctf_address),
          market_question: str(p.market_question),
          edge: num(p.edge),
          p_fundamental: num(p.p_fundamental),
          p_market: num(p.p_market),
          confidence_low: num(p.confidence_low),
          confidence_high: num(p.confidence_high),
          expected_value_bps: num(p.expected_value_bps),
          side_hint: side(p.side_hint),
          suggested_position_micros: num(p.suggested_position_micros),
          expires_at_unix_ms: num(p.expires_at_unix_ms),
          llm_rationale: str(p.llm_rationale),
          evidence_urls: Array.isArray(p.evidence_urls)
            ? (p.evidence_urls as string[])
            : [],
          issued_at_unix_ms: num(p.issued_at_unix_ms, Date.now()),
          priority:
            (str(p.priority).toUpperCase() as
              | "LOW"
              | "NORMAL"
              | "HIGH"
              | "URGENT"
              | "UNSPECIFIED") || "NORMAL",
        };
      });
    },

    [STREAM_WATCHLIST_UPSERT]: async (m) => {
      const p = await resolvePayload(STREAM_WATCHLIST_UPSERT, m, log);
      if (!p) return;
      const mid = str(p.market_id);
      if (!mid) return;
      store.apply("watchlist", (s) => {
        s.watchlist[mid] = {
          market_id: mid,
          ctf_address: str(p.ctf_address),
          version: num(p.version),
          edge: num(p.edge),
          p_fundamental: num(p.p_fundamental),
          confidence: num(p.confidence),
          side_hint: side(p.side_hint),
          max_position_usd_micros: num(p.max_position_usd_micros),
          max_slippage_bps: num(p.max_slippage_bps),
          ttl_ms: num(p.ttl_ms),
          issued_at_unix_ms: num(p.issued_at_unix_ms, Date.now()),
        };
      });
    },

    [STREAM_WATCHLIST_REMOVE]: async (m) => {
      const p = await resolvePayload(STREAM_WATCHLIST_REMOVE, m, log);
      if (!p) return;
      const mid = str(p.market_id);
      if (!mid) return;
      store.apply("watchlist", (s) => {
        delete s.watchlist[mid];
      });
    },

    [STREAM_TRIGGERS_FIRED]: async (m) => {
      const p = await resolvePayload(STREAM_TRIGGERS_FIRED, m, log);
      if (!p) return;
      const tid = str(p.trigger_id ?? p.message_id);
      if (!tid) return;
      store.apply("triggers", (s) => {
        s.triggers[tid] = {
          trigger_id: tid,
          market_id: str(p.market_id),
          kind:
            (str(p.kind).toUpperCase() as
              | "ORACLE_RESOLUTION"
              | "LARGE_FILL"
              | "ADMIN_ACTION"
              | "NEWS_CONFIRMED"
              | "UNSPECIFIED") || "UNSPECIFIED",
          state: "FIRED",
          eta_block: num(p.eta_block),
          observed_at_unix_ms: num(p.observed_at_unix_ms, Date.now()),
          expected_price: num(p.expected_price),
          slippage_bps: num(p.slippage_bps),
          source_count: num(p.source_count),
          issued_at_unix_ms: num(p.issued_at_unix_ms, num(p.observed_at_unix_ms, Date.now())),
          watchlist_valid_until_unix_ms: num(p.watchlist_valid_until_unix_ms),
        };
      });
      log.info("bridge applied trigger", {
        trigger_id: tid,
        market_id: str(p.market_id),
        kind: str(p.kind),
      });
    },

    [STREAM_EXEC_SUBMITTED]: async (m) => {
      const p = await resolvePayload(STREAM_EXEC_SUBMITTED, m, log);
      if (!p) return;
      const tx = str(p.tx_hash);
      if (!tx) return;
      store.apply("exec_status", (s) => {
        s.exec_status[tx] = {
          tx_hash: tx,
          market_id: str(p.market_id),
          status: "SUBMITTED",
          submitted_at_unix_ms: num(p.submitted_at_unix_ms, Date.now()),
          paper_mode: Boolean(p.paper_mode),
        };
      });
      log.info("bridge applied exec status", {
        tx_hash: tx,
        market_id: str(p.market_id),
        status: "SUBMITTED",
      });
    },

    [STREAM_EXEC_FILLED]: async (m) => {
      const p = await resolvePayload(STREAM_EXEC_FILLED, m, log);
      if (!p) return;
      const tx = str(p.tx_hash);
      if (!tx) return;
      store.apply("exec_status", (s) => {
        const prev = s.exec_status[tx];
        s.exec_status[tx] = {
          ...(prev ?? {
            tx_hash: tx,
            market_id: str(p.market_id),
            status: "FILLED",
            paper_mode: Boolean(p.paper_mode),
          }),
          status: "FILLED",
          realized_price_micros: num(p.realized_price_micros),
          filled_size_micros: num(p.filled_size_micros),
          gas_spent_usd_micros: num(p.gas_spent_usd_micros),
          block_number: num(p.block_number),
          filled_at_unix_ms: num(p.filled_at_unix_ms, Date.now()),
        };
      });
      log.info("bridge applied exec status", {
        tx_hash: tx,
        market_id: str(p.market_id),
        status: "FILLED",
      });
    },

    [STREAM_EXEC_REVERTED]: async (m) => {
      const p = await resolvePayload(STREAM_EXEC_REVERTED, m, log);
      if (!p) return;
      const tx = str(p.tx_hash);
      if (!tx) return;
      store.apply("exec_status", (s) => {
        const prev = s.exec_status[tx];
        s.exec_status[tx] = {
          ...(prev ?? {
            tx_hash: tx,
            market_id: str(p.market_id),
            status: "REVERTED",
            paper_mode: Boolean(p.paper_mode),
          }),
          status: "REVERTED",
          reason: str(p.reason),
          gas_spent_usd_micros: num(p.gas_spent_usd_micros),
          block_number: num(p.block_number),
          reverted_at_unix_ms: num(p.reverted_at_unix_ms, Date.now()),
        };
      });
      log.info("bridge applied exec status", {
        tx_hash: tx,
        market_id: str(p.market_id),
        status: "REVERTED",
        reason: str(p.reason),
      });
    },

    [STREAM_CONTROL_KILL_ACK]: async (m) => {
      const p = await resolvePayload(STREAM_CONTROL_KILL_ACK, m, log);
      if (!p) return;
      const cid = str(p.original_command_id);
      const status = (str(p.status).toUpperCase() ||
        "ACCEPTED") as NonNullable<GatewayState["kill"]["last_ack_status"]>;
      const result: KillAckResult = {
        command_id: cid,
        status,
        detail: str(p.detail) || undefined,
        active_triggers_cancelled: num(p.active_triggers_cancelled),
        pending_tx_cancelled: num(p.pending_tx_cancelled),
        ack_at_unix_ms: num(p.ack_at_unix_ms, Date.now()),
      };
      store.apply("exec_status", (s) => {
        s.kill = {
          engaged:
            status === "ACCEPTED" ||
            status === "DRAINING" ||
            status === "COMPLETE",
          scope: s.kill.scope,
          last_command_id: cid,
          last_ack_status: status,
          last_ack_at_unix_ms: result.ack_at_unix_ms,
        };
      });
      const waiter = killWaiters.get(cid);
      if (waiter) {
        killWaiters.delete(cid);
        waiter(result);
      }
    },
  };

  const subs: BusSubscription[] = [];
  for (const stream of Object.keys(handlers)) {
    const handler = handlers[stream];
    if (!handler) continue;
    const sub = await bus.subscribeStream(
      stream,
      CONSUMER_GROUP,
      consumer,
      handler,
    );
    subs.push(sub);
  }

  log.info("bridge started", { streams: Object.keys(handlers).length });

  return {
    async stop(): Promise<void> {
      await Promise.allSettled(subs.map((s) => s.stop()));
    },
    killAck(commandId, timeoutMs) {
      return new Promise<KillAckResult>((resolve, reject) => {
        const timer = setTimeout(() => {
          killWaiters.delete(commandId);
          reject(new Error("kill-ack timeout"));
        }, timeoutMs);
        if (typeof timer.unref === "function") timer.unref();
        killWaiters.set(commandId, (r) => {
          clearTimeout(timer);
          resolve(r);
        });
      });
    },
  };
}
