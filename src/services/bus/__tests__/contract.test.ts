/**
 * Bus contract tests (TypeScript side).
 *
 * Covers:
 *   1. Roundtrip encode→decode for every dual_loop.v1 message.
 *   2. Byte-for-byte topic parity with the Rust source of truth.
 *
 * The cross-language (TS publishes, Rust consumes) flow lives in a separate
 * integration test gated on a live Redis — see `services/common/tests/cross_lang.rs`
 * and `scripts/bus_cross_lang_test.sh`.
 */

import { describe, expect, it } from 'bun:test';

import { Side } from '../../../generated/dual_loop/v1/common';
import {
  DecisionAction,
  Signal,
  SignalDecision,
  SignalPriority,
} from '../../../generated/dual_loop/v1/signal';
import {
  KillAck,
  KillAckStatus,
  KillCommand,
  KillScope,
} from '../../../generated/dual_loop/v1/control';
import {
  ExecStatus,
  ExecSubmitted,
  ExecutorState,
} from '../../../generated/dual_loop/v1/execution';
import { Trigger } from '../../../generated/dual_loop/v1/trigger';
import {
  TriggerKind,
  WatchlistRemove,
  WatchlistUpsert,
} from '../../../generated/dual_loop/v1/watchlist';

import * as topics from '../topics';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

function roundtrip<T>(
  codec: { encode(m: T): { finish: () => Uint8Array }; decode(b: Uint8Array): T },
  message: T,
): T {
  const bytes = codec.encode(message).finish();
  return codec.decode(bytes);
}

describe('proto roundtrip', () => {
  it('WatchlistUpsert', () => {
    const msg: WatchlistUpsert = {
      messageId: { value: Buffer.alloc(16, 1) },
      market: { polymarketId: 'mkt-01', ctfAddress: '0xabc' },
      version: 42n,
      edgePpm: 50_000n,
      pFundamentalPpm: 670_000n,
      confidencePpm: 900_000n,
      sideHint: Side.SIDE_YES,
      maxPositionUsd: { amountMicros: 10_000_000n },
      maxSlippageBps: 75,
      ttlMs: 60_000n,
      issuedAtUnixMs: 1_700_000_000_000n,
      triggerPolicy: {
        minIndependentSources: 2,
        allowedKinds: [TriggerKind.TRIGGER_KIND_ORACLE_RESOLUTION, TriggerKind.TRIGGER_KIND_LARGE_FILL],
        maxPriceImpactBps: 100,
      },
    };
    const back = roundtrip(WatchlistUpsert, msg);
    expect(back.market?.polymarketId).toBe('mkt-01');
    expect(back.edgePpm).toBe(50_000n);
    expect(back.version).toBe(42n);
    expect(back.maxPositionUsd?.amountMicros).toBe(10_000_000n);
    expect(back.issuedAtUnixMs).toBe(1_700_000_000_000n);
    expect(back.triggerPolicy?.allowedKinds).toEqual(msg.triggerPolicy!.allowedKinds);
  });

  it('WatchlistRemove', () => {
    const msg: WatchlistRemove = {
      messageId: undefined,
      market: { polymarketId: 'mkt-02', ctfAddress: '0xdef' },
      version: 7n,
      reason: 1,
      issuedAtUnixMs: 1_700_000_001_000n,
    };
    const back = roundtrip(WatchlistRemove, msg);
    expect(back.market?.polymarketId).toBe('mkt-02');
    expect(back.version).toBe(7n);
    expect(back.issuedAtUnixMs).toBe(1_700_000_001_000n);
  });

  it('Signal + SignalDecision', () => {
    const signal: Signal = {
      messageId: undefined,
      signalId: 'sig-42',
      serverVersion: 3n,
      market: { polymarketId: 'm', ctfAddress: 'a' },
      marketQuestion: 'Will X happen?',
      edgePpm: 40_000n,
      pFundamentalPpm: 600_000n,
      pMarketPpm: 560_000n,
      confidenceLowPpm: 550_000n,
      confidenceHighPpm: 650_000n,
      expectedValueBps: 40n,
      sideHint: Side.SIDE_YES,
      suggestedPosition: { amountMicros: 500_000n },
      expiresAtUnixMs: 1_700_000_500_000n,
      llmRationale: '...',
      evidenceUrls: ['https://example.com'],
      issuedAtUnixMs: 1_700_000_000_000n,
      priority: SignalPriority.SIGNAL_PRIORITY_NORMAL,
    };
    const back = roundtrip(Signal, signal);
    expect(back.signalId).toBe('sig-42');
    expect(back.edgePpm).toBe(40_000n);
    expect(back.expectedValueBps).toBe(40n);
    expect(back.evidenceUrls).toEqual(['https://example.com']);

    const decision: SignalDecision = {
      messageId: undefined,
      signalId: 'sig-42',
      expectedVersion: 3n,
      idempotencyKey: 'hash',
      action: DecisionAction.DECISION_ACTION_ACCEPT,
      operatorId: 'op-1',
      rejectionReason: '',
      snoozeUntilUnixMs: 0n,
      decidedAtUnixMs: 1_700_000_010_000n,
    };
    const backDecision = roundtrip(SignalDecision, decision);
    expect(backDecision.action).toBe(DecisionAction.DECISION_ACTION_ACCEPT);
    expect(backDecision.decidedAtUnixMs).toBe(1_700_000_010_000n);
  });

  it('Trigger', () => {
    const msg: Trigger = {
      messageId: undefined,
      market: { polymarketId: 'm', ctfAddress: 'a' },
      kind: TriggerKind.TRIGGER_KIND_LARGE_FILL,
      evidenceHash: Buffer.alloc(32, 0xab),
      etaBlock: 123456n,
      observedAtNs: 42n,
      expectedPrice: { amountMicros: 550_000n },
      slippageBps: 10,
      sourceCount: 2,
    };
    const back = roundtrip(Trigger, msg);
    expect(back.etaBlock).toBe(123456n);
    expect(back.expectedPrice?.amountMicros).toBe(550_000n);
    expect(back.evidenceHash.length).toBe(32);
  });

  it('ExecStatus', () => {
    const msg: ExecStatus = {
      heartbeatSeq: 17n,
      emittedAtUnixMs: 1_700_000_030_000n,
      state: ExecutorState.EXECUTOR_STATE_IDLE,
      currentMarket: undefined,
      triggersSeenTotal: 42n,
      triggersExecutedTotal: 0n,
      killEngaged: false,
      paperMode: true,
    };
    const back = roundtrip(ExecStatus, msg);
    expect(back.state).toBe(ExecutorState.EXECUTOR_STATE_IDLE);
    expect(back.heartbeatSeq).toBe(17n);
    expect(back.triggersSeenTotal).toBe(42n);
  });

  it('ExecSubmitted', () => {
    const msg: ExecSubmitted = {
      messageId: undefined,
      triggerId: undefined,
      market: undefined,
      txHash: Buffer.alloc(32),
      nonce: 1n,
      size: { amountMicros: 100n },
      side: Side.SIDE_NO,
      maxFeePerGasWei: 100n,
      maxPriorityFeePerGasWei: 1n,
      relayUsed: 'public',
      submittedAtUnixMs: 1_700_000_020_000n,
      paperMode: true,
    };
    const back = roundtrip(ExecSubmitted, msg);
    expect(back.relayUsed).toBe('public');
    expect(back.paperMode).toBe(true);
  });

  it('KillCommand + KillAck', () => {
    const cmd: KillCommand = {
      messageId: undefined,
      operatorId: 'op-1',
      scope: KillScope.KILL_SCOPE_NEW_TRIGGERS,
      market: undefined,
      nonce: 42n,
      issuedAtUnixMs: 1n,
      signature: Buffer.alloc(32, 0xcc),
    };
    const backCmd = roundtrip(KillCommand, cmd);
    expect(backCmd.nonce).toBe(42n);

    const ack: KillAck = {
      messageId: undefined,
      originalCommandId: undefined,
      scope: KillScope.KILL_SCOPE_NEW_TRIGGERS,
      status: KillAckStatus.KILL_ACK_STATUS_ACCEPTED,
      detail: 'ok',
      activeTriggersCancelled: 0,
      pendingTxCancelled: 0,
      ackAtUnixMs: 1_700_000_040_000n,
    };
    const backAck = roundtrip(KillAck, ack);
    expect(backAck.status).toBe(KillAckStatus.KILL_ACK_STATUS_ACCEPTED);
  });
});

describe('topic parity with Rust source of truth', () => {
  it('WIRE_VERSION matches', () => {
    const rustTopicsPath = resolve(
      import.meta.dir,
      '../../../../services/common/src/topics.rs',
    );
    const rust = readFileSync(rustTopicsPath, 'utf8');
    const match = rust.match(/pub const WIRE_VERSION[^=]+=\s*crate::WIRE_VERSION/);
    expect(match).not.toBeNull();
    const libMatch = readFileSync(
      resolve(import.meta.dir, '../../../../services/common/src/lib.rs'),
      'utf8',
    ).match(/pub const WIRE_VERSION:\s*&str\s*=\s*"([^"]+)"/);
    expect(libMatch).not.toBeNull();
    expect(libMatch![1]).toBe(topics.WIRE_VERSION);
  });

  it('topic list matches byte-for-byte', () => {
    const rustTopicsPath = resolve(
      import.meta.dir,
      '../../../../services/common/src/topics.rs',
    );
    const rust = readFileSync(rustTopicsPath, 'utf8');

    // Extract ALL_TOPICS literal in declaration order.
    const block = rust.match(/pub const ALL_TOPICS:[^=]*=\s*&\[([\s\S]*?)\];/);
    expect(block, 'ALL_TOPICS block found in topics.rs').not.toBeNull();
    const identifiers = Array.from(block![1].matchAll(/([A-Z_]+),/g)).map((m) => m[1]);

    // Map each identifier to its string value via individual `pub const` lines.
    const rustTopics: string[] = identifiers.map((ident) => {
      const re = new RegExp(`pub const ${ident}:\\s*&str\\s*=\\s*"([^"]+)"`);
      const m = rust.match(re);
      if (!m) throw new Error(`no string literal found for ${ident}`);
      return m[1];
    });

    expect(rustTopics).toEqual(Array.from(topics.ALL_TOPICS));
  });

  it('all topics have dl.v1. prefix', () => {
    for (const t of topics.ALL_TOPICS) {
      expect(t.startsWith('dl.v1.')).toBe(true);
    }
  });

  it('topics are unique', () => {
    const seen = new Set(topics.ALL_TOPICS);
    expect(seen.size).toBe(topics.ALL_TOPICS.length);
  });
});
