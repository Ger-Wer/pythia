import { parse as uuidParse, stringify as uuidStringify } from "uuid";
import type { Logger } from "../logger.ts";
import {
  STREAM_CONTROL_KILL,
  STREAM_CONTROL_KILL_ACK,
  STREAM_EXEC_FILLED,
  STREAM_EXEC_REVERTED,
  STREAM_EXEC_SUBMITTED,
  STREAM_SIGNALS_DECISIONS,
  STREAM_SIGNALS_EMITTED,
  STREAM_TRIGGERS_FIRED,
  STREAM_WATCHLIST_REMOVE,
  STREAM_WATCHLIST_UPSERT,
} from "./topics.ts";

type ProtoCodec<T> = {
  encode?(input: T): { finish(): Uint8Array };
  decode(input: Uint8Array): T;
};

interface ProtoBundle {
  signal?: {
    Signal: ProtoCodec<Record<string, unknown>>;
    SignalDecision: ProtoCodec<Record<string, unknown>>;
  };
  watchlist?: {
    WatchlistUpsert: ProtoCodec<Record<string, unknown>>;
    WatchlistRemove: ProtoCodec<Record<string, unknown>>;
  };
  trigger?: { Trigger: ProtoCodec<Record<string, unknown>> };
  execution?: {
    ExecSubmitted: ProtoCodec<Record<string, unknown>>;
    ExecFilled: ProtoCodec<Record<string, unknown>>;
    ExecReverted: ProtoCodec<Record<string, unknown>>;
  };
  control?: {
    KillAck: ProtoCodec<Record<string, unknown>>;
    KillCommand: ProtoCodec<Record<string, unknown>>;
  };
}

const PPM_SCALE = 1_000_000;
const DYNAMIC_IMPORT = new Function(
  "path",
  "return import(path);",
) as (path: string) => Promise<Record<string, unknown>>;
const warned = new Set<string>();
let protoBundlePromise: Promise<ProtoBundle | null> | null = null;

function warnOnce(log: Logger, key: string, msg: string, extra?: Record<string, unknown>) {
  if (warned.has(key)) return;
  warned.add(key);
  log.warn(msg, extra);
}

async function loadModule(path: string): Promise<Record<string, unknown> | null> {
  try {
    return await DYNAMIC_IMPORT(path);
  } catch {
    return null;
  }
}

async function loadProtoBundle(log: Logger): Promise<ProtoBundle | null> {
  if (!protoBundlePromise) {
    protoBundlePromise = (async () => {
      const [signal, watchlist, trigger, execution, control] = await Promise.all([
        loadModule("../../../src/generated/dual_loop/v1/signal.ts"),
        loadModule("../../../src/generated/dual_loop/v1/watchlist.ts"),
        loadModule("../../../src/generated/dual_loop/v1/trigger.ts"),
        loadModule("../../../src/generated/dual_loop/v1/execution.ts"),
        loadModule("../../../src/generated/dual_loop/v1/control.ts"),
      ]);

      const bundle: ProtoBundle = {
        signal: signal as ProtoBundle["signal"] | undefined,
        watchlist: watchlist as ProtoBundle["watchlist"] | undefined,
        trigger: trigger as ProtoBundle["trigger"] | undefined,
        execution: execution as ProtoBundle["execution"] | undefined,
        control: control as ProtoBundle["control"] | undefined,
      };

      if (
        !bundle.signal &&
        !bundle.watchlist &&
        !bundle.trigger &&
        !bundle.execution &&
        !bundle.control
      ) {
        return null;
      }
      return bundle;
    })();
  }

  const bundle = await protoBundlePromise;
  if (!bundle) {
    warnOnce(
      log,
      "proto-modules-missing",
      "protobuf codecs unavailable; gateway bridge is running JSON-only",
      { hint: "Run root bun install + bun run proto:gen before live dual-loop use." },
    );
  }
  return bundle;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : fallback;
  if (typeof value === "bigint") return Number(value);
  if (typeof value === "string" && value.length > 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

function ppmToUnit(value: unknown): number {
  return asNumber(value) / PPM_SCALE;
}

function moneyMicros(value: unknown): number {
  if (!value || typeof value !== "object") return 0;
  const amount = (value as { amountMicros?: unknown }).amountMicros;
  return asNumber(amount);
}

function bytesToHex(value: unknown): string {
  if (!value) return "";
  if (value instanceof Uint8Array) {
    return `0x${Buffer.from(value).toString("hex")}`;
  }
  if (Array.isArray(value)) {
    return `0x${Buffer.from(value as number[]).toString("hex")}`;
  }
  if (typeof value === "string") return value;
  return "";
}

function uuidBytes(value: unknown): Uint8Array | null {
  if (!value || typeof value !== "object") return null;
  const raw = (value as { value?: unknown }).value;
  if (raw instanceof Uint8Array) return raw;
  if (Array.isArray(raw)) return Uint8Array.from(raw as number[]);
  return null;
}

function uuidToString(value: unknown): string {
  const raw = uuidBytes(value);
  if (!raw) return "";
  if (raw.length === 16) {
    try {
      return uuidStringify(raw);
    } catch {
      // Fall through to hex for malformed/non-RFC payloads.
    }
  }
  return bytesToHex(raw);
}

function uuidFromString(value: string): { value: Uint8Array } | undefined {
  if (!value) return undefined;
  try {
    return { value: uuidParse(value) };
  } catch {
    return undefined;
  }
}

function marketId(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  return String((value as { polymarketId?: unknown }).polymarketId ?? "");
}

function ctfAddress(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  return String((value as { ctfAddress?: unknown }).ctfAddress ?? "");
}

function sideName(value: unknown): string {
  switch (asNumber(value)) {
    case 1:
      return "YES";
    case 2:
      return "NO";
    default:
      return "UNSPECIFIED";
  }
}

function priorityName(value: unknown): string {
  switch (asNumber(value)) {
    case 1:
      return "LOW";
    case 2:
      return "NORMAL";
    case 3:
      return "HIGH";
    case 4:
      return "URGENT";
    default:
      return "UNSPECIFIED";
  }
}

function triggerKindName(value: unknown): string {
  switch (asNumber(value)) {
    case 1:
      return "ORACLE_RESOLUTION";
    case 2:
      return "LARGE_FILL";
    case 3:
      return "ADMIN_ACTION";
    case 4:
      return "NEWS_CONFIRMED";
    default:
      return "UNSPECIFIED";
  }
}

function killAckStatusName(value: unknown): string {
  switch (asNumber(value)) {
    case 1:
      return "ACCEPTED";
    case 2:
      return "DRAINING";
    case 3:
      return "COMPLETE";
    case 4:
      return "REJECTED_BAD_SIGNATURE";
    case 5:
      return "REJECTED_STALE";
    case 6:
      return "REJECTED_REPLAY";
    default:
      return "ACCEPTED";
  }
}

function decisionActionValue(value: unknown): number {
  switch (String(value ?? "").toUpperCase()) {
    case "ACCEPT":
      return 1;
    case "REJECT":
      return 2;
    case "SNOOZE":
      return 3;
    default:
      return 0;
  }
}

function killScopeValue(value: unknown): number {
  switch (String(value ?? "").toUpperCase()) {
    case "GLOBAL":
      return 1;
    case "MARKET":
      return 2;
    case "NEW_TRIGGERS":
      return 3;
    default:
      return 0;
  }
}

function hexToBytes(value: unknown): Uint8Array {
  if (typeof value !== "string") return new Uint8Array();
  const trimmed = value.trim().replace(/^0x/i, "");
  if (trimmed.length === 0 || trimmed.length % 2 !== 0) return new Uint8Array();
  try {
    return Uint8Array.from(Buffer.from(trimmed, "hex"));
  } catch {
    return new Uint8Array();
  }
}

export async function decodeProtoPayload(
  stream: string,
  payloadBytes: Uint8Array,
  log: Logger,
): Promise<Record<string, unknown> | null> {
  const bundle = await loadProtoBundle(log);
  if (!bundle) return null;

  try {
    switch (stream) {
      case STREAM_SIGNALS_EMITTED: {
        const codec = bundle.signal?.Signal;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          signal_id: String(msg.signalId ?? ""),
          server_version: asNumber(msg.serverVersion, 1),
          market_id: marketId(msg.market),
          ctf_address: ctfAddress(msg.market),
          market_question: String(msg.marketQuestion ?? ""),
          edge: ppmToUnit(msg.edgePpm),
          p_fundamental: ppmToUnit(msg.pFundamentalPpm),
          p_market: ppmToUnit(msg.pMarketPpm),
          confidence_low: ppmToUnit(msg.confidenceLowPpm),
          confidence_high: ppmToUnit(msg.confidenceHighPpm),
          expected_value_bps: asNumber(msg.expectedValueBps),
          side_hint: sideName(msg.sideHint),
          suggested_position_micros: moneyMicros(msg.suggestedPosition),
          expires_at_unix_ms: asNumber(msg.expiresAtUnixMs),
          llm_rationale: String(msg.llmRationale ?? ""),
          evidence_urls: Array.isArray(msg.evidenceUrls) ? msg.evidenceUrls : [],
          issued_at_unix_ms: asNumber(msg.issuedAtUnixMs),
          priority: priorityName(msg.priority),
        };
      }

      case STREAM_WATCHLIST_UPSERT: {
        const codec = bundle.watchlist?.WatchlistUpsert;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          market_id: marketId(msg.market),
          ctf_address: ctfAddress(msg.market),
          version: asNumber(msg.version),
          edge: ppmToUnit(msg.edgePpm),
          p_fundamental: ppmToUnit(msg.pFundamentalPpm),
          confidence: ppmToUnit(msg.confidencePpm),
          side_hint: sideName(msg.sideHint),
          max_position_usd_micros: moneyMicros(msg.maxPositionUsd),
          max_slippage_bps: asNumber(msg.maxSlippageBps),
          ttl_ms: asNumber(msg.ttlMs),
          issued_at_unix_ms: asNumber(msg.issuedAtUnixMs),
        };
      }

      case STREAM_WATCHLIST_REMOVE: {
        const codec = bundle.watchlist?.WatchlistRemove;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          market_id: marketId(msg.market),
          version: asNumber(msg.version),
          issued_at_unix_ms: asNumber(msg.issuedAtUnixMs),
        };
      }

      case STREAM_TRIGGERS_FIRED: {
        const codec = bundle.trigger?.Trigger;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          trigger_id: uuidToString(msg.messageId),
          market_id: marketId(msg.market),
          kind: triggerKindName(msg.kind),
          eta_block: asNumber(msg.etaBlock),
          // `observed_at_ns` is monotonic; use the wall-clock trigger issue time for UI projection.
          observed_at_unix_ms: asNumber(msg.issuedAtUnixMs) || Date.now(),
          expected_price: moneyMicros(msg.expectedPrice) / PPM_SCALE,
          slippage_bps: asNumber(msg.slippageBps),
          source_count: asNumber(msg.sourceCount),
          issued_at_unix_ms: asNumber(msg.issuedAtUnixMs),
          watchlist_valid_until_unix_ms: asNumber(msg.watchlistValidUntilUnixMs),
        };
      }

      case STREAM_EXEC_SUBMITTED: {
        const codec = bundle.execution?.ExecSubmitted;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          tx_hash: bytesToHex(msg.txHash),
          market_id: marketId(msg.market),
          submitted_at_unix_ms: asNumber(msg.submittedAtUnixMs),
          paper_mode: Boolean(msg.paperMode),
        };
      }

      case STREAM_EXEC_FILLED: {
        const codec = bundle.execution?.ExecFilled;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          tx_hash: bytesToHex(msg.txHash),
          market_id: marketId(msg.market),
          realized_price_micros: moneyMicros(msg.realizedPrice),
          filled_size_micros: moneyMicros(msg.filledSize),
          gas_spent_usd_micros: moneyMicros(msg.gasSpentUsd),
          block_number: asNumber(msg.blockNumber),
          filled_at_unix_ms: asNumber(msg.filledAtUnixMs),
          paper_mode: Boolean(msg.paperMode),
        };
      }

      case STREAM_EXEC_REVERTED: {
        const codec = bundle.execution?.ExecReverted;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          tx_hash: bytesToHex(msg.txHash),
          market_id: marketId(msg.market),
          reason: String(msg.reason ?? ""),
          gas_spent_usd_micros: moneyMicros(msg.gasSpentUsd),
          block_number: asNumber(msg.blockNumber),
          reverted_at_unix_ms: asNumber(msg.revertedAtUnixMs),
          paper_mode: Boolean(msg.paperMode),
        };
      }

      case STREAM_CONTROL_KILL_ACK: {
        const codec = bundle.control?.KillAck;
        if (!codec) return null;
        const msg = codec.decode(payloadBytes);
        return {
          original_command_id: uuidToString(msg.originalCommandId),
          status: killAckStatusName(msg.status),
          detail: String(msg.detail ?? ""),
          active_triggers_cancelled: asNumber(msg.activeTriggersCancelled),
          pending_tx_cancelled: asNumber(msg.pendingTxCancelled),
          ack_at_unix_ms: asNumber(msg.ackAtUnixMs),
        };
      }

      default:
        return null;
    }
  } catch (error) {
    warnOnce(log, `proto-decode-failed:${stream}`, "protobuf decode failed; keeping JSON fallback only", {
      stream,
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}

export async function encodeProtoPublishPayload(
  stream: string,
  payload: Record<string, unknown>,
  log: Logger,
): Promise<Uint8Array | null> {
  const bundle = await loadProtoBundle(log);
  if (!bundle) return null;

  try {
    switch (stream) {
      case STREAM_SIGNALS_DECISIONS: {
        const codec = bundle.signal?.SignalDecision;
        if (!codec?.encode) return null;
        return codec
          .encode({
            messageId: uuidFromString(String(payload.message_id ?? "")),
            signalId: String(payload.signal_id ?? ""),
            expectedVersion: BigInt(asNumber(payload.expected_version)),
            idempotencyKey: String(payload.idempotency_key ?? ""),
            action: decisionActionValue(payload.action),
            operatorId: String(payload.operator_id ?? ""),
            rejectionReason: String(payload.rejection_reason ?? ""),
            snoozeUntilUnixMs: BigInt(asNumber(payload.snooze_until_unix_ms)),
            decidedAtUnixMs: BigInt(asNumber(payload.decided_at_unix_ms)),
          })
          .finish();
      }

      case STREAM_CONTROL_KILL: {
        const codec = bundle.control?.KillCommand;
        if (!codec?.encode) return null;
        const marketId = String(payload.market_id ?? "");
        return codec
          .encode({
            messageId: uuidFromString(String(payload.message_id ?? "")),
            operatorId: String(payload.operator_id ?? ""),
            scope: killScopeValue(payload.scope),
            market: marketId
              ? {
                  polymarketId: marketId,
                  ctfAddress: "",
                  marketSpecificKey: "",
                }
              : undefined,
            nonce: BigInt(asNumber(payload.nonce)),
            issuedAtUnixMs: BigInt(asNumber(payload.issued_at_unix_ms)),
            signature: hexToBytes(payload.signature),
          })
          .finish();
      }

      default:
        return null;
    }
  } catch (error) {
    warnOnce(log, `proto-encode-failed:${stream}`, "protobuf encode failed; keeping JSON publish fallback", {
      stream,
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}
