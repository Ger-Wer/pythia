//! Exponential backoff and circuit breaker for fault tolerance.
//!
//! BackoffState tracks failure history and implements exponential backoff
//! with circuit breaker logic to prevent infinite retry loops.

use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct BackoffState {
    pub failures: u32,
    pub last_attempt: Instant,
    pub next_retry: Duration,
    pub circuit_open: bool,
}

impl Default for BackoffState {
    fn default() -> Self {
        Self {
            failures: 0,
            last_attempt: Instant::now(),
            next_retry: Duration::from_secs(0),
            circuit_open: false,
        }
    }
}

impl BackoffState {
    pub fn record_failure(&mut self, max_duration: Duration) {
        self.last_attempt = Instant::now();
        self.failures += 1;
        
        // Exponential backoff: 2^(failures-1) seconds, capped at max_duration
        let backoff_secs = 1u64 << (self.failures.saturating_sub(1).min(31));
        let backoff = Duration::from_secs(backoff_secs);
        self.next_retry = backoff.min(max_duration);
    }

    pub fn record_success(&mut self) {
        self.failures = 0;
        self.next_retry = Duration::from_secs(0);
        self.circuit_open = false;
    }

    pub fn is_circuit_open(&self, threshold: u32) -> bool {
        self.failures >= threshold
    }

    pub fn can_retry_now(&self) -> bool {
        self.last_attempt.elapsed() >= self.next_retry
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread::sleep;

    #[test]
    fn test_exponential_backoff_progression() {
        let mut state = BackoffState::default();
        let max_duration = Duration::from_secs(32);

        // Initial state: 0 failures, can retry immediately
        assert_eq!(state.failures, 0);
        assert_eq!(state.next_retry, Duration::from_secs(0));
        assert!(state.can_retry_now());

        // 1st failure: 1s
        state.record_failure(max_duration);
        assert_eq!(state.failures, 1);
        assert_eq!(state.next_retry, Duration::from_secs(1));

        // 2nd failure: 2s
        state.record_failure(max_duration);
        assert_eq!(state.failures, 2);
        assert_eq!(state.next_retry, Duration::from_secs(2));

        // 3rd failure: 4s
        state.record_failure(max_duration);
        assert_eq!(state.failures, 3);
        assert_eq!(state.next_retry, Duration::from_secs(4));

        // 4th failure: 8s
        state.record_failure(max_duration);
        assert_eq!(state.failures, 4);
        assert_eq!(state.next_retry, Duration::from_secs(8));

        // 5th failure: 16s
        state.record_failure(max_duration);
        assert_eq!(state.failures, 5);
        assert_eq!(state.next_retry, Duration::from_secs(16));

        // 6th failure: 32s (capped at max_duration)
        state.record_failure(max_duration);
        assert_eq!(state.failures, 6);
        assert_eq!(state.next_retry, Duration::from_secs(32));

        // 7th failure: still 32s (capped)
        state.record_failure(max_duration);
        assert_eq!(state.failures, 7);
        assert_eq!(state.next_retry, Duration::from_secs(32));
    }

    #[test]
    fn test_circuit_breaker() {
        let mut state = BackoffState::default();
        let max_duration = Duration::from_secs(32);
        let threshold = 5;

        // Initial state: circuit is closed
        assert!(!state.is_circuit_open(threshold));

        // 1st-4th failures: circuit still closed
        for i in 1..=4 {
            state.record_failure(max_duration);
            assert_eq!(state.failures, i);
            assert!(!state.is_circuit_open(threshold));
        }

        // 5th failure: circuit opens
        state.record_failure(max_duration);
        assert_eq!(state.failures, 5);
        assert!(state.is_circuit_open(threshold));

        // More failures: circuit remains open
        state.record_failure(max_duration);
        assert_eq!(state.failures, 6);
        assert!(state.is_circuit_open(threshold));

        // Success: circuit closes and resets
        state.record_success();
        assert_eq!(state.failures, 0);
        assert!(!state.is_circuit_open(threshold));
        assert_eq!(state.next_retry, Duration::from_secs(0));
        assert!(!state.circuit_open);
    }

    #[test]
    fn test_retry_timing() {
        let mut state = BackoffState::default();
        let max_duration = Duration::from_secs(32);

        // Initial state: can retry immediately
        assert!(state.can_retry_now());

        // Record failure with 1s backoff
        state.record_failure(max_duration);
        assert_eq!(state.next_retry, Duration::from_secs(1));
        
        // Immediately after failure: cannot retry yet
        assert!(!state.can_retry_now());

        // Wait for backoff duration to pass
        sleep(Duration::from_millis(1100));
        
        // After backoff duration: can retry now
        assert!(state.can_retry_now());

        // Record another failure with 2s backoff
        state.record_failure(max_duration);
        assert_eq!(state.next_retry, Duration::from_secs(2));
        
        // Immediately after: cannot retry
        assert!(!state.can_retry_now());

        // Success resets: can retry immediately
        state.record_success();
        assert!(state.can_retry_now());
    }
}
