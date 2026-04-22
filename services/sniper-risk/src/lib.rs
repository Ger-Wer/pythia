//! sniper-risk: pre-trade risk checks.
//!
//! Phase 0: скелет с типами и заглушкой PreTradeChecks. Реальные проверки
//! position caps / circuit breaker / dead-man switch появятся в Phase 2.
//!
//! Все методы на hot path должны быть O(1) и без аллокаций.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum RiskError {
    #[error(
        "position cap exceeded for market: {market}, requested {requested_usd}, cap {cap_usd}"
    )]
    PositionCapExceeded {
        market: String,
        requested_usd: i64,
        cap_usd: i64,
    },
    #[error("slippage too high: {got_bps} bps > {max_bps} bps")]
    SlippageTooHigh { got_bps: u32, max_bps: u32 },
    #[error("watchlist entry expired for market: {market}")]
    WatchlistExpired { market: String },
    #[error("circuit breaker engaged: {reason}")]
    CircuitBreakerOpen { reason: String },
    #[error("dead-man switch: operator idle > threshold")]
    DeadManTriggered,
    #[error("daily budget exhausted: spent {spent_usd}, cap {cap_usd}")]
    DailyBudgetExhausted { spent_usd: i64, cap_usd: i64 },
}

/// Вход для pre-trade check.
#[derive(Debug, Clone)]
pub struct TradeIntent {
    pub market_id: String,
    pub size_usd: i64,
    pub slippage_bps: u32,
    pub watchlist_valid_until_unix_ms: i64,
    pub now_unix_ms: i64,
}

/// Конфигурация лимитов.
#[derive(Debug, Clone)]
pub struct RiskLimits {
    pub per_market_cap_usd: i64,
    pub global_cap_usd: i64,
    pub max_slippage_bps: u32,
    pub daily_budget_usd: i64,
}

/// Результат pre-trade check.
pub type RiskResult<T> = Result<T, RiskError>;

/// Placeholder PreTradeChecks. В Phase 2 расширится состоянием (open positions,
/// circuit breaker status, operator heartbeat, spent budget).
#[derive(Debug, Clone)]
pub struct PreTradeChecks {
    limits: RiskLimits,
}

impl PreTradeChecks {
    pub fn new(limits: RiskLimits) -> Self {
        Self { limits }
    }

    /// O(1) evaluate. Возвращает Ok если сделка проходит, иначе детализированную ошибку.
    pub fn evaluate(&self, intent: &TradeIntent) -> RiskResult<()> {
        if intent.watchlist_valid_until_unix_ms <= intent.now_unix_ms {
            return Err(RiskError::WatchlistExpired {
                market: intent.market_id.clone(),
            });
        }
        if intent.slippage_bps > self.limits.max_slippage_bps {
            return Err(RiskError::SlippageTooHigh {
                got_bps: intent.slippage_bps,
                max_bps: self.limits.max_slippage_bps,
            });
        }
        if intent.size_usd > self.limits.per_market_cap_usd {
            return Err(RiskError::PositionCapExceeded {
                market: intent.market_id.clone(),
                requested_usd: intent.size_usd,
                cap_usd: self.limits.per_market_cap_usd,
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn limits() -> RiskLimits {
        RiskLimits {
            per_market_cap_usd: 5_000_000_000, // $5k в микро-USD
            global_cap_usd: 50_000_000_000,
            max_slippage_bps: 50,
            daily_budget_usd: 100_000_000_000,
        }
    }

    #[test]
    fn ok_trade_passes() {
        let checks = PreTradeChecks::new(limits());
        let intent = TradeIntent {
            market_id: "m1".to_string(),
            size_usd: 1_000_000_000,
            slippage_bps: 20,
            watchlist_valid_until_unix_ms: 2_000,
            now_unix_ms: 1_000,
        };
        assert!(checks.evaluate(&intent).is_ok());
    }

    #[test]
    fn expired_watchlist_rejected() {
        let checks = PreTradeChecks::new(limits());
        let intent = TradeIntent {
            market_id: "m1".to_string(),
            size_usd: 100,
            slippage_bps: 10,
            watchlist_valid_until_unix_ms: 1_000,
            now_unix_ms: 2_000,
        };
        assert!(matches!(
            checks.evaluate(&intent),
            Err(RiskError::WatchlistExpired { .. })
        ));
    }

    #[test]
    fn high_slippage_rejected() {
        let checks = PreTradeChecks::new(limits());
        let intent = TradeIntent {
            market_id: "m1".to_string(),
            size_usd: 100,
            slippage_bps: 500,
            watchlist_valid_until_unix_ms: 2_000,
            now_unix_ms: 1_000,
        };
        assert!(matches!(
            checks.evaluate(&intent),
            Err(RiskError::SlippageTooHigh { .. })
        ));
    }

    #[test]
    fn position_cap_rejected() {
        let checks = PreTradeChecks::new(limits());
        let intent = TradeIntent {
            market_id: "m1".to_string(),
            size_usd: 10_000_000_000,
            slippage_bps: 20,
            watchlist_valid_until_unix_ms: 2_000,
            now_unix_ms: 1_000,
        };
        assert!(matches!(
            checks.evaluate(&intent),
            Err(RiskError::PositionCapExceeded { .. })
        ));
    }
}
