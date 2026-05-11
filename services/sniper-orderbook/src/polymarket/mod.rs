//! Polymarket CLOB integration: live REST snapshot client, WS subscriber,
//! and the wire-format converters that bridge them to the canonical
//! [`OrderBook`] / [`Tracker`] state machine.
//!
//! See module docs in each sub-module for endpoint / schema details.
//!
//! ## Sequence semantics
//!
//! Polymarket does not deliver a strict `+1` sequence per WS frame.
//! Every host that drives a [`Tracker`] against Polymarket must
//! therefore assign its own monotonic sequence on receive
//! ([`SequenceCursor`] below). The exchange `timestamp` is preserved
//! on each delta so the snapshot/buffer reconcile can drop stale
//! entries by *exchange time* rather than by host sequence (which is
//! meaningless across a snapshot refetch).

pub mod rest;
pub mod wire;
pub mod ws;

pub use rest::{RestClient, RestError, DEFAULT_REST_BASE_URL};
pub use wire::{
    parse_polymarket_timestamp, parse_price_micros, parse_side, parse_size_micros,
    rest_book_to_snapshot, ws_book_to_snapshot, ws_price_change_to_update, RestBookResponse,
    RestPriceLevel, WireError, WsBookEvent, WsPriceChange, WsPriceChangeEvent,
};
pub use ws::{parse_event, WsError, WsEvent, WsSubscriber, DEFAULT_WS_URL};

/// Strictly-monotonic host-side sequence cursor. Polymarket does not
/// guarantee `+1` sequence per WS frame; the host assigns its own
/// counter on receive so the [`OrderBook::apply_update`] state machine
/// can still enforce continuity downstream.
#[derive(Debug, Default, Clone, Copy)]
pub struct SequenceCursor {
    next: u64,
}

impl SequenceCursor {
    /// New cursor starting at 1 (so a snapshot with `sequence=0` makes
    /// the first delta `seq=1` valid as `last+1`).
    pub fn new() -> Self {
        Self { next: 1 }
    }

    /// Yield the next sequence and advance. Named `take_next` (rather
    /// than `next`) so it doesn't get confused with `Iterator::next`.
    pub fn take_next(&mut self) -> u64 {
        let v = self.next;
        self.next += 1;
        v
    }

    /// Peek the next value that would be returned by [`Self::take_next`].
    pub fn peek(&self) -> u64 {
        self.next
    }

    /// Reset to 1. Used after a Degraded → Connecting transition so
    /// every fresh WS connection starts a new sequence space.
    pub fn reset(&mut self) {
        self.next = 1;
    }
}
