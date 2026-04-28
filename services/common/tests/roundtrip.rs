//! Proto roundtrip contract tests — single-language (Rust).
//!
//! For the cross-language (TS ↔ Rust) test see `tests/cross_lang.rs`
//! which is gated on a live Redis instance.

use dual_loop_common::proto::common::{MarketId, Money, Side, Uuid};
use dual_loop_common::proto::control::{Heartbeat, KillAck, KillAckStatus, KillCommand, KillScope};
use dual_loop_common::proto::execution::{ExecStatus, ExecSubmitted, ExecutorState};
use dual_loop_common::proto::signal::{DecisionAction, Signal, SignalDecision, SignalPriority};
use dual_loop_common::proto::trigger::Trigger;
use dual_loop_common::proto::watchlist::{
    TriggerKind, TriggerPolicy, WatchlistRemove, WatchlistUpsert,
};
use prost::Message;

fn assert_roundtrip<M>(msg: M)
where
    M: Message + Default + PartialEq + std::fmt::Debug,
{
    let mut buf = Vec::new();
    msg.encode(&mut buf).expect("encode");
    let back = M::decode(&buf[..]).expect("decode");
    assert_eq!(back, msg);
}

#[test]
fn watchlist_upsert_roundtrip() {
    let msg = WatchlistUpsert {
        message_id: Some(Uuid {
            value: prost::bytes::Bytes::from_static(&[1u8; 16]),
        }),
        market: Some(MarketId {
            polymarket_id: "mkt-01".into(),
            ctf_address: "0xabc".into(),
        }),
        version: 42,
        edge_ppm: 50_000,
        p_fundamental_ppm: 670_000,
        confidence_ppm: 900_000,
        side_hint: Side::Yes as i32,
        max_position_usd: Some(Money {
            amount_micros: 10_000_000,
        }),
        max_slippage_bps: 75,
        ttl_ms: 60_000,
        issued_at_unix_ms: 1_700_000_000_000,
        trigger_policy: Some(TriggerPolicy {
            min_independent_sources: 2,
            allowed_kinds: vec![
                TriggerKind::OracleResolution as i32,
                TriggerKind::LargeFill as i32,
            ],
            max_price_impact_bps: 100,
        }),
    };
    assert_roundtrip(msg);
}

#[test]
fn watchlist_remove_roundtrip() {
    let msg = WatchlistRemove {
        message_id: None,
        market: Some(MarketId {
            polymarket_id: "mkt-02".into(),
            ctf_address: "0xdef".into(),
        }),
        version: 7,
        reason: 1,
        issued_at_unix_ms: 1_700_000_001_000,
    };
    assert_roundtrip(msg);
}

#[test]
fn signal_and_decision_roundtrip() {
    let signal = Signal {
        message_id: None,
        signal_id: "sig-42".into(),
        server_version: 3,
        market: Some(MarketId {
            polymarket_id: "m".into(),
            ctf_address: "a".into(),
        }),
        market_question: "Will X happen?".into(),
        edge_ppm: 40_000,
        p_fundamental_ppm: 600_000,
        p_market_ppm: 560_000,
        confidence_low_ppm: 550_000,
        confidence_high_ppm: 650_000,
        expected_value_bps: 40,
        side_hint: Side::Yes as i32,
        suggested_position: Some(Money {
            amount_micros: 500_000,
        }),
        expires_at_unix_ms: 1_700_000_500_000,
        llm_rationale: "…".into(),
        evidence_urls: vec!["https://example.com".into()],
        issued_at_unix_ms: 1_700_000_000_000,
        priority: SignalPriority::Normal as i32,
    };
    assert_roundtrip(signal);

    let decision = SignalDecision {
        message_id: None,
        signal_id: "sig-42".into(),
        expected_version: 3,
        idempotency_key: "hash".into(),
        action: DecisionAction::Accept as i32,
        operator_id: "op-1".into(),
        rejection_reason: String::new(),
        snooze_until_unix_ms: 0,
        decided_at_unix_ms: 1_700_000_010_000,
    };
    assert_roundtrip(decision);
}

#[test]
fn trigger_roundtrip() {
    let trigger = Trigger {
        message_id: None,
        market: Some(MarketId {
            polymarket_id: "m".into(),
            ctf_address: "a".into(),
        }),
        kind: TriggerKind::LargeFill as i32,
        evidence_hash: prost::bytes::Bytes::from_static(&[0xAB; 32]),
        eta_block: 123456,
        observed_at_ns: 42,
        expected_price: Some(Money {
            amount_micros: 550_000,
        }),
        slippage_bps: 10,
        source_count: 2,
    };
    assert_roundtrip(trigger);
}

#[test]
fn exec_submitted_roundtrip() {
    let msg = ExecSubmitted {
        message_id: None,
        trigger_id: None,
        market: None,
        tx_hash: prost::bytes::Bytes::from_static(&[0u8; 32]),
        nonce: 1,
        size: Some(Money { amount_micros: 100 }),
        side: Side::No as i32,
        max_fee_per_gas_wei: 100,
        max_priority_fee_per_gas_wei: 1,
        relay_used: "public".into(),
        submitted_at_unix_ms: 1_700_000_020_000,
        paper_mode: true,
    };
    assert_roundtrip(msg);
}

#[test]
fn exec_status_roundtrip() {
    let msg = ExecStatus {
        heartbeat_seq: 17,
        emitted_at_unix_ms: 1_700_000_030_000,
        state: ExecutorState::Idle as i32,
        current_market: None,
        triggers_seen_total: 42,
        triggers_executed_total: 0,
        kill_engaged: false,
        paper_mode: true,
    };
    assert_roundtrip(msg);
}

#[test]
fn kill_command_and_ack_roundtrip() {
    let cmd = KillCommand {
        message_id: None,
        operator_id: "op-1".into(),
        scope: KillScope::NewTriggers as i32,
        market: None,
        nonce: 42,
        issued_at_unix_ms: 1,
        signature: prost::bytes::Bytes::from_static(&[0xCC; 32]),
    };
    assert_roundtrip(cmd);

    let ack = KillAck {
        message_id: None,
        original_command_id: None,
        scope: KillScope::NewTriggers as i32,
        status: KillAckStatus::Accepted as i32,
        detail: "ok".into(),
        active_triggers_cancelled: 0,
        pending_tx_cancelled: 0,
        ack_at_unix_ms: 1_700_000_040_000,
    };
    assert_roundtrip(ack);
}

#[test]
fn heartbeat_roundtrip() {
    let msg = Heartbeat {
        message_id: None,
        operator_id: "op-1".into(),
        heartbeat_at_unix_ms: 1_700_000_050_000,
    };
    assert_roundtrip(msg);
}
