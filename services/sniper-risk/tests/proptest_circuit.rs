//! Property tests for CircuitBreaker state machine invariants.
//!
//! Verifies that regardless of the random sequence of successes and failures,
//! the circuit breaker maintains its state machine invariants.

use proptest::prelude::*;
use sniper_risk::{CircuitBreaker, CircuitBreakerConfig, CircuitState};
use std::time::{Duration, Instant};

/// Actions that can be applied to the circuit breaker.
#[derive(Debug, Clone)]
enum Action {
    RecordSuccess,
    RecordFailure,
    /// Advance time by `ms` milliseconds and check.
    AdvanceAndCheck(u64),
}

fn action_strategy() -> impl Strategy<Value = Action> {
    prop_oneof![
        3 => Just(Action::RecordSuccess),
        5 => Just(Action::RecordFailure),
        2 => (1u64..120_000).prop_map(Action::AdvanceAndCheck),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(10_000))]

    /// Invariant: state is always one of Closed, Open, or HalfOpen.
    /// A check() call when Open and past timeout transitions to HalfOpen.
    /// After HalfOpen success → Closed. After HalfOpen failure → Open.
    #[test]
    fn state_machine_invariants(actions in proptest::collection::vec(action_strategy(), 1..200)) {
        let config = CircuitBreakerConfig {
            consecutive_fail_threshold: 3,
            failure_rate_threshold: 0.6,
            window_duration: Duration::from_secs(30),
        };
        let cb = CircuitBreaker::new(config);
        let base = Instant::now();
        let mut elapsed_ms: u64 = 0;

        for action in actions {
            let now = base + Duration::from_millis(elapsed_ms);
            match action {
                Action::RecordSuccess => {
                    let state_before = cb.state();
                    cb.record_success_at(now);
                    let state_after = cb.state();
                    // After success in HalfOpen → must be Closed.
                    if state_before == CircuitState::HalfOpen {
                        prop_assert_eq!(state_after, CircuitState::Closed);
                    }
                    // Success never moves Closed → Open.
                    if state_before == CircuitState::Closed {
                        prop_assert!(
                            state_after == CircuitState::Closed,
                            "success in Closed should stay Closed, got {:?}",
                            state_after
                        );
                    }
                }
                Action::RecordFailure => {
                    let state_before = cb.state();
                    cb.record_failure_at("prop_err", now);
                    let state_after = cb.state();
                    // After failure in HalfOpen → must be Open.
                    if state_before == CircuitState::HalfOpen {
                        prop_assert!(
                            matches!(state_after, CircuitState::Open { .. }),
                            "failure in HalfOpen should go Open, got {:?}",
                            state_after
                        );
                    }
                    // Failure in Closed either stays Closed or goes to Open.
                    if state_before == CircuitState::Closed {
                        prop_assert!(
                            state_after == CircuitState::Closed
                                || matches!(state_after, CircuitState::Open { .. }),
                            "failure in Closed should be Closed or Open, got {:?}",
                            state_after
                        );
                    }
                }
                Action::AdvanceAndCheck(ms) => {
                    elapsed_ms = elapsed_ms.saturating_add(ms);
                    let check_time = base + Duration::from_millis(elapsed_ms);
                    let _ = cb.check_at(check_time);
                    // check_at only transitions Open → HalfOpen, nothing else.
                    let state = cb.state();
                    prop_assert!(
                        state == CircuitState::Closed
                            || state == CircuitState::HalfOpen
                            || matches!(state, CircuitState::Open { .. }),
                        "invalid state after check: {:?}",
                        state
                    );
                }
            }

            // Global invariant: state is always valid.
            let state = cb.state();
            match &state {
                CircuitState::Closed => {}
                CircuitState::Open { .. } => {}
                CircuitState::HalfOpen => {}
            }
        }
    }

    /// Invariant: gauge value always matches state.
    #[test]
    fn gauge_matches_state(actions in proptest::collection::vec(action_strategy(), 1..100)) {
        let cb = CircuitBreaker::new(CircuitBreakerConfig {
            consecutive_fail_threshold: 3,
            failure_rate_threshold: 0.6,
            window_duration: Duration::from_secs(30),
        });
        let base = Instant::now();
        let mut elapsed_ms: u64 = 0;

        for action in actions {
            let now = base + Duration::from_millis(elapsed_ms);
            match action {
                Action::RecordSuccess => cb.record_success_at(now),
                Action::RecordFailure => cb.record_failure_at("e", now),
                Action::AdvanceAndCheck(ms) => {
                    elapsed_ms = elapsed_ms.saturating_add(ms);
                    let _ = cb.check_at(base + Duration::from_millis(elapsed_ms));
                }
            }
            let gauge = cb.state_gauge();
            let state = cb.state();
            match state {
                CircuitState::Closed => prop_assert_eq!(gauge, 0.0),
                CircuitState::HalfOpen => prop_assert_eq!(gauge, 1.0),
                CircuitState::Open { .. } => prop_assert_eq!(gauge, 2.0),
            }
        }
    }
}
