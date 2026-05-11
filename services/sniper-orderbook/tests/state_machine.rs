//! Integration tests for the full tracker + state machine.
//!
//! These tests exercise [`Tracker`] through its public API and assert
//! end-to-end invariants (e.g. "host saw the book transition through
//! exactly these states", "buffer was drained correctly", "degrade →
//! reconnect → re-snapshot lands you back in Tracking with a clean
//! book"). They complement the unit tests embedded in the source files.

use sniper_orderbook::{
    BookSide, BookSnapshot, BookUpdate, PriceLevel, Tracker, TrackerConfig, TrackerState,
};

fn mk_update(seq: u64, side: BookSide, price: i64, size: i64) -> BookUpdate {
    BookUpdate {
        sequence: seq,
        side,
        level: PriceLevel::new(price, size),
        received_at_unix_ms: 0,
    }
}

fn mk_snap_full(seq: u64) -> BookSnapshot {
    BookSnapshot {
        sequence: seq,
        bids: vec![
            PriceLevel::new(450_000, 5_000_000),
            PriceLevel::new(449_000, 10_000_000),
        ],
        asks: vec![
            PriceLevel::new(460_000, 5_000_000),
            PriceLevel::new(461_000, 7_000_000),
        ],
        taken_at_unix_ms: 1_700_000_000_000,
    }
}

#[test]
fn full_lifecycle_recovers_from_disconnect() {
    let mut t = Tracker::new(TrackerConfig::default());

    // -- Bring up.
    t.on_ws_connected();
    t.on_ws_message(mk_update(10, BookSide::Bid, 451_000, 1_000_000));
    t.request_snapshot();
    t.on_ws_message(mk_update(101, BookSide::Bid, 452_000, 9_000_000));
    let r = t.on_snapshot_received(mk_snap_full(100));
    assert_eq!(r.new_state, TrackerState::Tracking);
    assert_eq!(t.book().bid_depth(), 3); // 450k, 449k, 452k (10 + 101)
    assert_eq!(t.book().last_sequence(), Some(101));

    // -- Live deltas applied directly.
    let r = t.on_ws_message(mk_update(102, BookSide::Ask, 460_000, 0));
    assert_eq!(r.new_state, TrackerState::Tracking);
    assert_eq!(t.book().ask_depth(), 1);
    assert_eq!(t.book().last_sequence(), Some(102));

    // -- WS drops.
    let r = t.on_ws_disconnected();
    assert!(matches!(r.new_state, TrackerState::Degraded { .. }));
    assert!(!t.book().is_initialized());

    // -- Host reconnects.
    let r = t.on_ws_connected();
    assert_eq!(r.new_state, TrackerState::BufferingWs);

    // -- Recovery snapshot.
    t.request_snapshot();
    let r = t.on_snapshot_received(mk_snap_full(200));
    assert_eq!(r.new_state, TrackerState::Tracking);
    assert_eq!(t.book().last_sequence(), Some(200));
    assert_eq!(t.book().bid_depth(), 2);
    assert_eq!(t.book().ask_depth(), 2);
}

#[test]
fn re_snapshot_in_tracking_does_not_lose_book_continuity() {
    let mut t = Tracker::new(TrackerConfig::default());
    t.on_ws_connected();
    t.request_snapshot();
    t.on_snapshot_received(mk_snap_full(100));
    t.on_ws_message(mk_update(101, BookSide::Bid, 449_500, 1));
    assert_eq!(t.state(), &TrackerState::Tracking);

    // Operator re-snapshots.
    t.request_snapshot();
    // A few deltas arrive during the fetch.
    t.on_ws_message(mk_update(150, BookSide::Ask, 461_500, 1));
    let r = t.on_snapshot_received(mk_snap_full(150));
    // Buffered delta seq=150 is stale (== snapshot.sequence) and dropped.
    assert_eq!(r.new_state, TrackerState::Tracking);
    assert_eq!(t.book().last_sequence(), Some(150));
}

#[test]
fn multiple_degrades_are_idempotent_and_recoverable() {
    let mut t = Tracker::new(TrackerConfig::default());

    for _ in 0..3 {
        t.on_ws_connected();
        t.on_ws_disconnected();
        assert!(matches!(t.state(), TrackerState::Degraded { .. }));
    }

    // Final clean recovery.
    t.on_ws_connected();
    t.request_snapshot();
    t.on_snapshot_received(mk_snap_full(1));
    assert_eq!(t.state(), &TrackerState::Tracking);
}

#[test]
fn shutdown_from_degraded_is_terminal() {
    let mut t = Tracker::new(TrackerConfig::default());
    t.on_ws_connected();
    t.on_ws_disconnected();
    let r = t.shutdown();
    assert_eq!(r.new_state, TrackerState::Stopped);

    // Nothing else moves the state.
    t.on_ws_connected();
    t.request_snapshot();
    t.on_snapshot_received(mk_snap_full(1));
    assert_eq!(t.state(), &TrackerState::Stopped);
}
