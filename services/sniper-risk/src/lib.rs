//! sniper-risk: pre-trade risk checks with stateful components.
//!
//! Phase 2: full risk engine — circuit breaker, dead-man switch, daily budget,
//! position store. All hot-path checks are O(1) with no allocations.
//!
//! ## Architecture
//!
//! [`RiskGuard`] is the integration façade consumed by `sniper-executor`.
//! It aggregates all stateful components and provides a single `evaluate()`
//! entry point. Background tasks (bus subscriptions, Redis persistence)
//! are spawned via `spawn_background_tasks()`.
//!
//! ## Example
//!
//! ```rust,ignore
//! let guard = RiskGuard::new(limits, redis_pool, bus_handle).await;
//! guard.spawn_background_tasks();
//!
//! // Before each trade:
//! guard.evaluate(&intent).await?;
//!
//! // After execution results:
//! guard.record_fill(fill).await;
//! guard.record_revert(rev).await;
//! ```

use thiserror::Error;

pub mod budget;
pub mod circuit;
pub mod deadman;
pub mod positions;

pub use budget::DailyBudget;
pub use circuit::{CircuitBreaker, CircuitBreakerConfig, CircuitState};
pub use deadman::DeadManSwitch;
pub use positions::{Position, PositionStore, Side};

// Re-export for downstream (sniper-executor).
pub use dual_loop_common::proto::execution::{ExecFilled, ExecReverted};

/// Risk check errors with detailed context for logging and metrics.
#[derive(Debug, Error)]
pub enum RiskError {
    #[error(
        "position cap exceeded for market: {market}, current {current_usd} + requested {requested_usd} > cap {cap_usd}"
    )]
    PositionCapExceeded {
        market: String,
        current_usd: i64,
        requested_usd: i64,
        cap_usd: i64,
    },
    #[error("global exposure cap exceeded: current {current_usd} + requested {requested_usd} > cap {cap_usd}")]
    GlobalCapExceeded {
        current_usd: i64,
        requested_usd: i64,
        cap_usd: i64,
    },
    #[error("slippage too high: {got_bps} bps > {max_bps} bps")]
    SlippageTooHigh { got_bps: u32, max_bps: u32 },
    #[error("watchlist entry expired for market: {market}")]
    WatchlistExpired { market: String },
    #[error("stale signal: age {age_ms}ms > 60000ms")]
    StaleSignal { age_ms: i64 },
    #[error("circuit breaker engaged: {reason}")]
    CircuitBreakerOpen { reason: String },
    #[error("dead-man switch: operator idle > threshold")]
    DeadManTriggered,
    #[error("daily budget exhausted: spent {spent_usd}, cap {cap_usd}")]
    DailyBudgetExhausted { spent_usd: i64, cap_usd: i64 },
}

/// Trade intent for pre-trade risk evaluation.
#[derive(Debug, Clone)]
pub struct TradeIntent {
    pub market_id: String,
    pub size_usd: i64,
    pub slippage_bps: u32,
    pub watchlist_valid_until_unix_ms: i64,
    /// Signal issuance time (for stale signal check).
    pub issued_at_unix_ms: i64,
    pub now_unix_ms: i64,
}

/// Static risk limits loaded from ENV.
#[derive(Debug, Clone)]
pub struct RiskLimits {
    pub per_market_cap_usd: i64,
    pub global_cap_usd: i64,
    pub max_slippage_bps: u32,
    pub daily_budget_usd: i64,
    /// Stale signal threshold in milliseconds (default 60_000).
    pub stale_signal_threshold_ms: i64,
    /// Dead-man switch timeout in milliseconds (default 300_000).
    pub dead_man_timeout_ms: i64,
    /// Circuit breaker config.
    pub cb_consecutive_fails: u32,
    pub cb_failure_rate: f64,
    pub cb_window_secs: u64,
}

impl Default for RiskLimits {
    fn default() -> Self {
        Self {
            per_market_cap_usd: 5_000_000_000,
            global_cap_usd: 50_000_000_000,
            max_slippage_bps: 50,
            daily_budget_usd: 100_000_000_000,
            stale_signal_threshold_ms: 60_000,
            dead_man_timeout_ms: 300_000,
            cb_consecutive_fails: 5,
            cb_failure_rate: 0.5,
            cb_window_secs: 60,
        }
    }
}

impl RiskLimits {
    /// Load from environment variables with defaults.
    pub fn from_env() -> Self {
        let mut limits = Self::default();
        if let Ok(v) = std::env::var("RISK_PER_MARKET_CAP_USD") {
            if let Ok(n) = v.parse() {
                limits.per_market_cap_usd = n;
            }
        }
        if let Ok(v) = std::env::var("RISK_GLOBAL_CAP_USD") {
            if let Ok(n) = v.parse() {
                limits.global_cap_usd = n;
            }
        }
        if let Ok(v) = std::env::var("RISK_MAX_SLIPPAGE_BPS") {
            if let Ok(n) = v.parse() {
                limits.max_slippage_bps = n;
            }
        }
        if let Ok(v) = std::env::var("RISK_DAILY_BUDGET_USD") {
            if let Ok(n) = v.parse() {
                limits.daily_budget_usd = n;
            }
        }
        if let Ok(v) = std::env::var("RISK_CB_CONSECUTIVE_FAILS") {
            if let Ok(n) = v.parse() {
                limits.cb_consecutive_fails = n;
            }
        }
        if let Ok(v) = std::env::var("RISK_CB_FAILURE_RATE") {
            if let Ok(n) = v.parse() {
                limits.cb_failure_rate = n;
            }
        }
        if let Ok(v) = std::env::var("RISK_CB_WINDOW_SECS") {
            if let Ok(n) = v.parse() {
                limits.cb_window_secs = n;
            }
        }
        if let Ok(v) = std::env::var("DEAD_MAN_TIMEOUT_MS") {
            if let Ok(n) = v.parse() {
                limits.dead_man_timeout_ms = n;
            }
        }
        limits
    }
}

/// Result type for risk checks.
pub type RiskResult<T> = Result<T, RiskError>;

/// Stateful pre-trade checks (Phase 2).
///
/// Evaluates: watchlist freshness, stale signal, slippage, per-market cap,
/// global cap. Position data is read from the shared [`PositionStore`].
pub struct PreTradeChecks {
    limits: RiskLimits,
}

impl PreTradeChecks {
    pub fn new(limits: RiskLimits) -> Self {
        Self { limits }
    }

    /// O(1) stateful evaluate. Reads positions from the store.
    pub fn evaluate(&self, intent: &TradeIntent, positions: &PositionStore) -> RiskResult<()> {
        // 1. Watchlist freshness.
        if intent.watchlist_valid_until_unix_ms <= intent.now_unix_ms {
            return Err(RiskError::WatchlistExpired {
                market: intent.market_id.clone(),
            });
        }

        // 2. Stale signal.
        let signal_age = intent.now_unix_ms - intent.issued_at_unix_ms;
        if signal_age > self.limits.stale_signal_threshold_ms {
            return Err(RiskError::StaleSignal { age_ms: signal_age });
        }

        // 3. Slippage bound.
        if intent.slippage_bps > self.limits.max_slippage_bps {
            return Err(RiskError::SlippageTooHigh {
                got_bps: intent.slippage_bps,
                max_bps: self.limits.max_slippage_bps,
            });
        }

        // 4. Per-market position cap.
        let current_market = positions.market_exposure_usd(&intent.market_id);
        if current_market + intent.size_usd > self.limits.per_market_cap_usd {
            return Err(RiskError::PositionCapExceeded {
                market: intent.market_id.clone(),
                current_usd: current_market,
                requested_usd: intent.size_usd,
                cap_usd: self.limits.per_market_cap_usd,
            });
        }

        // 5. Global position cap.
        let current_global = positions.total_exposure_usd();
        if current_global + intent.size_usd > self.limits.global_cap_usd {
            return Err(RiskError::GlobalCapExceeded {
                current_usd: current_global,
                requested_usd: intent.size_usd,
                cap_usd: self.limits.global_cap_usd,
            });
        }

        Ok(())
    }
}

/// Façade that aggregates all risk components.
///
/// `sniper-executor` imports this and calls `guard.evaluate(&intent)` before
/// every transaction broadcast.
pub struct RiskGuard {
    pub checks: PreTradeChecks,
    pub breaker: CircuitBreaker,
    pub deadman: DeadManSwitch,
    pub budget: DailyBudget,
    pub positions: PositionStore,
}

impl RiskGuard {
    /// Create a new RiskGuard from limits.
    ///
    /// `now_ms` — current wall-clock time for dead-man grace period.
    pub fn new(limits: RiskLimits, now_ms: i64) -> Self {
        let cb_config = CircuitBreakerConfig {
            consecutive_fail_threshold: limits.cb_consecutive_fails,
            failure_rate_threshold: limits.cb_failure_rate,
            window_duration: std::time::Duration::from_secs(limits.cb_window_secs),
        };
        Self {
            checks: PreTradeChecks::new(limits.clone()),
            breaker: CircuitBreaker::new(cb_config),
            deadman: DeadManSwitch::new(limits.dead_man_timeout_ms, now_ms),
            budget: DailyBudget::new(limits.daily_budget_usd),
            positions: PositionStore::new(),
        }
    }

    /// Full pre-trade evaluation pipeline.
    ///
    /// Checks in order (fail-fast):
    /// 1. Circuit breaker (is trade engine healthy?)
    /// 2. Dead-man switch (is operator alive?)
    /// 3. Daily budget (have we exceeded loss cap?)
    /// 4. Static checks (watchlist, signal, slippage, position caps)
    pub fn evaluate(&self, intent: &TradeIntent) -> RiskResult<()> {
        // 1. Circuit breaker.
        self.breaker.check()?;

        // 2. Dead-man switch.
        self.deadman.check(intent.now_unix_ms)?;

        // 3. Daily budget.
        self.budget.check()?;

        // 4. Static + position checks.
        self.checks.evaluate(intent, &self.positions)?;

        Ok(())
    }

    /// Record a successful fill from the execution pipeline.
    pub fn record_fill(&self, fill: &ExecFilled) {
        let market_id = fill
            .market
            .as_ref()
            .map(|m| m.polymarket_id.clone())
            .unwrap_or_default();

        let size_usd = fill
            .filled_size
            .as_ref()
            .map(|m| m.amount_micros)
            .unwrap_or(0);
        let price_micros = fill
            .realized_price
            .as_ref()
            .map(|m| m.amount_micros)
            .unwrap_or(0);
        let gas_usd = fill
            .gas_spent_usd
            .as_ref()
            .map(|m| m.amount_micros)
            .unwrap_or(0);
        let filled_at = fill
            .filled_at
            .as_ref()
            .map(|t| t.wall_clock_unix_ms)
            .unwrap_or(0);

        self.positions
            .record_fill(&market_id, Side::Yes, size_usd, price_micros, filled_at);
        self.budget.record_gas(gas_usd);
        self.breaker.record_success();

        metrics::counter!("risk_checks_total", "outcome" => "accepted").increment(1);
    }

    /// Record a reverted transaction.
    pub fn record_revert(&self, rev: &ExecReverted) {
        let gas_usd = rev
            .gas_spent_usd
            .as_ref()
            .map(|m| m.amount_micros)
            .unwrap_or(0);

        self.budget.record_gas(gas_usd);
        self.budget.record_pnl(-gas_usd);

        let reason = &rev.reason;
        self.breaker.record_failure(reason);
    }

    /// Record a heartbeat from the operator.
    pub fn record_heartbeat(&self, ts_ms: i64) {
        self.deadman.record_heartbeat(ts_ms);
    }

    /// Export Prometheus metrics.
    pub fn emit_metrics(&self, now_ms: i64) {
        // Circuit breaker state gauge.
        metrics::gauge!("circuit_breaker_state").set(self.breaker.state_gauge());

        // Dead-man seconds since heartbeat.
        metrics::gauge!("deadman_seconds_since_heartbeat")
            .set(self.deadman.seconds_since_heartbeat(now_ms));

        // Daily budget.
        metrics::gauge!("daily_budget_spent_usd").set(self.budget.spent_today() as f64);
        metrics::gauge!("daily_budget_remaining_usd").set(self.budget.remaining() as f64);

        // Positions.
        metrics::gauge!("open_positions_count").set(self.positions.count() as f64);
        metrics::gauge!("open_positions_exposure_usd")
            .set(self.positions.total_exposure_usd() as f64);
    }
}

impl std::fmt::Debug for RiskGuard {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RiskGuard")
            .field("breaker", &self.breaker)
            .field("deadman", &self.deadman)
            .field("budget", &self.budget)
            .field("positions_count", &self.positions.count())
            .finish()
    }
}

/// Emit a risk_checks_total counter increment with the appropriate outcome label.
pub fn record_risk_outcome(result: &RiskResult<()>) {
    let outcome = match result {
        Ok(()) => "accepted",
        Err(RiskError::PositionCapExceeded { .. }) => "position_cap",
        Err(RiskError::GlobalCapExceeded { .. }) => "position_cap",
        Err(RiskError::SlippageTooHigh { .. }) => "slippage",
        Err(RiskError::WatchlistExpired { .. }) => "stale",
        Err(RiskError::StaleSignal { .. }) => "stale",
        Err(RiskError::CircuitBreakerOpen { .. }) => "cb_open",
        Err(RiskError::DeadManTriggered) => "deadman",
        Err(RiskError::DailyBudgetExhausted { .. }) => "budget",
    };
    metrics::counter!("risk_checks_total", "outcome" => outcome).increment(1);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_limits() -> RiskLimits {
        RiskLimits {
            per_market_cap_usd: 5_000_000_000,
            global_cap_usd: 50_000_000_000,
            max_slippage_bps: 50,
            daily_budget_usd: 100_000_000_000,
            stale_signal_threshold_ms: 60_000,
            dead_man_timeout_ms: 300_000,
            cb_consecutive_fails: 5,
            cb_failure_rate: 0.5,
            cb_window_secs: 60,
        }
    }

    fn good_intent() -> TradeIntent {
        TradeIntent {
            market_id: "m1".to_string(),
            size_usd: 1_000_000_000,
            slippage_bps: 20,
            watchlist_valid_until_unix_ms: 2_000_000,
            issued_at_unix_ms: 999_000,
            now_unix_ms: 1_000_000,
        }
    }

    #[test]
    fn ok_trade_passes() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        assert!(guard.evaluate(&good_intent()).is_ok());
    }

    #[test]
    fn expired_watchlist_rejected() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        let intent = TradeIntent {
            watchlist_valid_until_unix_ms: 500_000,
            ..good_intent()
        };
        assert!(matches!(
            guard.evaluate(&intent),
            Err(RiskError::WatchlistExpired { .. })
        ));
    }

    #[test]
    fn stale_signal_rejected() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        let intent = TradeIntent {
            issued_at_unix_ms: 900_000, // 100s old > 60s threshold
            now_unix_ms: 1_000_000,
            ..good_intent()
        };
        assert!(matches!(
            guard.evaluate(&intent),
            Err(RiskError::StaleSignal { .. })
        ));
    }

    #[test]
    fn high_slippage_rejected() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        let intent = TradeIntent {
            slippage_bps: 500,
            ..good_intent()
        };
        assert!(matches!(
            guard.evaluate(&intent),
            Err(RiskError::SlippageTooHigh { .. })
        ));
    }

    #[test]
    fn per_market_cap_rejected() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        // Fill up to near cap.
        guard
            .positions
            .record_fill("m1", Side::Yes, 4_500_000_000, 500_000, 1_000_000);
        let intent = TradeIntent {
            size_usd: 1_000_000_000, // 4.5B + 1B > 5B cap
            ..good_intent()
        };
        assert!(matches!(
            guard.evaluate(&intent),
            Err(RiskError::PositionCapExceeded { .. })
        ));
    }

    #[test]
    fn global_cap_rejected() {
        let limits = RiskLimits {
            global_cap_usd: 3_000_000_000,
            ..default_limits()
        };
        let guard = RiskGuard::new(limits, 1_000_000);
        guard
            .positions
            .record_fill("m2", Side::Yes, 2_500_000_000, 500_000, 1_000_000);
        let intent = TradeIntent {
            size_usd: 1_000_000_000, // 2.5B + 1B > 3B global cap
            ..good_intent()
        };
        assert!(matches!(
            guard.evaluate(&intent),
            Err(RiskError::GlobalCapExceeded { .. })
        ));
    }

    #[test]
    fn dead_man_blocks_when_expired() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        let intent = TradeIntent {
            now_unix_ms: 1_000_000 + 300_001, // > 5min after startup
            watchlist_valid_until_unix_ms: 1_000_000 + 400_000,
            issued_at_unix_ms: 1_000_000 + 300_000,
            ..good_intent()
        };
        assert!(matches!(
            guard.evaluate(&intent),
            Err(RiskError::DeadManTriggered)
        ));
    }

    #[test]
    fn heartbeat_keeps_alive() {
        let guard = RiskGuard::new(default_limits(), 1_000_000);
        guard.record_heartbeat(1_000_000 + 200_000);
        let intent = TradeIntent {
            now_unix_ms: 1_000_000 + 400_000,
            watchlist_valid_until_unix_ms: 1_000_000 + 500_000,
            issued_at_unix_ms: 1_000_000 + 399_000,
            ..good_intent()
        };
        assert!(guard.evaluate(&intent).is_ok());
    }
}
