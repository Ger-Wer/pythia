//! M3 — bus publisher: contract + BBO-diff filter.
//!
//! Two collaborating concepts:
//!
//! * [`OrderBookSnapshotView`] — derived top-of-book + top-N liquidity
//!   view, ready to be encoded as `dual_loop.v1.OrderBookUpdate` proto.
//!   [`OrderBookSnapshotView::derive`] turns an [`OrderBook`] into a
//!   view; it is empty-safe (no panic on missing sides) and computes
//!   `spread_bps` in signed integer basis points off mid.
//! * [`OrderBookPublisher`] — async trait with a single `publish`
//!   method. [`NoopPublisher`] drops events (default for tests),
//!   [`nats::NatsPublisher`] is the production impl over JetStream.
//!
//! The actual *when-to-publish* policy lives on
//! [`crate::Tracker::publish_if_bbo_changed`]: only republish when
//! the top of book (best bid price + size, best ask price + size)
//! changes. Depth-only updates do not trigger publication — they
//! don't affect snipers' slippage assumptions.
//!
//! See `schemas/dual_loop/v1/orderbook.proto` for the wire schema.

use std::future::Future;

use crate::book::OrderBook;

pub mod nats;

/// NATS subject this crate publishes on. Mirrors
/// [`dual_loop_common::topics::ORDERBOOK_UPDATED`]; re-exported so driver
/// binaries don't have to round-trip through the common crate for a
/// single constant.
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
    /// 0 if either side is empty.
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
    /// Host-monotonic sequence; mirrors `OrderBook::last_sequence()`,
    /// or 0 if the book has not been initialized yet.
    pub last_sequence: u64,
}

impl OrderBookSnapshotView {
    /// Build a view from the current order-book state.
    ///
    /// Empty-safe: if a side has no liquidity, `best_*_micros`, `mid`,
    /// and `spread_bps` are 0 (rather than panicking or wrapping).
    /// `spread_bps` is computed in signed integer bps off mid and
    /// remains negative on a crossed book.
    pub fn derive(
        book: &OrderBook,
        market_specific_key: impl Into<String>,
        asset_id: impl Into<String>,
        liquidity_depth: u32,
        timestamp_ms: i64,
    ) -> Self {
        let best_bid = book.best_bid();
        let best_ask = book.best_ask();
        let best_bid_micros = best_bid.map(|l| l.price_micros).unwrap_or(0);
        let best_ask_micros = best_ask.map(|l| l.price_micros).unwrap_or(0);

        let (mid_micros, spread_bps) = match (best_bid, best_ask) {
            (Some(b), Some(a)) => {
                let mid = (b.price_micros + a.price_micros) / 2;
                if mid == 0 {
                    (0, 0)
                } else {
                    // Signed; negative on crossed because (ask - bid) < 0.
                    let bps = (a.price_micros.saturating_sub(b.price_micros))
                        .saturating_mul(10_000)
                        / mid;
                    (mid, bps)
                }
            }
            _ => (0, 0),
        };

        let depth = liquidity_depth as usize;
        let liquidity_bid_micros: i64 = book.top_bids(depth).iter().map(|l| l.size_micros).sum();
        let liquidity_ask_micros: i64 = book.top_asks(depth).iter().map(|l| l.size_micros).sum();

        Self {
            market_specific_key: market_specific_key.into(),
            asset_id: asset_id.into(),
            best_bid_micros,
            best_ask_micros,
            mid_micros,
            spread_bps,
            liquidity_bid_micros,
            liquidity_ask_micros,
            liquidity_depth,
            crossed: book.is_crossed(),
            timestamp_ms,
            last_sequence: book.last_sequence().unwrap_or(0),
        }
    }
}

/// Best-bid / best-offer fingerprint. Used by
/// [`crate::Tracker::publish_if_bbo_changed`] to decide whether the
/// derived view materially changed from the last publication.
///
/// We compare price *and* size on both sides — a size change on the
/// best level still affects slippage assumptions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(crate) struct Bbo {
    pub best_bid_price_micros: i64,
    pub best_bid_size_micros: i64,
    pub best_ask_price_micros: i64,
    pub best_ask_size_micros: i64,
}

impl Bbo {
    pub(crate) fn from_book(book: &OrderBook) -> Self {
        let bid = book.best_bid();
        let ask = book.best_ask();
        Self {
            best_bid_price_micros: bid.map(|l| l.price_micros).unwrap_or(0),
            best_bid_size_micros: bid.map(|l| l.size_micros).unwrap_or(0),
            best_ask_price_micros: ask.map(|l| l.price_micros).unwrap_or(0),
            best_ask_size_micros: ask.map(|l| l.size_micros).unwrap_or(0),
        }
    }
}

/// Error returned by [`OrderBookPublisher::publish`].
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

/// Async publisher trait. Single `publish` method on a `&self` receiver
/// — implementors are responsible for any internal synchronisation,
/// batching, retry, or rate limiting.
///
/// The trait uses RPITIT (`fn publish() -> impl Future + Send`) so it
/// is `Send` everywhere and does not pull in the `async_trait` macro.
pub trait OrderBookPublisher: Send + Sync {
    /// Publish a single derived view. Returning `Ok(())` means the
    /// publisher has accepted ownership of the event; it does not
    /// necessarily mean the event has been durably persisted.
    fn publish(
        &self,
        view: &OrderBookSnapshotView,
    ) -> impl Future<Output = Result<(), PublishError>> + Send;
}

/// No-op publisher. Drops every event silently. Default for tests and
/// for hosts that haven't wired a real bus client yet.
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
    use crate::book::{BookSnapshot, PriceLevel};

    fn book_with(bids: &[(i64, i64)], asks: &[(i64, i64)], sequence: u64) -> OrderBook {
        let mut b = OrderBook::new();
        b.apply_snapshot(&BookSnapshot {
            sequence,
            bids: bids.iter().map(|(p, s)| PriceLevel::new(*p, *s)).collect(),
            asks: asks.iter().map(|(p, s)| PriceLevel::new(*p, *s)).collect(),
            taken_at_unix_ms: 1_700_000_000_000,
        });
        b
    }

    #[test]
    fn topic_constant_matches_canonical_source() {
        assert_eq!(TOPIC, "dl.v1.orderbook.updated");
        assert_eq!(TOPIC, dual_loop_common::topics::ORDERBOOK_UPDATED);
    }

    // -------------------- derive: empty / one-sided --------------------

    #[test]
    fn derive_from_empty_book_yields_zeros_no_panic() {
        let book = OrderBook::new();
        let view = OrderBookSnapshotView::derive(&book, "0xabc", "tok", 3, 42);
        assert_eq!(view.best_bid_micros, 0);
        assert_eq!(view.best_ask_micros, 0);
        assert_eq!(view.mid_micros, 0);
        assert_eq!(view.spread_bps, 0);
        assert_eq!(view.liquidity_bid_micros, 0);
        assert_eq!(view.liquidity_ask_micros, 0);
        assert_eq!(view.liquidity_depth, 3);
        assert!(!view.crossed);
        assert_eq!(view.last_sequence, 0);
        assert_eq!(view.market_specific_key, "0xabc");
        assert_eq!(view.asset_id, "tok");
        assert_eq!(view.timestamp_ms, 42);
    }

    #[test]
    fn derive_with_only_bids_keeps_ask_and_spread_zero() {
        let book = book_with(&[(13_000, 100_000), (12_500, 50_000)], &[], 7);
        let view = OrderBookSnapshotView::derive(&book, "k", "a", 3, 0);
        assert_eq!(view.best_bid_micros, 13_000);
        assert_eq!(view.best_ask_micros, 0);
        assert_eq!(view.mid_micros, 0);
        assert_eq!(view.spread_bps, 0);
        assert_eq!(view.liquidity_bid_micros, 150_000);
        assert_eq!(view.liquidity_ask_micros, 0);
        assert_eq!(view.last_sequence, 7);
    }

    #[test]
    fn derive_with_only_asks_keeps_bid_and_spread_zero() {
        let book = book_with(&[], &[(14_000, 80_000)], 9);
        let view = OrderBookSnapshotView::derive(&book, "k", "a", 3, 0);
        assert_eq!(view.best_bid_micros, 0);
        assert_eq!(view.best_ask_micros, 14_000);
        assert_eq!(view.mid_micros, 0);
        assert_eq!(view.spread_bps, 0);
        assert_eq!(view.liquidity_bid_micros, 0);
        assert_eq!(view.liquidity_ask_micros, 80_000);
    }

    // -------------------- derive: normal / crossed / depth --------------------

    #[test]
    fn derive_normal_book_computes_mid_and_bps() {
        // bid 0.013, ask 0.014, mid 0.0135, spread = 0.001
        // spread_bps = 1000 * 10000 / 13500 = 740 (integer trunc)
        let book = book_with(
            &[(13_000, 100_000), (12_500, 50_000), (12_000, 30_000)],
            &[(14_000, 80_000), (14_500, 60_000), (15_000, 40_000)],
            10,
        );
        let view = OrderBookSnapshotView::derive(&book, "0xabc", "1234", 3, 1_700_000_000_000);
        assert_eq!(view.best_bid_micros, 13_000);
        assert_eq!(view.best_ask_micros, 14_000);
        assert_eq!(view.mid_micros, 13_500);
        assert_eq!(view.spread_bps, 740);
        assert_eq!(view.liquidity_bid_micros, 180_000);
        assert_eq!(view.liquidity_ask_micros, 180_000);
        assert!(!view.crossed);
        assert_eq!(view.last_sequence, 10);
        assert_eq!(view.timestamp_ms, 1_700_000_000_000);
    }

    #[test]
    fn derive_crossed_book_flags_crossed_and_negative_bps() {
        // bid 0.015 > ask 0.013 (crossed). mid = (15000 + 13000)/2 = 14000.
        // spread_bps = (13000 - 15000) * 10000 / 14000 = -1428 (integer trunc).
        let book = book_with(&[(15_000, 100_000)], &[(13_000, 80_000)], 11);
        let view = OrderBookSnapshotView::derive(&book, "k", "a", 3, 0);
        assert!(view.crossed);
        assert!(view.spread_bps < 0);
        assert_eq!(view.spread_bps, -1428);
        assert_eq!(view.mid_micros, 14_000);
    }

    #[test]
    fn derive_clamps_liquidity_depth_to_available_levels() {
        // Only 2 levels on each side but caller asked depth=5: no panic,
        // sum across the levels that exist.
        let book = book_with(
            &[(13_000, 100_000), (12_500, 50_000)],
            &[(14_000, 80_000), (14_500, 60_000)],
            12,
        );
        let view = OrderBookSnapshotView::derive(&book, "k", "a", 5, 0);
        assert_eq!(view.liquidity_bid_micros, 150_000);
        assert_eq!(view.liquidity_ask_micros, 140_000);
        assert_eq!(view.liquidity_depth, 5);
    }

    // -------------------- Bbo fingerprint --------------------

    #[test]
    fn bbo_fingerprint_changes_on_price_or_size() {
        let book = book_with(&[(13_000, 100_000)], &[(14_000, 80_000)], 1);
        let bbo_a = Bbo::from_book(&book);

        // Price change on best bid → different.
        let book2 = book_with(&[(13_500, 100_000)], &[(14_000, 80_000)], 2);
        assert_ne!(bbo_a, Bbo::from_book(&book2));

        // Size change on best ask → different.
        let book3 = book_with(&[(13_000, 100_000)], &[(14_000, 70_000)], 3);
        assert_ne!(bbo_a, Bbo::from_book(&book3));

        // Same top + different depth levels → equal.
        let book4 = OrderBook::new();
        let view4 = book_with(
            &[(13_000, 100_000), (12_000, 999_999)],
            &[(14_000, 80_000)],
            4,
        );
        let _ = book4;
        let bbo_d = Bbo::from_book(&view4);
        let bbo_orig_only =
            Bbo::from_book(&book_with(&[(13_000, 100_000)], &[(14_000, 80_000)], 5));
        assert_eq!(bbo_d, bbo_orig_only);
    }

    #[test]
    fn bbo_empty_book_has_zeroed_fingerprint() {
        let bbo = Bbo::from_book(&OrderBook::new());
        assert_eq!(bbo, Bbo::default());
    }

    // -------------------- NoopPublisher --------------------

    #[tokio::test]
    async fn noop_publisher_accepts_view_and_returns_ok() {
        let view = OrderBookSnapshotView::derive(
            &book_with(&[(13_000, 100_000)], &[(14_000, 80_000)], 1),
            "0xabc",
            "tok",
            3,
            1_700_000_000_000,
        );
        let publisher = NoopPublisher;
        publisher.publish(&view).await.expect("noop never fails");
    }
}
