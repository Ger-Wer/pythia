//! sniper-orderbook: Polymarket L2 order-book tracker.
//!
//! # Status
//!
//! **Milestone 2 — live Polymarket adapters (gate #10).** This crate exposes:
//!
//! * Data types ([`OrderBook`], [`BookSnapshot`], [`BookUpdate`], [`PriceLevel`]).
//! * The synchronization state machine ([`TrackerState`], [`TrackerEvent`],
//!   [`Tracker`]) that orchestrates a strict REST-snapshot + WS-incremental
//!   join, with sequence-number based reconciliation.
//! * Live Polymarket REST + WebSocket adapters under [`polymarket`] (behind
//!   the default-on `polymarket-live` feature).
//! * `OrderBook::is_crossed()` + tracker warn-only crossed-book detection,
//!   per the M2 brief: a crossed top-of-book is transient HFT noise, not a
//!   degrade condition; we log + count it and wait for the next delta.
//!
//! # Architecture
//!
//! Polymarket's order book API (and most exchange feeds in general) is
//! eventually consistent: REST snapshots are stale by the time you parse
//! them, and WS deltas can start arriving before you've fetched the
//! snapshot. To get a deterministic local book you have to bridge the two
//! streams precisely:
//!
//! 1. Open the WS subscription first.
//! 2. **Buffer** every incoming WS delta in memory, do not apply yet.
//! 3. Issue the REST snapshot request.
//! 4. When the snapshot arrives, throw away every buffered delta whose
//!    sequence is `<= snapshot.sequence`.
//! 5. Apply the remaining buffered deltas (in order) to the snapshot.
//! 6. Verify there is no sequence gap between the last buffered delta and
//!    the next live delta. If there is, the local book is degraded and we
//!    must re-snapshot.
//!
//! That dance is encoded as a state machine:
//!
//! ```text
//! Connecting ──ws_connected──► BufferingWs ──snapshot_requested──► FetchingSnapshot
//!                                                                       │
//!                                                                snapshot_received
//!                                                                       ▼
//!                                                                    Syncing
//!                                                                       │
//!                                                          buffer_drained_no_gap
//!                                                                       ▼
//!                                                                    Tracking
//! ```
//!
//! Plus two terminal-ish states:
//!
//! * [`TrackerState::Degraded`] — gap detected, must re-snapshot. Re-entered
//!   from any post-snapshot state when a sequence discontinuity shows up.
//! * [`TrackerState::Stopped`] — terminal, no further transitions.
//!
//! # Wire conventions
//!
//! All prices and sizes are integer fixed-point in micros (1_000_000 = $1.00
//! or 1.0 share), matching `dual_loop.v1.common.proto`'s `Money` and `*_ppm`
//! convention. No `f64`/`f32` anywhere on hot path — IEEE-754 rounding ruins
//! sequence reconciliation. See [`PriceLevel`].
//!
//! Sequence numbers are host-monotonic `u64`. Polymarket's WS contract
//! does **not** guarantee `+1` increments, so the host assigns sequence
//! on WS-message receive (see [`polymarket::SequenceCursor`]); the
//! [`OrderBook::apply_update`] state machine then enforces continuity
//! downstream.

pub mod book;
pub mod driver;
pub mod publish;
pub mod state;
pub mod tracker;

#[cfg(feature = "polymarket-live")]
pub mod polymarket;

pub use book::{BookSide, BookSnapshot, BookUpdate, OrderBook, PriceLevel};
pub use publish::{
    NoopPublisher, OrderBookPublisher, OrderBookSnapshotView, PublishError,
    TOPIC as ORDERBOOK_UPDATED_TOPIC,
};
pub use state::{TrackerEvent, TrackerState, TransitionError};
pub use tracker::{Tracker, TrackerConfig};
