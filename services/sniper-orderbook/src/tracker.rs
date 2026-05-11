//! Tracker — owns the [`OrderBook`], the WS delta buffer, and the
//! current [`TrackerState`], and drives the synchronization state
//! machine.
//!
//! The tracker itself does **no IO**. The host (a future
//! `sniper-orderbook` binary or an embedded module inside
//! `sniper-executor`) is responsible for:
//!
//! * Opening the WebSocket and feeding each incoming delta via
//!   [`Tracker::on_ws_message`].
//! * Issuing the REST snapshot request when
//!   [`Action::RequestSnapshot`] is returned, and feeding the result
//!   into [`Tracker::on_snapshot_received`] or
//!   [`Tracker::on_snapshot_failed`].
//! * Listening to WS connection state and feeding
//!   [`Tracker::on_ws_connected`] / [`Tracker::on_ws_disconnected`].
//!
//! All decisions about *what* to do next are returned by the tracker
//! as a [`TickResult`] so the host has no business logic of its own.

use crate::book::{ApplyError, BookSnapshot, BookUpdate, OrderBook};
use crate::state::{transition, Action, DegradeReason, TrackerEvent, TrackerState};

/// Configuration knobs for a [`Tracker`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TrackerConfig {
    /// Hard cap on the WS delta buffer. If exceeded while
    /// [`TrackerState::BufferingWs`] / [`TrackerState::FetchingSnapshot`],
    /// the tracker transitions to [`TrackerState::Degraded`] with reason
    /// [`DegradeReason::BufferOverflow`]. Production default is generous
    /// (10_000) because the snapshot fetch is fast (sub-second).
    pub max_buffer_len: usize,
}

impl Default for TrackerConfig {
    fn default() -> Self {
        Self {
            max_buffer_len: 10_000,
        }
    }
}

/// The result of feeding one event into the tracker. Surfaces both the
/// new state and what the host should do next.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TickResult {
    pub new_state: TrackerState,
    pub action: Action,
}

/// Owns the book, the delta buffer, and the state machine.
#[derive(Debug)]
pub struct Tracker {
    config: TrackerConfig,
    state: TrackerState,
    book: OrderBook,
    /// FIFO of WS deltas captured while not in `Tracking`. Each push
    /// also checks `max_buffer_len`; overflow degrades the tracker.
    buffer: Vec<BookUpdate>,
}

impl Tracker {
    pub fn new(config: TrackerConfig) -> Self {
        Self {
            config,
            state: TrackerState::Connecting,
            book: OrderBook::new(),
            buffer: Vec::new(),
        }
    }

    /// Read-only access to the current state.
    pub fn state(&self) -> &TrackerState {
        &self.state
    }

    /// Read-only access to the underlying book. The book is only
    /// meaningful in [`TrackerState::Tracking`]; in any other state it
    /// is a partial / stale view and the host MUST NOT use it for
    /// trading decisions.
    pub fn book(&self) -> &OrderBook {
        &self.book
    }

    /// Read-only access to the current buffer length, for metrics.
    pub fn buffer_len(&self) -> usize {
        self.buffer.len()
    }

    // -------------------- event entry points --------------------

    /// WS handshake completed.
    pub fn on_ws_connected(&mut self) -> TickResult {
        self.drive(&TrackerEvent::WsConnected)
    }

    /// WS dropped. Degrades the tracker.
    pub fn on_ws_disconnected(&mut self) -> TickResult {
        self.drive(&TrackerEvent::WsDisconnected)
    }

    /// A WS delta arrived. Returns the action the host should take.
    ///
    /// In [`TrackerState::Tracking`] we attempt to apply the update
    /// immediately; a sequence gap will internally fire
    /// [`TrackerEvent::SequenceGapDetected`] and degrade.
    pub fn on_ws_message(&mut self, update: BookUpdate) -> TickResult {
        // First, transition based on the WsMessage event.
        let TickResult { new_state, action } = self.drive(&TrackerEvent::WsMessage);

        match action {
            Action::BufferDelta => {
                self.buffer.push(update);
                if self.buffer.len() > self.config.max_buffer_len {
                    return self.drive(&TrackerEvent::BufferOverflowed);
                }
                TickResult { new_state, action }
            }
            Action::ApplyDelta => match self.book.apply_update(&update) {
                Ok(()) => {
                    self.observe_crossed_if_any();
                    TickResult { new_state, action }
                }
                Err(ApplyError::SequenceGap { .. }) => {
                    self.drive(&TrackerEvent::SequenceGapDetected)
                }
                Err(ApplyError::NotInitialized) => {
                    // Should be unreachable in Tracking, but if it
                    // happens, treat as gap-equivalent.
                    self.drive(&TrackerEvent::SequenceGapDetected)
                }
            },
            _ => TickResult { new_state, action },
        }
    }

    /// Host called: request a snapshot. Transitions to
    /// [`TrackerState::FetchingSnapshot`] from
    /// [`TrackerState::BufferingWs`] / [`TrackerState::Tracking`].
    pub fn request_snapshot(&mut self) -> TickResult {
        self.drive(&TrackerEvent::SnapshotRequested)
    }

    /// Host called: snapshot fetched. The tracker reconciles immediately:
    /// drops every buffered delta with `sequence <= snapshot.sequence`,
    /// applies the rest. If a sequence gap is detected mid-reconcile the
    /// tracker degrades.
    pub fn on_snapshot_received(&mut self, snap: BookSnapshot) -> TickResult {
        // Move state to Syncing (action: Reconcile).
        let r = self.drive(&TrackerEvent::SnapshotReceived);
        if !matches!(r.new_state, TrackerState::Syncing) {
            return r;
        }

        // Apply snapshot.
        self.book.apply_snapshot(&snap);

        // Reconcile buffer.
        // 1. Drop stale: anything with seq <= snap.sequence.
        // 2. Apply remaining in seq order.
        // The buffer is FIFO in arrival order, which for Polymarket-style
        // strictly-monotonic feeds equals seq order. We still sort
        // defensively to survive any reordering bug in the WS host.
        let mut pending: Vec<BookUpdate> = self
            .buffer
            .drain(..)
            .filter(|u| u.sequence > snap.sequence)
            .collect();
        pending.sort_by_key(|u| u.sequence);

        for u in pending {
            match self.book.apply_update(&u) {
                Ok(()) => self.observe_crossed_if_any(),
                Err(ApplyError::SequenceGap { .. }) | Err(ApplyError::NotInitialized) => {
                    return self.drive(&TrackerEvent::SequenceGapDetected);
                }
            }
        }

        // Sync done.
        self.drive(&TrackerEvent::SyncCompleted)
    }

    /// Host called: snapshot fetch failed.
    pub fn on_snapshot_failed(&mut self) -> TickResult {
        self.drive(&TrackerEvent::SnapshotFailed)
    }

    /// Shut the tracker down. Terminal.
    pub fn shutdown(&mut self) -> TickResult {
        self.drive(&TrackerEvent::Shutdown)
    }

    // -------------------- internals --------------------

    /// Detect (best_bid >= best_ask) after a delta apply. Crossed books
    /// are a normal transient anomaly on hybrid CLOB venues like
    /// Polymarket: log + count, do not degrade or panic.
    fn observe_crossed_if_any(&mut self) {
        if !self.book.is_crossed() {
            return;
        }
        let (bid, ask) = match (self.book.best_bid(), self.book.best_ask()) {
            (Some(b), Some(a)) => (b, a),
            _ => return,
        };
        metrics::counter!("sniper_orderbook_crossed_observed_total").increment(1);
        tracing::warn!(
            best_bid_micros = bid.price_micros,
            best_bid_size_micros = bid.size_micros,
            best_ask_micros = ask.price_micros,
            best_ask_size_micros = ask.size_micros,
            "crossed book observed (transient); awaiting next delta to restore spread",
        );
    }

    fn drive(&mut self, event: &TrackerEvent) -> TickResult {
        match transition(&self.state, event) {
            Ok((next, action)) => {
                // Side-effects driven directly by state transitions:
                // ClearAndRecover means we drop both the book and the
                // buffer so the host can re-establish from scratch.
                if matches!(action, Action::ClearAndRecover | Action::Shutdown) {
                    self.book.clear();
                    self.buffer.clear();
                }
                metrics::counter!(
                    "sniper_orderbook_transitions_total",
                    "to" => state_label(&next),
                )
                .increment(1);
                self.state = next.clone();
                TickResult {
                    new_state: next,
                    action,
                }
            }
            Err(_) => {
                // Invalid transitions are intentionally ignored at the
                // tracker level (the state machine considers them
                // no-ops). The host can detect them by observing that
                // the state did not change.
                metrics::counter!(
                    "sniper_orderbook_invalid_events_total",
                    "state" => state_label(&self.state),
                )
                .increment(1);
                TickResult {
                    new_state: self.state.clone(),
                    action: Action::None,
                }
            }
        }
    }
}

fn state_label(state: &TrackerState) -> &'static str {
    match state {
        TrackerState::Connecting => "connecting",
        TrackerState::BufferingWs => "buffering_ws",
        TrackerState::FetchingSnapshot => "fetching_snapshot",
        TrackerState::Syncing => "syncing",
        TrackerState::Tracking => "tracking",
        TrackerState::Degraded {
            reason: DegradeReason::WsDisconnected,
        } => "degraded_ws_disconnected",
        TrackerState::Degraded {
            reason: DegradeReason::SnapshotFailed,
        } => "degraded_snapshot_failed",
        TrackerState::Degraded {
            reason: DegradeReason::SequenceGap,
        } => "degraded_sequence_gap",
        TrackerState::Degraded {
            reason: DegradeReason::BufferOverflow,
        } => "degraded_buffer_overflow",
        TrackerState::Stopped => "stopped",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::book::{BookSide, PriceLevel};

    fn mk_update(seq: u64, side: BookSide, price: i64, size: i64) -> BookUpdate {
        BookUpdate {
            sequence: seq,
            side,
            level: PriceLevel::new(price, size),
            received_at_unix_ms: 0,
        }
    }

    fn mk_snapshot(seq: u64) -> BookSnapshot {
        BookSnapshot {
            sequence: seq,
            bids: vec![PriceLevel::new(450_000, 5_000_000)],
            asks: vec![PriceLevel::new(460_000, 5_000_000)],
            taken_at_unix_ms: 0,
        }
    }

    #[test]
    fn end_to_end_happy_path_book_is_correct() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        // Two deltas arrive while buffering.
        t.on_ws_message(mk_update(98, BookSide::Bid, 449_000, 1_000_000));
        t.on_ws_message(mk_update(99, BookSide::Bid, 448_000, 2_000_000));
        t.request_snapshot();
        // A third delta arrives during the snapshot fetch.
        t.on_ws_message(mk_update(101, BookSide::Bid, 451_000, 7_000_000));
        // Snapshot arrives with seq=100. Deltas 98, 99 are stale and
        // must be dropped; delta 101 (seq == snap.sequence + 1) is the
        // continuation.
        let r = t.on_snapshot_received(mk_snapshot(100));
        assert_eq!(r.new_state, TrackerState::Tracking);
        assert_eq!(t.book().last_sequence(), Some(101));
        // Bid side now has 3 levels: 450_000 from snapshot + 451_000 from
        // the live continuation + 450_000 remains.
        assert_eq!(t.book().bid_depth(), 2);
        assert_eq!(
            t.book().best_bid(),
            Some(PriceLevel::new(451_000, 7_000_000))
        );

        // Now in Tracking, deltas apply immediately.
        let r = t.on_ws_message(mk_update(102, BookSide::Ask, 460_000, 0));
        assert_eq!(r.new_state, TrackerState::Tracking);
        assert_eq!(t.book().ask_depth(), 0);
    }

    #[test]
    fn reconcile_drops_stale_buffered_deltas() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.on_ws_message(mk_update(1, BookSide::Bid, 400_000, 1));
        t.on_ws_message(mk_update(2, BookSide::Bid, 400_000, 2));
        t.request_snapshot();
        t.on_ws_message(mk_update(51, BookSide::Ask, 600_000, 5));

        let r = t.on_snapshot_received(mk_snapshot(50));
        assert_eq!(r.new_state, TrackerState::Tracking);
        // Only the seq=51 delta survived the reconcile.
        assert_eq!(t.book().last_sequence(), Some(51));
        // Snapshot put one ask at 460_000; delta 51 added another at 600_000.
        // Best ask is the lower one (snapshot's), depth should be 2.
        assert_eq!(t.book().ask_depth(), 2);
        assert_eq!(t.book().best_ask().map(|l| l.price_micros), Some(460_000));
    }

    #[test]
    fn reconcile_detects_gap_and_degrades() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.request_snapshot();
        // Buffered delta is 102, but snapshot.sequence = 100 — that's a gap
        // (we never saw 101).
        t.on_ws_message(mk_update(102, BookSide::Bid, 449_000, 1_000_000));
        let r = t.on_snapshot_received(mk_snapshot(100));
        assert!(matches!(
            r.new_state,
            TrackerState::Degraded {
                reason: DegradeReason::SequenceGap
            }
        ));
        // Book and buffer must be cleared.
        assert!(!t.book().is_initialized());
        assert_eq!(t.buffer_len(), 0);
    }

    #[test]
    fn live_gap_in_tracking_degrades() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.request_snapshot();
        t.on_snapshot_received(mk_snapshot(100));
        assert_eq!(t.state(), &TrackerState::Tracking);

        // seq=102 should have been 101.
        let r = t.on_ws_message(mk_update(102, BookSide::Bid, 449_000, 1));
        assert!(matches!(
            r.new_state,
            TrackerState::Degraded {
                reason: DegradeReason::SequenceGap
            }
        ));
        assert!(!t.book().is_initialized());
    }

    #[test]
    fn ws_disconnect_in_tracking_degrades_and_clears() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.request_snapshot();
        t.on_snapshot_received(mk_snapshot(50));
        assert_eq!(t.state(), &TrackerState::Tracking);

        let r = t.on_ws_disconnected();
        assert!(matches!(
            r.new_state,
            TrackerState::Degraded {
                reason: DegradeReason::WsDisconnected
            }
        ));
        assert!(!t.book().is_initialized());
    }

    #[test]
    fn snapshot_failure_degrades_and_keeps_buffer_empty() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.on_ws_message(mk_update(1, BookSide::Bid, 100, 1));
        t.request_snapshot();
        let r = t.on_snapshot_failed();
        assert!(matches!(
            r.new_state,
            TrackerState::Degraded {
                reason: DegradeReason::SnapshotFailed
            }
        ));
        assert_eq!(t.buffer_len(), 0);
    }

    #[test]
    fn buffer_overflow_degrades() {
        let mut t = Tracker::new(TrackerConfig { max_buffer_len: 3 });
        t.on_ws_connected();
        t.on_ws_message(mk_update(1, BookSide::Bid, 100, 1));
        t.on_ws_message(mk_update(2, BookSide::Bid, 101, 1));
        t.on_ws_message(mk_update(3, BookSide::Bid, 102, 1));
        // 4th push exceeds cap.
        let r = t.on_ws_message(mk_update(4, BookSide::Bid, 103, 1));
        assert!(matches!(
            r.new_state,
            TrackerState::Degraded {
                reason: DegradeReason::BufferOverflow
            }
        ));
        assert_eq!(t.buffer_len(), 0);
    }

    #[test]
    fn recovery_after_degrade_returns_to_buffering() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.on_ws_disconnected();
        assert!(matches!(t.state(), TrackerState::Degraded { .. }));

        // Host re-opens WS and calls connected again.
        let r = t.on_ws_connected();
        assert_eq!(r.new_state, TrackerState::BufferingWs);
    }

    #[test]
    fn shutdown_is_terminal_and_clears_state() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.request_snapshot();
        t.on_snapshot_received(mk_snapshot(10));
        assert_eq!(t.state(), &TrackerState::Tracking);
        let r = t.shutdown();
        assert_eq!(r.new_state, TrackerState::Stopped);
        assert!(!t.book().is_initialized());
        assert_eq!(t.buffer_len(), 0);

        // Any further event other than shutdown is a no-op (stays
        // Stopped); we report it as such.
        let r = t.on_ws_connected();
        assert_eq!(r.new_state, TrackerState::Stopped);
        assert_eq!(r.action, Action::None);
    }

    #[test]
    fn periodic_resnapshot_from_tracking() {
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.request_snapshot();
        t.on_snapshot_received(mk_snapshot(10));
        assert_eq!(t.state(), &TrackerState::Tracking);

        // Re-snapshot mid-flight.
        let r = t.request_snapshot();
        assert_eq!(r.new_state, TrackerState::FetchingSnapshot);

        // New snapshot lands.
        let r = t.on_snapshot_received(mk_snapshot(20));
        assert_eq!(r.new_state, TrackerState::Tracking);
        assert_eq!(t.book().last_sequence(), Some(20));
    }

    #[test]
    fn crossed_book_in_tracking_does_not_degrade() {
        // Seed Tracking with a normal book (bid 450k, ask 460k).
        let mut t = Tracker::new(TrackerConfig::default());
        t.on_ws_connected();
        t.request_snapshot();
        let r = t.on_snapshot_received(mk_snapshot(100));
        assert_eq!(r.new_state, TrackerState::Tracking);
        assert!(!t.book().is_crossed());

        // Apply a delta that pushes best_bid above best_ask. The
        // Tracker must log + count it but stay in Tracking — no
        // degrade, no panic, no state transition.
        let r = t.on_ws_message(mk_update(101, BookSide::Bid, 470_000, 1_000_000));
        assert_eq!(r.new_state, TrackerState::Tracking);
        assert!(t.book().is_crossed());
        assert_eq!(t.book().last_sequence(), Some(101));

        // The very next delta restores the spread — Tracker remains
        // in Tracking, crossed flag clears.
        let r = t.on_ws_message(mk_update(102, BookSide::Bid, 470_000, 0));
        assert_eq!(r.new_state, TrackerState::Tracking);
        assert!(!t.book().is_crossed());
    }
}
