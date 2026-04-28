//! CircuitBreaker: sliding-window failure tracking with exponential backoff.
//!
//! State machine: Closed → Open → HalfOpen → Closed (on success) or Open (on failure).
//! Prevents cascading failures by blocking trades after consecutive failures
//! or when failure rate exceeds threshold within a sliding window.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use tracing::{info, warn};

use crate::RiskError;

/// Circuit breaker configuration.
#[derive(Debug, Clone)]
pub struct CircuitBreakerConfig {
    /// Number of consecutive failures to trip the breaker.
    pub consecutive_fail_threshold: u32,
    /// Failure rate (0.0–1.0) within the window to trip.
    pub failure_rate_threshold: f64,
    /// Sliding window duration.
    pub window_duration: Duration,
}

impl Default for CircuitBreakerConfig {
    fn default() -> Self {
        Self {
            consecutive_fail_threshold: 5,
            failure_rate_threshold: 0.5,
            window_duration: Duration::from_secs(60),
        }
    }
}

/// Possible states of the circuit breaker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CircuitState {
    Closed,
    Open { until: Instant },
    HalfOpen,
}

#[derive(Debug, Clone)]
struct FailureEvent {
    at: Instant,
    #[allow(dead_code)]
    reason: String,
}

#[derive(Debug, Clone)]
struct SuccessEvent {
    at: Instant,
}

/// Exponential backoff tiers for Open state duration.
const BACKOFF_TIERS: &[Duration] = &[
    Duration::from_secs(30),
    Duration::from_secs(300),
    Duration::from_secs(1800),
    Duration::from_secs(3600),
];

/// Thread-safe circuit breaker.
pub struct CircuitBreaker {
    inner: Mutex<CircuitBreakerInner>,
}

struct CircuitBreakerInner {
    config: CircuitBreakerConfig,
    state: CircuitState,
    failures: VecDeque<FailureEvent>,
    successes: VecDeque<SuccessEvent>,
    consecutive_failures: u32,
    backoff_tier: usize,
}

impl CircuitBreaker {
    pub fn new(config: CircuitBreakerConfig) -> Self {
        Self {
            inner: Mutex::new(CircuitBreakerInner {
                config,
                state: CircuitState::Closed,
                failures: VecDeque::new(),
                successes: VecDeque::new(),
                consecutive_failures: 0,
                backoff_tier: 0,
            }),
        }
    }

    /// Check if the circuit breaker allows a trade.
    /// Returns Ok(()) if closed or half-open (allowing one probe trade).
    /// Returns Err(CircuitBreakerOpen) if open.
    pub fn check(&self) -> Result<(), RiskError> {
        self.check_at(Instant::now())
    }

    /// Testable version with explicit timestamp.
    pub fn check_at(&self, now: Instant) -> Result<(), RiskError> {
        let mut inner = self.inner.lock();
        match &inner.state {
            CircuitState::Closed => Ok(()),
            CircuitState::Open { until } => {
                if now >= *until {
                    info!("circuit breaker transitioning Open → HalfOpen");
                    inner.state = CircuitState::HalfOpen;
                    Ok(())
                } else {
                    Err(RiskError::CircuitBreakerOpen {
                        reason: format!("open for {}s more", until.duration_since(now).as_secs()),
                    })
                }
            }
            CircuitState::HalfOpen => Ok(()),
        }
    }

    /// Record a successful trade outcome.
    pub fn record_success(&self) {
        self.record_success_at(Instant::now());
    }

    /// Testable version with explicit timestamp.
    pub fn record_success_at(&self, now: Instant) {
        let mut inner = self.inner.lock();
        inner.successes.push_back(SuccessEvent { at: now });
        inner.consecutive_failures = 0;
        inner.prune_window(now);

        if inner.state == CircuitState::HalfOpen {
            info!("circuit breaker HalfOpen → Closed (probe success)");
            inner.state = CircuitState::Closed;
            inner.backoff_tier = 0;
        }
    }

    /// Record a failed trade outcome.
    pub fn record_failure(&self, reason: &str) {
        self.record_failure_at(reason, Instant::now());
    }

    /// Testable version with explicit timestamp.
    pub fn record_failure_at(&self, reason: &str, now: Instant) {
        let mut inner = self.inner.lock();
        inner.failures.push_back(FailureEvent {
            at: now,
            reason: reason.to_string(),
        });
        inner.consecutive_failures += 1;
        inner.prune_window(now);

        if inner.state == CircuitState::HalfOpen {
            // HalfOpen probe failed → back to Open with next tier.
            let tier = inner.backoff_tier.min(BACKOFF_TIERS.len() - 1);
            let timeout = BACKOFF_TIERS[tier];
            inner.backoff_tier = (inner.backoff_tier + 1).min(BACKOFF_TIERS.len() - 1);
            inner.state = CircuitState::Open {
                until: now + timeout,
            };
            warn!(
                tier,
                timeout_secs = timeout.as_secs(),
                "circuit breaker HalfOpen → Open (probe failed)"
            );
            return;
        }

        // Check if we should trip from Closed → Open.
        let should_trip = inner.should_trip();
        if should_trip && inner.state == CircuitState::Closed {
            let tier = inner.backoff_tier.min(BACKOFF_TIERS.len() - 1);
            let timeout = BACKOFF_TIERS[tier];
            inner.backoff_tier = (inner.backoff_tier + 1).min(BACKOFF_TIERS.len() - 1);
            inner.state = CircuitState::Open {
                until: now + timeout,
            };
            warn!(
                consecutive = inner.consecutive_failures,
                tier,
                timeout_secs = timeout.as_secs(),
                reason,
                "circuit breaker Closed → Open"
            );
        }
    }

    /// Current state (for metrics/inspection).
    pub fn state(&self) -> CircuitState {
        let inner = self.inner.lock();
        inner.state
    }

    /// Current state as a numeric gauge value.
    pub fn state_gauge(&self) -> f64 {
        match self.state() {
            CircuitState::Closed => 0.0,
            CircuitState::HalfOpen => 1.0,
            CircuitState::Open { .. } => 2.0,
        }
    }
}

impl CircuitBreakerInner {
    fn prune_window(&mut self, now: Instant) {
        let cutoff = now.checked_sub(self.config.window_duration).unwrap_or(now);
        while self.failures.front().is_some_and(|e| e.at < cutoff) {
            self.failures.pop_front();
        }
        while self.successes.front().is_some_and(|e| e.at < cutoff) {
            self.successes.pop_front();
        }
    }

    fn should_trip(&self) -> bool {
        if self.consecutive_failures >= self.config.consecutive_fail_threshold {
            return true;
        }
        let total = self.failures.len() + self.successes.len();
        if total >= 5 {
            let rate = self.failures.len() as f64 / total as f64;
            if rate >= self.config.failure_rate_threshold {
                return true;
            }
        }
        false
    }
}

impl std::fmt::Debug for CircuitBreaker {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let inner = self.inner.lock();
        f.debug_struct("CircuitBreaker")
            .field("state", &inner.state)
            .field("consecutive_failures", &inner.consecutive_failures)
            .field("backoff_tier", &inner.backoff_tier)
            .field("window_failures", &inner.failures.len())
            .field("window_successes", &inner.successes.len())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_cb() -> CircuitBreaker {
        CircuitBreaker::new(CircuitBreakerConfig::default())
    }

    #[test]
    fn starts_closed() {
        let cb = default_cb();
        assert_eq!(cb.state(), CircuitState::Closed);
        assert!(cb.check().is_ok());
    }

    #[test]
    fn consecutive_failures_trip_to_open() {
        let cb = default_cb();
        let now = Instant::now();
        for i in 0..5 {
            cb.record_failure_at("err", now + Duration::from_millis(i * 10));
        }
        assert!(matches!(cb.state(), CircuitState::Open { .. }));
        assert!(cb.check_at(now + Duration::from_millis(100)).is_err());
    }

    #[test]
    fn open_transitions_to_halfopen_after_timeout() {
        let cb = default_cb();
        let now = Instant::now();
        for i in 0..5 {
            cb.record_failure_at("err", now + Duration::from_millis(i * 10));
        }
        // After 30s (first backoff tier), check should transition to HalfOpen.
        let after = now + Duration::from_secs(31);
        assert!(cb.check_at(after).is_ok());
        assert_eq!(cb.state(), CircuitState::HalfOpen);
    }

    #[test]
    fn halfopen_success_closes() {
        let cb = default_cb();
        let now = Instant::now();
        for i in 0..5 {
            cb.record_failure_at("err", now + Duration::from_millis(i * 10));
        }
        let after_open = now + Duration::from_secs(31);
        cb.check_at(after_open).unwrap(); // Transition to HalfOpen.
        cb.record_success_at(after_open + Duration::from_secs(1));
        assert_eq!(cb.state(), CircuitState::Closed);
    }

    #[test]
    fn halfopen_failure_reopens_with_higher_tier() {
        let cb = default_cb();
        let now = Instant::now();
        // Trip to Open (tier 0 = 30s).
        for i in 0..5 {
            cb.record_failure_at("err", now + Duration::from_millis(i * 10));
        }
        // Transition to HalfOpen.
        let t1 = now + Duration::from_secs(31);
        cb.check_at(t1).unwrap();
        assert_eq!(cb.state(), CircuitState::HalfOpen);

        // Fail in HalfOpen → Open (tier 1 = 300s).
        cb.record_failure_at("err2", t1 + Duration::from_secs(1));
        match cb.state() {
            CircuitState::Open { until } => {
                let wait = until.duration_since(t1 + Duration::from_secs(1));
                assert!(wait.as_secs() >= 290 && wait.as_secs() <= 300);
            }
            other => panic!("expected Open, got {:?}", other),
        }
    }

    #[test]
    fn success_resets_consecutive_counter() {
        let cb = default_cb();
        let now = Instant::now();
        // 4 failures (below consecutive threshold of 5).
        for i in 0..4 {
            cb.record_failure_at("err", now + Duration::from_millis(i * 10));
        }
        assert_eq!(cb.state(), CircuitState::Closed);
        // Success resets consecutive counter.
        cb.record_success_at(now + Duration::from_millis(50));
        // 4 more failures: consecutive counter back to 4 (below 5).
        // But total window: 8F + 1S = rate 8/9 > 0.5 → trips on rate.
        // So we need the old events to be outside window.
        // Use t > window (60s) so old events are pruned.
        let later = now + Duration::from_secs(61);
        cb.record_success_at(later);
        for i in 0..4 {
            cb.record_failure_at("err", later + Duration::from_millis(10 + i * 10));
        }
        // 4 consecutive (< 5), rate = 4F / 5total < 0.5 threshold (exactly 0.8 > 0.5).
        // Actually 4F + 1S = 4/5 = 0.8 ≥ 0.5 → still trips.
        // We need more successes to dilute the rate.
        // Let's just verify that consecutive count was reset:
        // After success, 4 more failures should not trip by consecutive count alone.
        // For a clean test, use a short window with fresh data.
        let cb2 = CircuitBreaker::new(CircuitBreakerConfig {
            consecutive_fail_threshold: 5,
            failure_rate_threshold: 0.5,
            window_duration: Duration::from_secs(10),
        });
        let t = Instant::now();
        for i in 0..4 {
            cb2.record_failure_at("err", t + Duration::from_millis(i * 10));
        }
        assert_eq!(cb2.state(), CircuitState::Closed);
        cb2.record_success_at(t + Duration::from_millis(50));
        // Wait for old failures to fall out of the 10s window.
        let t2 = t + Duration::from_secs(11);
        // Now add 4 failures again — consecutive counter was reset so these are 1..4.
        for i in 0..4 {
            cb2.record_failure_at("err", t2 + Duration::from_millis(i * 10));
        }
        // Only 4 consecutive (< 5) and rate = 4/4 but total < 5, so no trip.
        assert_eq!(cb2.state(), CircuitState::Closed);
    }

    #[test]
    fn failure_rate_trips_breaker() {
        let cb = default_cb();
        let now = Instant::now();
        // 3 failures, 2 successes interleaved → rate = 3/5 = 0.6 > 0.5.
        cb.record_failure_at("e", now);
        cb.record_success_at(now + Duration::from_millis(1));
        cb.record_failure_at("e", now + Duration::from_millis(2));
        cb.record_success_at(now + Duration::from_millis(3));
        cb.record_failure_at("e", now + Duration::from_millis(4));
        assert!(matches!(cb.state(), CircuitState::Open { .. }));
    }

    #[test]
    fn state_gauge_values() {
        let cb = default_cb();
        assert_eq!(cb.state_gauge(), 0.0); // Closed.
        let now = Instant::now();
        for i in 0..5 {
            cb.record_failure_at("err", now + Duration::from_millis(i * 10));
        }
        assert_eq!(cb.state_gauge(), 2.0); // Open.
        cb.check_at(now + Duration::from_secs(31)).unwrap();
        assert_eq!(cb.state_gauge(), 1.0); // HalfOpen.
    }

    #[test]
    fn events_outside_window_are_pruned() {
        let cb = CircuitBreaker::new(CircuitBreakerConfig {
            window_duration: Duration::from_secs(10),
            ..Default::default()
        });
        let now = Instant::now();
        // 3 failures at t=0.
        for i in 0..3 {
            cb.record_failure_at("e", now + Duration::from_millis(i));
        }
        // 2 successes at t=11s (outside window). Old failures pruned.
        let later = now + Duration::from_secs(11);
        cb.record_success_at(later);
        cb.record_success_at(later + Duration::from_millis(1));
        // State should still be Closed since old failures were pruned.
        assert_eq!(cb.state(), CircuitState::Closed);
    }
}
