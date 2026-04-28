use bytes::Bytes;
use dual_loop_common::proto::common::{MarketId, Money, Side};
use dual_loop_common::proto::execution::{ExecFilled, ExecReverted, ExecSubmitted};
use dual_loop_common::proto::new_uuid_v7;
use dual_loop_common::proto::trigger::Trigger;

#[derive(Debug, Clone)]
pub struct PaperExecutorConfig {
    pub default_size_usd_micros: i64,
    pub auto_fill: bool,
    pub fill_delay_ms: i64,
    pub stuck_timeout_ms: i64,
    pub relay_used: String,
    pub max_fee_per_gas_wei: u64,
    pub max_priority_fee_per_gas_wei: u64,
    pub base_nonce: u64,
}

impl Default for PaperExecutorConfig {
    fn default() -> Self {
        Self {
            default_size_usd_micros: 1_000_000,
            auto_fill: true,
            fill_delay_ms: 750,
            stuck_timeout_ms: 10_000,
            relay_used: "paper-noop".to_string(),
            max_fee_per_gas_wei: 0,
            max_priority_fee_per_gas_wei: 0,
            base_nonce: 0,
        }
    }
}

impl PaperExecutorConfig {
    pub fn from_env() -> Self {
        let mut cfg = Self::default();

        if let Ok(v) = std::env::var("PAPER_DEFAULT_SIZE_USD_MICROS") {
            if let Ok(parsed) = v.parse() {
                cfg.default_size_usd_micros = parsed;
            }
        }
        if let Ok(v) = std::env::var("PAPER_AUTO_FILL") {
            cfg.auto_fill = !matches!(v.as_str(), "0" | "false" | "no");
        }
        if let Ok(v) = std::env::var("PAPER_FILL_DELAY_MS") {
            if let Ok(parsed) = v.parse() {
                cfg.fill_delay_ms = parsed;
            }
        }
        if let Ok(v) = std::env::var("EXECUTOR_STUCK_TIMEOUT_MS") {
            if let Ok(parsed) = v.parse() {
                cfg.stuck_timeout_ms = parsed;
            }
        }
        if let Ok(v) = std::env::var("PAPER_RELAY_USED") {
            if !v.trim().is_empty() {
                cfg.relay_used = v;
            }
        }
        if let Ok(v) = std::env::var("EXECUTOR_MAX_FEE_PER_GAS_WEI") {
            if let Ok(parsed) = v.parse() {
                cfg.max_fee_per_gas_wei = parsed;
            }
        }
        if let Ok(v) = std::env::var("EXECUTOR_MAX_PRIORITY_FEE_PER_GAS_WEI") {
            if let Ok(parsed) = v.parse() {
                cfg.max_priority_fee_per_gas_wei = parsed;
            }
        }
        if let Ok(v) = std::env::var("EXECUTOR_BASE_NONCE") {
            if let Ok(parsed) = v.parse() {
                cfg.base_nonce = parsed;
            }
        }

        cfg
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub enum BroadcastSink {
    #[default]
    Noop,
}

impl BroadcastSink {
    #[inline]
    pub fn submit(&self, _execution: &PlannedExecution) {
        match self {
            Self::Noop => {}
        }
    }
}

#[derive(Debug, Clone)]
pub struct PlannedExecution {
    pub nonce: u64,
    pub submitted_at_unix_ms: i64,
    pub fill_due_at_unix_ms: Option<i64>,
    pub timeout_at_unix_ms: i64,
    pub submitted: ExecSubmitted,
    pub fill: Option<ExecFilled>,
    pub timeout_revert: ExecReverted,
}

impl PlannedExecution {
    pub fn market(&self) -> Option<MarketId> {
        self.submitted.market.clone()
    }

    pub fn market_id(&self) -> String {
        self.submitted
            .market
            .as_ref()
            .map(|market| market.polymarket_id.clone())
            .unwrap_or_default()
    }
}

fn synthetic_tx_hash(trigger: &Trigger, nonce: u64, now_ms: i64) -> Bytes {
    let mut raw = [0u8; 32];
    if let Some(message_id) = trigger.message_id.as_ref() {
        let id = message_id.value.as_ref();
        let len = id.len().min(16);
        raw[..len].copy_from_slice(&id[..len]);
    }
    raw[16..24].copy_from_slice(&nonce.to_le_bytes());
    raw[24..32].copy_from_slice(&(now_ms as u64).to_le_bytes());
    Bytes::copy_from_slice(&raw)
}

pub fn plan_paper_execution(
    trigger: &Trigger,
    nonce: u64,
    now_ms: i64,
    cfg: &PaperExecutorConfig,
) -> PlannedExecution {
    let tx_hash = synthetic_tx_hash(trigger, nonce, now_ms);
    let size = Money {
        amount_micros: cfg.default_size_usd_micros,
    };
    let fill_due_at_unix_ms = if cfg.auto_fill {
        Some(now_ms.saturating_add(cfg.fill_delay_ms.max(0)))
    } else {
        None
    };
    let timeout_at_unix_ms = now_ms.saturating_add(cfg.stuck_timeout_ms.max(0));

    let submitted = ExecSubmitted {
        message_id: Some(new_uuid_v7()),
        trigger_id: trigger.message_id.clone(),
        market: trigger.market.clone(),
        tx_hash: tx_hash.clone(),
        nonce,
        size: Some(size.clone()),
        side: Side::Unspecified as i32,
        max_fee_per_gas_wei: cfg.max_fee_per_gas_wei,
        max_priority_fee_per_gas_wei: cfg.max_priority_fee_per_gas_wei,
        relay_used: cfg.relay_used.clone(),
        submitted_at_unix_ms: now_ms,
        paper_mode: true,
    };

    let fill = fill_due_at_unix_ms.map(|filled_at_unix_ms| ExecFilled {
        message_id: Some(new_uuid_v7()),
        tx_hash: tx_hash.clone(),
        market: trigger.market.clone(),
        realized_price: trigger.expected_price.clone().or(Some(Money {
            amount_micros: 0,
        })),
        filled_size: Some(size.clone()),
        gas_spent_usd: Some(Money { amount_micros: 0 }),
        block_number: 0,
        filled_at_unix_ms,
        paper_mode: true,
    });

    let timeout_revert = ExecReverted {
        message_id: Some(new_uuid_v7()),
        tx_hash,
        market: trigger.market.clone(),
        reason: "timeout".to_string(),
        gas_spent_usd: Some(Money { amount_micros: 0 }),
        block_number: 0,
        reverted_at_unix_ms: timeout_at_unix_ms,
        paper_mode: true,
    };

    PlannedExecution {
        nonce,
        submitted_at_unix_ms: now_ms,
        fill_due_at_unix_ms,
        timeout_at_unix_ms,
        submitted,
        fill,
        timeout_revert,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use dual_loop_common::proto::common::{MarketId, Money, Uuid};

    fn sample_trigger() -> Trigger {
        Trigger {
            message_id: Some(Uuid {
                value: Bytes::from_static(&[0xAB; 16]),
            }),
            market: Some(MarketId {
                polymarket_id: "market-1".to_string(),
                ctf_address: "0xfeed".to_string(),
            }),
            kind: 1,
            evidence_hash: Bytes::from_static(&[0x11; 32]),
            eta_block: 42,
            observed_at_ns: 100,
            expected_price: Some(Money {
                amount_micros: 720_000,
            }),
            slippage_bps: 25,
            source_count: 2,
        }
    }

    #[test]
    fn paper_plan_uses_trigger_price_and_configured_size() {
        let trigger = sample_trigger();
        let cfg = PaperExecutorConfig {
            default_size_usd_micros: 5_000_000,
            fill_delay_ms: 500,
            stuck_timeout_ms: 10_000,
            ..PaperExecutorConfig::default()
        };

        let planned = plan_paper_execution(&trigger, 7, 1_000_000, &cfg);

        assert_eq!(planned.submitted.nonce, 7);
        assert_eq!(
            planned
                .submitted
                .size
                .as_ref()
                .map(|money| money.amount_micros),
            Some(5_000_000)
        );
        assert_eq!(planned.submitted.side, Side::Unspecified as i32);
        assert_eq!(
            planned
                .fill
                .as_ref()
                .and_then(|fill| fill.realized_price.as_ref())
                .map(|money| money.amount_micros),
            Some(720_000)
        );
        assert_eq!(planned.fill_due_at_unix_ms, Some(1_000_500));
        assert_eq!(planned.timeout_at_unix_ms, 1_010_000);
    }

    #[test]
    fn paper_plan_can_disable_auto_fill_to_exercise_stuck_detector() {
        let trigger = sample_trigger();
        let cfg = PaperExecutorConfig {
            auto_fill: false,
            ..PaperExecutorConfig::default()
        };

        let planned = plan_paper_execution(&trigger, 1, 1_000, &cfg);

        assert!(planned.fill.is_none());
        assert_eq!(planned.fill_due_at_unix_ms, None);
        assert_eq!(planned.timeout_revert.reason, "timeout");
    }
}
