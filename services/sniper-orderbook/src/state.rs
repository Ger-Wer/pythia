//! State machine for the synchronization dance.
//!
//! This module is intentionally side-effect free. Given a state and an
//! event, [`transition`] returns the next state and an [`Action`] the
//! caller should perform (e.g. "fetch a snapshot"). The caller (the
//! [`Tracker`](crate::Tracker)) owns all IO and buffer manipulation.
//!
//! The point of keeping transitions pure is so they can be exhaustively
//! unit-tested without any IO mocks.

use serde::{Deserialize, Serialize};

/// High-level state of the tracker, exactly as documented in the
/// architectural ASCII diagram in [`crate`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TrackerState {
    /// Initial state; the WS connection has not been established yet.
    Connecting,
    /// WS is connected and we are buffering deltas into memory while we
    /// wait for the operator (or `Tracker::run` loop) to fire the
    /// snapshot request.
    BufferingWs,
    /// REST snapshot request is in flight. We are still buffering WS
    /// deltas; once the snapshot lands we reconcile.
    FetchingSnapshot,
    /// Snapshot has been applied; we are now draining the in-memory
    /// delta buffer, applying any updates that came after the snapshot's
    /// `sequence`.
    Syncing,
    /// Steady-state: every WS delta is being applied immediately to the
    /// in-memory book.
    Tracking,
    /// Something went wrong (sequence gap, WS disconnect, snapshot
    /// failure). The caller should clear the book and trigger a
    /// re-snapshot. `reason` is a stable identifier intended for
    /// metric labels.
    Degraded { reason: DegradeReason },
    /// Terminal: shutdown was requested. No further transitions.
    Stopped,
}

/// Stable identifier for the reason a tracker entered [`TrackerState::Degraded`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DegradeReason {
    /// The WS feed dropped while we were past `Connecting`.
    WsDisconnected,
    /// We tried to fetch the REST snapshot and it failed or timed out.
    SnapshotFailed,
    /// A WS delta arrived with a non-contiguous sequence number.
    SequenceGap,
    /// The internal buffer grew past its configured cap; we drop the
    /// connection rather than risk applying stale or out-of-order data.
    BufferOverflow,
}

/// Input events to the state machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TrackerEvent {
    /// WS handshake completed successfully.
    WsConnected,
    /// WS connection dropped.
    WsDisconnected,
    /// A WS delta arrived. We do not validate it here — the [`Tracker`]
    /// owns the buffer and decides what to do.
    WsMessage,
    /// `Tracker::request_snapshot` was called.
    SnapshotRequested,
    /// REST snapshot fetch returned successfully.
    SnapshotReceived,
    /// REST snapshot fetch failed (network error, parse error, etc).
    SnapshotFailed,
    /// We finished draining the buffer after a snapshot apply and the
    /// next live WS delta was contiguous.
    SyncCompleted,
    /// While in `Tracking` we saw a sequence gap.
    SequenceGapDetected,
    /// Buffer hit its hard cap.
    BufferOverflowed,
    /// Graceful shutdown signal.
    Shutdown,
}

/// Side-effect the caller should perform after a successful
/// transition. `None` for any [`TransitionError`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    /// Nothing to do; just update state.
    None,
    /// Start fetching a REST snapshot now. The tracker should schedule
    /// the request immediately.
    RequestSnapshot,
    /// Buffer the incoming WS message.
    BufferDelta,
    /// Apply the incoming WS message directly to the book.
    ApplyDelta,
    /// Reconcile: drop stale buffered deltas (`seq <= snapshot.sequence`),
    /// then apply the rest in order. If reconcile reveals a gap, the
    /// tracker should feed a [`TrackerEvent::SequenceGapDetected`] back
    /// into the machine; otherwise feed [`TrackerEvent::SyncCompleted`].
    Reconcile,
    /// Clear the in-memory book and the delta buffer; the caller is
    /// expected to re-establish (typically `Connecting → BufferingWs →
    /// FetchingSnapshot`).
    ClearAndRecover,
    /// Drop all in-flight resources (WS, pending requests). Terminal.
    Shutdown,
}

/// Errors returned by [`transition`] when an event is not legal in the
/// current state.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum TransitionError {
    /// Event makes no sense in the current state. The tracker should
    /// log and ignore.
    #[error("invalid transition from {state:?} on event {event:?}")]
    Invalid {
        state: TrackerState,
        event: TrackerEvent,
    },
}

/// Pure transition function.
///
/// Returns the next state and the action the caller should perform.
/// Does not own any data; the caller is expected to keep the
/// [`OrderBook`](crate::OrderBook) and delta buffer separately.
pub fn transition(
    state: &TrackerState,
    event: &TrackerEvent,
) -> Result<(TrackerState, Action), TransitionError> {
    use Action as A;
    use TrackerEvent as E;
    use TrackerState as S;

    // Shutdown is accepted from any state — we just go to Stopped.
    if matches!(event, E::Shutdown) {
        return Ok((S::Stopped, A::Shutdown));
    }

    let result = match (state, event) {
        // --- Connecting ---
        (S::Connecting, E::WsConnected) => (S::BufferingWs, A::None),
        (S::Connecting, E::WsDisconnected) => (
            S::Degraded {
                reason: DegradeReason::WsDisconnected,
            },
            A::ClearAndRecover,
        ),

        // --- BufferingWs ---
        (S::BufferingWs, E::WsMessage) => (S::BufferingWs, A::BufferDelta),
        (S::BufferingWs, E::SnapshotRequested) => (S::FetchingSnapshot, A::RequestSnapshot),
        (S::BufferingWs, E::WsDisconnected) => (
            S::Degraded {
                reason: DegradeReason::WsDisconnected,
            },
            A::ClearAndRecover,
        ),
        (S::BufferingWs, E::BufferOverflowed) => (
            S::Degraded {
                reason: DegradeReason::BufferOverflow,
            },
            A::ClearAndRecover,
        ),

        // --- FetchingSnapshot ---
        // We keep buffering deltas while the snapshot is in flight.
        (S::FetchingSnapshot, E::WsMessage) => (S::FetchingSnapshot, A::BufferDelta),
        (S::FetchingSnapshot, E::SnapshotReceived) => (S::Syncing, A::Reconcile),
        (S::FetchingSnapshot, E::SnapshotFailed) => (
            S::Degraded {
                reason: DegradeReason::SnapshotFailed,
            },
            A::ClearAndRecover,
        ),
        (S::FetchingSnapshot, E::WsDisconnected) => (
            S::Degraded {
                reason: DegradeReason::WsDisconnected,
            },
            A::ClearAndRecover,
        ),
        (S::FetchingSnapshot, E::BufferOverflowed) => (
            S::Degraded {
                reason: DegradeReason::BufferOverflow,
            },
            A::ClearAndRecover,
        ),

        // --- Syncing ---
        // The tracker drives reconciliation synchronously and feeds a
        // result event back in.
        (S::Syncing, E::SyncCompleted) => (S::Tracking, A::None),
        (S::Syncing, E::SequenceGapDetected) => (
            S::Degraded {
                reason: DegradeReason::SequenceGap,
            },
            A::ClearAndRecover,
        ),
        (S::Syncing, E::WsMessage) => (S::Syncing, A::BufferDelta),
        (S::Syncing, E::WsDisconnected) => (
            S::Degraded {
                reason: DegradeReason::WsDisconnected,
            },
            A::ClearAndRecover,
        ),

        // --- Tracking ---
        (S::Tracking, E::WsMessage) => (S::Tracking, A::ApplyDelta),
        (S::Tracking, E::SequenceGapDetected) => (
            S::Degraded {
                reason: DegradeReason::SequenceGap,
            },
            A::ClearAndRecover,
        ),
        (S::Tracking, E::WsDisconnected) => (
            S::Degraded {
                reason: DegradeReason::WsDisconnected,
            },
            A::ClearAndRecover,
        ),
        // Operator-driven re-snapshot while live (e.g. periodic resync).
        (S::Tracking, E::SnapshotRequested) => (S::FetchingSnapshot, A::RequestSnapshot),

        // --- Degraded ---
        // Operator/loop drives recovery by issuing WsConnected after
        // re-establishing the connection.
        (S::Degraded { .. }, E::WsConnected) => (S::BufferingWs, A::None),

        // --- Stopped ---
        // Terminal; nothing accepted other than Shutdown (handled above).

        // Anything else is an invalid transition.
        _ => {
            return Err(TransitionError::Invalid {
                state: state.clone(),
                event: event.clone(),
            });
        }
    };
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Walk the canonical happy path end-to-end.
    #[test]
    fn happy_path_connecting_to_tracking() {
        let mut state = TrackerState::Connecting;

        let (next, act) = transition(&state, &TrackerEvent::WsConnected).unwrap();
        assert_eq!(next, TrackerState::BufferingWs);
        assert_eq!(act, Action::None);
        state = next;

        let (next, act) = transition(&state, &TrackerEvent::WsMessage).unwrap();
        assert_eq!(next, TrackerState::BufferingWs);
        assert_eq!(act, Action::BufferDelta);
        state = next;

        let (next, act) = transition(&state, &TrackerEvent::SnapshotRequested).unwrap();
        assert_eq!(next, TrackerState::FetchingSnapshot);
        assert_eq!(act, Action::RequestSnapshot);
        state = next;

        let (next, act) = transition(&state, &TrackerEvent::WsMessage).unwrap();
        assert_eq!(next, TrackerState::FetchingSnapshot);
        assert_eq!(act, Action::BufferDelta);
        state = next;

        let (next, act) = transition(&state, &TrackerEvent::SnapshotReceived).unwrap();
        assert_eq!(next, TrackerState::Syncing);
        assert_eq!(act, Action::Reconcile);
        state = next;

        let (next, act) = transition(&state, &TrackerEvent::SyncCompleted).unwrap();
        assert_eq!(next, TrackerState::Tracking);
        assert_eq!(act, Action::None);
        state = next;

        let (next, act) = transition(&state, &TrackerEvent::WsMessage).unwrap();
        assert_eq!(next, TrackerState::Tracking);
        assert_eq!(act, Action::ApplyDelta);
    }

    #[test]
    fn shutdown_is_accepted_from_any_state() {
        for state in [
            TrackerState::Connecting,
            TrackerState::BufferingWs,
            TrackerState::FetchingSnapshot,
            TrackerState::Syncing,
            TrackerState::Tracking,
            TrackerState::Degraded {
                reason: DegradeReason::WsDisconnected,
            },
        ] {
            let (next, act) = transition(&state, &TrackerEvent::Shutdown).unwrap();
            assert_eq!(next, TrackerState::Stopped);
            assert_eq!(act, Action::Shutdown);
        }
    }

    #[test]
    fn ws_disconnect_degrades_from_any_live_state() {
        for state in [
            TrackerState::Connecting,
            TrackerState::BufferingWs,
            TrackerState::FetchingSnapshot,
            TrackerState::Syncing,
            TrackerState::Tracking,
        ] {
            let (next, act) = transition(&state, &TrackerEvent::WsDisconnected).unwrap();
            assert!(matches!(
                next,
                TrackerState::Degraded {
                    reason: DegradeReason::WsDisconnected,
                }
            ));
            assert_eq!(act, Action::ClearAndRecover);
        }
    }

    #[test]
    fn sequence_gap_degrades_from_syncing_and_tracking() {
        for state in [TrackerState::Syncing, TrackerState::Tracking] {
            let (next, act) = transition(&state, &TrackerEvent::SequenceGapDetected).unwrap();
            assert!(matches!(
                next,
                TrackerState::Degraded {
                    reason: DegradeReason::SequenceGap,
                }
            ));
            assert_eq!(act, Action::ClearAndRecover);
        }
    }

    #[test]
    fn snapshot_failure_degrades() {
        let (next, act) = transition(
            &TrackerState::FetchingSnapshot,
            &TrackerEvent::SnapshotFailed,
        )
        .unwrap();
        assert!(matches!(
            next,
            TrackerState::Degraded {
                reason: DegradeReason::SnapshotFailed,
            }
        ));
        assert_eq!(act, Action::ClearAndRecover);
    }

    #[test]
    fn degraded_recovers_on_reconnect() {
        let (next, act) = transition(
            &TrackerState::Degraded {
                reason: DegradeReason::SequenceGap,
            },
            &TrackerEvent::WsConnected,
        )
        .unwrap();
        assert_eq!(next, TrackerState::BufferingWs);
        assert_eq!(act, Action::None);
    }

    #[test]
    fn tracking_supports_periodic_resnapshot() {
        let (next, act) =
            transition(&TrackerState::Tracking, &TrackerEvent::SnapshotRequested).unwrap();
        assert_eq!(next, TrackerState::FetchingSnapshot);
        assert_eq!(act, Action::RequestSnapshot);
    }

    #[test]
    fn invalid_transitions_are_rejected() {
        // SnapshotReceived in Tracking is meaningless (we'd have to be
        // in FetchingSnapshot to expect it).
        let err = transition(&TrackerState::Tracking, &TrackerEvent::SnapshotReceived).unwrap_err();
        assert!(matches!(err, TransitionError::Invalid { .. }));

        // SyncCompleted while Connecting is invalid.
        let err = transition(&TrackerState::Connecting, &TrackerEvent::SyncCompleted).unwrap_err();
        assert!(matches!(err, TransitionError::Invalid { .. }));

        // Stopped accepts nothing but Shutdown.
        let err = transition(&TrackerState::Stopped, &TrackerEvent::WsConnected).unwrap_err();
        assert!(matches!(err, TransitionError::Invalid { .. }));
    }

    #[test]
    fn buffer_overflow_degrades_only_in_buffering_states() {
        for state in [TrackerState::BufferingWs, TrackerState::FetchingSnapshot] {
            let (next, act) = transition(&state, &TrackerEvent::BufferOverflowed).unwrap();
            assert!(matches!(
                next,
                TrackerState::Degraded {
                    reason: DegradeReason::BufferOverflow,
                }
            ));
            assert_eq!(act, Action::ClearAndRecover);
        }
        // Not legal in Tracking — we'd never buffer there.
        let err = transition(&TrackerState::Tracking, &TrackerEvent::BufferOverflowed).unwrap_err();
        assert!(matches!(err, TransitionError::Invalid { .. }));
    }
}
