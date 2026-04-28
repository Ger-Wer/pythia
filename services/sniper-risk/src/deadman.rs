//! DeadManSwitch: blocks new trades when operator heartbeat is stale.
//!
//! Subscribes to `dl.v1.control.heartbeat`. If no heartbeat is received
//! within `DEAD_MAN_TIMEOUT_MS` (default 5 min), all new trades are blocked.
//! Initial state: `last_heartbeat = now()` on startup (grace period).

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Arc;

use crate::RiskError;

/// Dead-man switch that tracks operator liveness via heartbeats.
#[derive(Debug, Clone)]
pub struct DeadManSwitch {
    last_heartbeat_ms: Arc<AtomicI64>,
    timeout_ms: i64,
}

impl DeadManSwitch {
    /// Create a new dead-man switch.
    ///
    /// `timeout_ms` — how long without a heartbeat before blocking (default 300_000 = 5 min).
    /// `now_ms` — current wall-clock time for initial grace period.
    pub fn new(timeout_ms: i64, now_ms: i64) -> Self {
        Self {
            last_heartbeat_ms: Arc::new(AtomicI64::new(now_ms)),
            timeout_ms,
        }
    }

    /// Record a heartbeat from the operator.
    pub fn record_heartbeat(&self, ts_ms: i64) {
        self.last_heartbeat_ms.store(ts_ms, Ordering::Relaxed);
    }

    /// Check if the operator is still alive.
    /// Returns Ok(()) if within timeout, Err(DeadManTriggered) otherwise.
    pub fn check(&self, now_ms: i64) -> Result<(), RiskError> {
        let last = self.last_heartbeat_ms.load(Ordering::Relaxed);
        let elapsed = now_ms - last;
        if elapsed > self.timeout_ms {
            return Err(RiskError::DeadManTriggered);
        }
        Ok(())
    }

    /// Seconds since last heartbeat (for metrics).
    pub fn seconds_since_heartbeat(&self, now_ms: i64) -> f64 {
        let last = self.last_heartbeat_ms.load(Ordering::Relaxed);
        ((now_ms - last) as f64) / 1000.0
    }

    /// Get the last heartbeat timestamp.
    pub fn last_heartbeat_ms(&self) -> i64 {
        self.last_heartbeat_ms.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn initial_grace_period() {
        let now = 1_000_000;
        let dm = DeadManSwitch::new(300_000, now);
        // Immediately after creation, should pass.
        assert!(dm.check(now + 1_000).is_ok());
    }

    #[test]
    fn heartbeat_refreshes() {
        let now = 1_000_000;
        let dm = DeadManSwitch::new(300_000, now);
        // Fast-forward 4 min. Still ok.
        assert!(dm.check(now + 240_000).is_ok());
        // Send heartbeat at 4 min.
        dm.record_heartbeat(now + 240_000);
        // Now at 9 min from start but only 5 min from last heartbeat — still ok.
        assert!(dm.check(now + 540_000).is_ok());
    }

    #[test]
    fn expires_after_timeout() {
        let now = 1_000_000;
        let dm = DeadManSwitch::new(300_000, now);
        // 5 min + 1ms past last heartbeat.
        assert!(dm.check(now + 300_001).is_err());
    }

    #[test]
    fn exactly_at_timeout_passes() {
        let now = 1_000_000;
        let dm = DeadManSwitch::new(300_000, now);
        // Exactly at timeout boundary should still pass (> not >=).
        assert!(dm.check(now + 300_000).is_ok());
    }

    #[test]
    fn seconds_since_heartbeat_metric() {
        let now = 1_000_000;
        let dm = DeadManSwitch::new(300_000, now);
        let secs = dm.seconds_since_heartbeat(now + 60_000);
        assert!((secs - 60.0).abs() < 0.01);
    }
}
