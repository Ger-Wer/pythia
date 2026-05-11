//! sniper-orderbook: Polymarket L2 order-book tracker.
//!
//! # Status
//!
//! **Milestone 1 — skeleton (gate #10).** This crate exposes:
//!
//! * Data types ([`OrderBook`], [`BookSnapshot`], [`BookUpdate`], [`PriceLevel`]).
//! * The synchronization state machine ([`TrackerState`], [`TrackerEvent`],
//!   [`Tracker`]) that orchestrates a strict REST-snapshot + WS-incremental
//!   join, with sequence-number based reconciliation.
//!
//! It does **not** include live Polymarket REST or WebSocket clients —
//! those land in a follow-up PR. The point of this milestone is to ship a
//! deterministic, fully unit-tested state machine that the network layer
//! can drive.
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
//! Sequence numbers are exchange-monotonic `u64`. We assume strict +1
//! increments on the WS feed (Polymarket's convention) and treat any
//! non-contiguous sequence as a gap.
//!
//! # Non-goals (milestone 1)
//!
//! * No live network IO. No `reqwest`. No `tokio-tungstenite`.
//! * No persistence. No Redis writes. No bus publishes.
//! * No metric histograms (counters are stubbed via `metrics::counter!`,
//!   visible once a binary host is wired in).
//! * No tracker manager / multi-market driver. That comes once the
//!   single-market state machine has been validated end-to-end.

pub mod book;
pub mod state;
pub mod tracker;

pub use book::{BookSide, BookSnapshot, BookUpdate, OrderBook, PriceLevel};
pub use state::{TrackerEvent, TrackerState, TransitionError};
pub use tracker::{Tracker, TrackerConfig};
