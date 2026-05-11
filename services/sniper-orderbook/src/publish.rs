//! M3 — bus publisher skeleton.
//!
//! This module declares the **shape** of order-book publication for
//! `sniper-risk` and other downstream consumers. The actual NATS wiring
//! is intentionally left to the host (a future sniper-orderbook driver
//! binary) — this module is contract-only:
//!
//! * [`OrderBookSnapshotView`] — derived top-of-book + top-N liquidity
//!   view, ready to be encoded as `dual_loop.v1.OrderBookUpdate` proto.
//! * [`OrderBookPublisher`] — async trait with a single `publish` method,
//!   so hosts can plug in real NATS, in-memory channel, or [`NoopPublisher`]
//!   for tests.
//! * [`NoopPublisher`] — drops events on the floor; lets the rest of the
//!   tracker compile cleanly without a wired bus.
//!
//! M3 (next session) will:
//! 1. Add a `derive_view(&OrderBook, ...) -> OrderBookSnapshotView` helper.
//! 2. Call that derive + `publisher.publish(...)` from `Tracker` after each
//!    successful delta apply (rate-limited).
//! 3. Provide a `NatsPublisher` that maps the view to the generated
//!    `dual_loop_common::proto::orderbook::OrderBookUpdate` and publishes
//!    on [`TOPIC`].
//!
//! See `schemas/dual_loop/v1/orderbook.proto` for the wire schema.

use std::future::Future;

/// NATS subject this crate publishes on. Mirrors
/// [`dual_loop_common::topics::ORDERBOOK_UPDATED`]; we re-export here so
/// driver binaries don't have to round-trip through the common crate for
/// a single constant.
pub use dual_loop_common::topics::ORDERBOOK_UPDATED as TOPIC;

/// Derived top-of-book view, ready to be encoded into a wire payload.
/// Integer fixed-point everywhere — `*_micros` follow `Money.amount_micros`
/// semantics (1_000_000 = $1.00, see `common.proto`).
///
/// `liquidity_depth` documents the N used to compute
/// `liquidity_bid_micros` / `liquidity_ask_micros`. Typically 3 for
/// top-of-book health checks; the publishing host decides.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OrderBookSnapshotView {
    /// Polymarket conditionId / canonical market key. Empty if unknown.
    pub market_specific_key: String,
    /// Polymarket tokenId / asset_id this book belongs to. Required to
    /// disambiguate YES vs NO sides of a binary market — both share the
    /// same conditionId.
    pub asset_id: String,
    /// Best bid in micros, or 0 if the bid side is empty.
    pub best_bid_micros: i64,
    /// Best ask in micros, or 0 if the ask side is empty.
    pub best_ask_micros: i64,
    /// (best_bid + best_ask) / 2, or 0 if either side is empty.
    pub mid_micros: i64,
    /// (best_ask - best_bid) * 10_000 / mid. Signed; negative on crossed.
    pub spread_bps: i64,
    /// Sum of size_micros across the top `liquidity_depth` bid levels.
    pub liquidity_bid_micros: i64,
    /// Sum of size_micros across the top `liquidity_depth` ask levels.
    pub liquidity_ask_micros: i64,
    /// The N used to compute the liquidity sums.
    pub liquidity_depth: u32,
    /// best_bid >= best_ask at publish time. Consumers should NOT degrade
    /// their own state on this flag.
    pub crossed: bool,
    /// Wall-clock time the publishing host applied the producing delta.
    pub timestamp_ms: i64,
    /// Host-monotonic sequence; mirrors `OrderBook::last_sequence()`.
    pub last_sequence: u64,
}

/// Error returned by [`OrderBookPublisher::publish`]. Stays opaque-ish
/// for now: M3 will plug in a concrete NATS error variant.
#[derive(Debug, thiserror::Error)]
pub enum PublishError {
    /// Underlying transport (NATS, channel, etc.) refused or failed to
    /// deliver the event. The publisher decides retry semantics.
    #[error("transport: {0}")]
    Transport(String),
    /// Encoding the view into the wire payload failed.
    #[error("encode: {0}")]
    Encode(String),
}

/// Skeleton publisher trait. M3 will provide a real `NatsPublisher`
/// implementation; for now hosts can use [`NoopPublisher`].
///
/// The trait is intentionally minimal — single async `publish` method.
/// Implementors are responsible for any rate-limiting, batching, or
/// retry behavior they need; this trait does not prescribe it.
pub trait OrderBookPublisher: Send + Sync {
    /// Publish a single derived view onto the bus. Returning `Ok(())`
    /// means the publisher has accepted ownership of the event; it does
    /// not necessarily mean the event has been durably persisted.
    fn publish(
        &self,
        view: &OrderBookSnapshotView,
    ) -> impl Future<Output = Result<(), PublishError>> + Send;
}

/// Default no-op publisher. Drops every event silently. Lets the rest
/// of the tracker compile and unit-test without a wired bus.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoopPublisher;

impl OrderBookPublisher for NoopPublisher {
    async fn publish(&self, _view: &OrderBookSnapshotView) -> Result<(), PublishError> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn topic_constant_matches_canonical_source() {
        assert_eq!(TOPIC, "dl.v1.orderbook.updated");
        assert_eq!(TOPIC, dual_loop_common::topics::ORDERBOOK_UPDATED);
    }

    #[tokio::test]
    #[cfg(feature = "polymarket-live")]
    async fn noop_publisher_accepts_view_and_returns_ok() {
        let view = OrderBookSnapshotView {
            market_specific_key: "0xabc".to_owned(),
            asset_id: "1234".to_owned(),
            best_bid_micros: 13_000,
            best_ask_micros: 14_000,
            mid_micros: 13_500,
            spread_bps: 740,
            liquidity_bid_micros: 2_456_000,
            liquidity_ask_micros: 151_000,
            liquidity_depth: 3,
            crossed: false,
            timestamp_ms: 1_700_000_000_000,
            last_sequence: 42,
        };
        let publisher = NoopPublisher;
        publisher.publish(&view).await.expect("noop never fails");
    }
}
