//! Integration tests for RiskGuard: full evaluation pipeline with synthetic trades.

use sniper_risk::{
    CircuitState, ExecFilled, ExecReverted, RiskError, RiskGuard, RiskLimits, Side, TradeIntent,
};

fn test_limits() -> RiskLimits {
    RiskLimits {
        per_market_cap_usd: 5_000,
        global_cap_usd: 10_000,
        max_slippage_bps: 50,
        daily_budget_usd: 20_000,
        stale_signal_threshold_ms: 60_000,
        dead_man_timeout_ms: 300_000,
        cb_consecutive_fails: 3,
        cb_failure_rate: 0.5,
        cb_window_secs: 60,
    }
}

fn make_intent(market: &str, size: i64, now: i64) -> TradeIntent {
    TradeIntent {
        market_id: market.to_string(),
        size_usd: size,
        slippage_bps: 20,
        watchlist_valid_until_unix_ms: now + 60_000,
        issued_at_unix_ms: now - 1_000,
        now_unix_ms: now,
    }
}

fn make_fill(market: &str, size: i64, price: i64, gas: i64) -> ExecFilled {
    use dual_loop_common::proto::common::{MarketId, Money};

    ExecFilled {
        message_id: None,
        tx_hash: bytes::Bytes::new(),
        market: Some(MarketId {
            polymarket_id: market.to_string(),
            ctf_address: String::new(),
            market_specific_key: String::new(),
        }),
        realized_price: Some(Money {
            amount_micros: price,
        }),
        filled_size: Some(Money {
            amount_micros: size,
        }),
        gas_spent_usd: Some(Money { amount_micros: gas }),
        block_number: 1,
        filled_at_unix_ms: 1_000_000,
        paper_mode: true,
    }
}

fn make_revert(market: &str, gas: i64, reason: &str) -> ExecReverted {
    use dual_loop_common::proto::common::{MarketId, Money};

    ExecReverted {
        message_id: None,
        tx_hash: bytes::Bytes::new(),
        market: Some(MarketId {
            polymarket_id: market.to_string(),
            ctf_address: String::new(),
            market_specific_key: String::new(),
        }),
        reason: reason.to_string(),
        gas_spent_usd: Some(Money { amount_micros: gas }),
        block_number: 0,
        reverted_at_unix_ms: 1_000_000,
        paper_mode: true,
    }
}

#[test]
fn happy_path_trade_and_fill() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);
    let intent = make_intent("m1", 1_000, now + 1_000);
    assert!(guard.evaluate(&intent).is_ok());

    // Record fill.
    let fill = make_fill("m1", 1_000, 500_000, 100);
    guard.record_fill(&fill);

    // Position should be tracked.
    assert_eq!(guard.positions.market_exposure_usd("m1"), 1_000);
    assert_eq!(guard.positions.count(), 1);
}

#[test]
fn per_market_cap_violation() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    // Fill up to cap.
    guard
        .positions
        .record_fill("m1", Side::Yes, 4_500, 500_000, now);

    let intent = make_intent("m1", 1_000, now + 1_000);
    let result = guard.evaluate(&intent);
    assert!(matches!(result, Err(RiskError::PositionCapExceeded { .. })));
}

#[test]
fn global_cap_violation() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    guard
        .positions
        .record_fill("m1", Side::Yes, 4_000, 500_000, now);
    guard
        .positions
        .record_fill("m2", Side::No, 4_000, 300_000, now);

    let intent = make_intent("m3", 3_000, now + 1_000);
    let result = guard.evaluate(&intent);
    assert!(matches!(result, Err(RiskError::GlobalCapExceeded { .. })));
}

#[test]
fn slippage_violation() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    let intent = TradeIntent {
        slippage_bps: 100,
        ..make_intent("m1", 1_000, now + 1_000)
    };
    assert!(matches!(
        guard.evaluate(&intent),
        Err(RiskError::SlippageTooHigh { .. })
    ));
}

#[test]
fn stale_signal_violation() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    let intent = TradeIntent {
        issued_at_unix_ms: now - 61_000,
        now_unix_ms: now + 1_000,
        watchlist_valid_until_unix_ms: now + 100_000,
        ..make_intent("m1", 1_000, now + 1_000)
    };
    assert!(matches!(
        guard.evaluate(&intent),
        Err(RiskError::StaleSignal { .. })
    ));
}

#[test]
fn watchlist_expired_violation() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    let intent = TradeIntent {
        watchlist_valid_until_unix_ms: now,
        ..make_intent("m1", 1_000, now + 1_000)
    };
    assert!(matches!(
        guard.evaluate(&intent),
        Err(RiskError::WatchlistExpired { .. })
    ));
}

#[test]
fn circuit_breaker_blocks_after_failures() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    // Trip the circuit breaker with 3 consecutive reverts.
    for _ in 0..3 {
        let rev = make_revert("m1", 100, "revert: out_of_gas");
        guard.record_revert(&rev);
    }

    assert!(matches!(guard.breaker.state(), CircuitState::Open { .. }));

    let intent = make_intent("m1", 1_000, now + 1_000);
    assert!(matches!(
        guard.evaluate(&intent),
        Err(RiskError::CircuitBreakerOpen { .. })
    ));
}

#[test]
fn dead_man_blocks_after_timeout() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    // 5 min + 1ms later, no heartbeat.
    let intent = TradeIntent {
        now_unix_ms: now + 300_001,
        watchlist_valid_until_unix_ms: now + 400_000,
        issued_at_unix_ms: now + 300_000,
        ..make_intent("m1", 1_000, now)
    };
    assert!(matches!(
        guard.evaluate(&intent),
        Err(RiskError::DeadManTriggered)
    ));
}

#[test]
fn heartbeat_prevents_dead_man() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    guard.record_heartbeat(now + 200_000);

    let intent = TradeIntent {
        now_unix_ms: now + 400_000,
        watchlist_valid_until_unix_ms: now + 500_000,
        issued_at_unix_ms: now + 399_000,
        ..make_intent("m1", 1_000, now)
    };
    assert!(guard.evaluate(&intent).is_ok());
}

#[test]
fn fill_updates_position_store() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    let fill = make_fill("m1", 2_000, 500_000, 50);
    guard.record_fill(&fill);

    assert_eq!(guard.positions.market_exposure_usd("m1"), 2_000);
    assert_eq!(guard.positions.total_exposure_usd(), 2_000);
    assert_eq!(guard.positions.count(), 1);

    // Second fill same market.
    let fill2 = make_fill("m1", 1_000, 600_000, 30);
    guard.record_fill(&fill2);

    assert_eq!(guard.positions.market_exposure_usd("m1"), 3_000);
    assert_eq!(guard.positions.total_exposure_usd(), 3_000);
}

#[test]
fn revert_records_gas_and_trips_breaker() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    // 3 reverts to trip breaker (cb_consecutive_fails=3).
    for i in 0..3 {
        let rev = make_revert(&format!("m{i}"), 500, "revert: slippage");
        guard.record_revert(&rev);
    }

    // Budget should track gas spent only (500 * 3 = 1500).
    assert_eq!(guard.budget.spent_today(), 1_500);
    assert!(matches!(guard.breaker.state(), CircuitState::Open { .. }));
}

#[test]
fn multiple_markets_tracked_independently() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);

    guard.record_fill(&make_fill("m1", 2_000, 500_000, 10));
    guard.record_fill(&make_fill("m2", 3_000, 400_000, 10));

    assert_eq!(guard.positions.market_exposure_usd("m1"), 2_000);
    assert_eq!(guard.positions.market_exposure_usd("m2"), 3_000);
    assert_eq!(guard.positions.total_exposure_usd(), 5_000);

    // m1 can still accept trades (2000 + 2000 = 4000 < 5000 cap).
    let intent = make_intent("m1", 2_000, now + 1_000);
    assert!(guard.evaluate(&intent).is_ok());

    // m2 cannot (3000 + 2500 = 5500 > 5000 cap).
    let intent = make_intent("m2", 2_500, now + 1_000);
    assert!(matches!(
        guard.evaluate(&intent),
        Err(RiskError::PositionCapExceeded { .. })
    ));
}

#[test]
fn metrics_emit_does_not_panic() {
    let now = 1_000_000i64;
    let guard = RiskGuard::new(test_limits(), now);
    guard.record_fill(&make_fill("m1", 1_000, 500_000, 50));
    // Should not panic even without a metrics recorder installed.
    guard.emit_metrics(now + 5_000);
}
