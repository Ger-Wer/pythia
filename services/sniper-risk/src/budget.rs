//! DailyBudget: tracks realized P&L and gas costs within a UTC day.
//!
//! Resets at UTC midnight. Persists state to Redis for crash recovery.
//! Blocks trades when cumulative losses exceed the daily cap.

use parking_lot::Mutex;
use time::macros::format_description;
use time::{Date, OffsetDateTime};
use tracing::{debug, info};

use crate::RiskError;

/// Daily budget tracker.
pub struct DailyBudget {
    inner: Mutex<DailyBudgetInner>,
    cap_usd: i64,
}

#[derive(Debug, Clone)]
struct DailyBudgetInner {
    date: Date,
    /// Realized P&L in micro-USD (negative = loss).
    realized_pnl_usd: i64,
    /// Gas spent in micro-USD.
    gas_spent_usd: i64,
}

impl DailyBudget {
    /// Create a new daily budget tracker.
    ///
    /// `cap_usd` — maximum daily loss budget in micro-USD.
    pub fn new(cap_usd: i64) -> Self {
        Self {
            inner: Mutex::new(DailyBudgetInner {
                date: OffsetDateTime::now_utc().date(),
                realized_pnl_usd: 0,
                gas_spent_usd: 0,
            }),
            cap_usd,
        }
    }

    /// Create with an explicit date (for testing).
    pub fn new_with_date(cap_usd: i64, date: Date) -> Self {
        Self {
            inner: Mutex::new(DailyBudgetInner {
                date,
                realized_pnl_usd: 0,
                gas_spent_usd: 0,
            }),
            cap_usd,
        }
    }

    /// Check if the daily budget allows a new trade.
    pub fn check(&self) -> Result<(), RiskError> {
        self.check_at(OffsetDateTime::now_utc())
    }

    /// Testable version with explicit time.
    pub fn check_at(&self, now: OffsetDateTime) -> Result<(), RiskError> {
        let mut inner = self.inner.lock();
        inner.maybe_reset(now.date());
        let spent = inner.total_spent();
        if spent >= self.cap_usd {
            return Err(RiskError::DailyBudgetExhausted {
                spent_usd: spent,
                cap_usd: self.cap_usd,
            });
        }
        Ok(())
    }

    /// Record a realized P&L event (can be negative for loss, positive for profit).
    pub fn record_pnl(&self, pnl_usd: i64) {
        self.record_pnl_at(pnl_usd, OffsetDateTime::now_utc());
    }

    /// Testable version.
    pub fn record_pnl_at(&self, pnl_usd: i64, now: OffsetDateTime) {
        let mut inner = self.inner.lock();
        inner.maybe_reset(now.date());
        // We track negative PnL as budget consumption.
        // Positive PnL doesn't reduce spending (conservative approach).
        if pnl_usd < 0 {
            inner.realized_pnl_usd += pnl_usd.abs();
        }
        debug!(
            pnl_usd,
            total_pnl_loss = inner.realized_pnl_usd,
            "daily budget: PnL recorded"
        );
    }

    /// Record gas expenditure.
    pub fn record_gas(&self, gas_usd: i64) {
        self.record_gas_at(gas_usd, OffsetDateTime::now_utc());
    }

    /// Testable version.
    pub fn record_gas_at(&self, gas_usd: i64, now: OffsetDateTime) {
        let mut inner = self.inner.lock();
        inner.maybe_reset(now.date());
        inner.gas_spent_usd += gas_usd;
        debug!(
            gas_usd,
            total_gas = inner.gas_spent_usd,
            "daily budget: gas recorded"
        );
    }

    /// Total spent today (for metrics).
    pub fn spent_today(&self) -> i64 {
        let inner = self.inner.lock();
        inner.total_spent()
    }

    /// Remaining budget (for metrics).
    pub fn remaining(&self) -> i64 {
        (self.cap_usd - self.spent_today()).max(0)
    }

    /// Current date being tracked.
    pub fn current_date(&self) -> Date {
        self.inner.lock().date
    }

    /// Serialize state for Redis persistence.
    pub fn snapshot(&self) -> (String, i64, i64) {
        let inner = self.inner.lock();
        let date_str = inner
            .date
            .format(format_description!("[year]-[month]-[day]"))
            .unwrap_or_default();
        (date_str, inner.realized_pnl_usd, inner.gas_spent_usd)
    }

    /// Restore from Redis.
    pub fn restore(&self, date: Date, realized_pnl_usd: i64, gas_spent_usd: i64) {
        let today = OffsetDateTime::now_utc().date();
        let mut inner = self.inner.lock();
        if date == today {
            inner.date = date;
            inner.realized_pnl_usd = realized_pnl_usd;
            inner.gas_spent_usd = gas_spent_usd;
            info!(
                ?date,
                realized_pnl_usd, gas_spent_usd, "daily budget restored from Redis"
            );
        } else {
            // Stale data, start fresh.
            inner.date = today;
            inner.realized_pnl_usd = 0;
            inner.gas_spent_usd = 0;
            info!(?today, "daily budget: stale Redis data, starting fresh");
        }
    }
}

impl DailyBudgetInner {
    fn maybe_reset(&mut self, today: Date) {
        if self.date != today {
            info!(old = ?self.date, new = ?today, "daily budget reset (UTC midnight)");
            self.date = today;
            self.realized_pnl_usd = 0;
            self.gas_spent_usd = 0;
        }
    }

    fn total_spent(&self) -> i64 {
        self.realized_pnl_usd + self.gas_spent_usd
    }
}

impl std::fmt::Debug for DailyBudget {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let inner = self.inner.lock();
        f.debug_struct("DailyBudget")
            .field("cap_usd", &self.cap_usd)
            .field("date", &inner.date)
            .field("realized_pnl_usd", &inner.realized_pnl_usd)
            .field("gas_spent_usd", &inner.gas_spent_usd)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::macros::date;

    #[test]
    fn within_budget_passes() {
        let budget = DailyBudget::new_with_date(100_000, date!(2025 - 01 - 15));
        let now = date!(2025 - 01 - 15)
            .with_hms(12, 0, 0)
            .unwrap()
            .assume_utc();
        budget.record_gas_at(50_000, now);
        assert!(budget.check_at(now).is_ok());
    }

    #[test]
    fn exceeding_budget_rejected() {
        let budget = DailyBudget::new_with_date(100_000, date!(2025 - 01 - 15));
        let now = date!(2025 - 01 - 15)
            .with_hms(12, 0, 0)
            .unwrap()
            .assume_utc();
        budget.record_gas_at(60_000, now);
        budget.record_pnl_at(-50_000, now); // loss
        assert!(budget.check_at(now).is_err());
    }

    #[test]
    fn positive_pnl_does_not_reduce_spent() {
        let budget = DailyBudget::new_with_date(100_000, date!(2025 - 01 - 15));
        let now = date!(2025 - 01 - 15)
            .with_hms(12, 0, 0)
            .unwrap()
            .assume_utc();
        budget.record_gas_at(90_000, now);
        budget.record_pnl_at(50_000, now); // profit doesn't help
                                           // Still at 90_000 spent (gas only, profit ignored).
        assert!(budget.check_at(now).is_ok());
        assert_eq!(budget.spent_today(), 90_000);
    }

    #[test]
    fn reset_on_utc_midnight() {
        let budget = DailyBudget::new_with_date(100_000, date!(2025 - 01 - 15));
        let day1 = date!(2025 - 01 - 15)
            .with_hms(23, 59, 0)
            .unwrap()
            .assume_utc();
        budget.record_gas_at(99_000, day1);
        assert_eq!(budget.spent_today(), 99_000);

        // Next day — should reset.
        let day2 = date!(2025 - 01 - 16)
            .with_hms(0, 1, 0)
            .unwrap()
            .assume_utc();
        assert!(budget.check_at(day2).is_ok());
        assert_eq!(budget.spent_today(), 0);
    }

    #[test]
    fn snapshot_and_restore() {
        let budget = DailyBudget::new_with_date(100_000, date!(2025 - 01 - 15));
        let now = date!(2025 - 01 - 15)
            .with_hms(12, 0, 0)
            .unwrap()
            .assume_utc();
        budget.record_gas_at(30_000, now);
        budget.record_pnl_at(-20_000, now);

        let (date_str, pnl, gas) = budget.snapshot();
        assert_eq!(date_str, "2025-01-15");
        assert_eq!(pnl, 20_000);
        assert_eq!(gas, 30_000);
    }

    #[test]
    fn remaining_calculation() {
        let budget = DailyBudget::new_with_date(100_000, date!(2025 - 01 - 15));
        let now = date!(2025 - 01 - 15)
            .with_hms(12, 0, 0)
            .unwrap()
            .assume_utc();
        budget.record_gas_at(30_000, now);
        assert_eq!(budget.remaining(), 70_000);
    }
}
