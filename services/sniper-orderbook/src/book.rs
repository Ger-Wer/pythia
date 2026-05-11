//! Order-book data types.
//!
//! Everything is integer fixed-point (micros). See module-level docs in
//! [`crate`] for the wire convention.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Side of an order-book level.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BookSide {
    /// Bid side — buyers. Sorted descending by price (best = highest).
    Bid,
    /// Ask side — sellers. Sorted ascending by price (best = lowest).
    Ask,
}

/// A single price level: price (micros) and the *total resting size* at
/// that price after the most recent update.
///
/// A `size_micros` of `0` means the level was wiped (delete). Producers
/// must emit explicit zeros on cancellation rather than dropping the
/// level silently — the tracker uses zero to remove entries from its
/// `BTreeMap` representation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PriceLevel {
    /// Price in micros. For Polymarket YES/NO this is in [0, 1_000_000].
    pub price_micros: i64,
    /// Total resting size in micros (1_000_000 = 1 share).
    pub size_micros: i64,
}

impl PriceLevel {
    /// Convenience constructor.
    #[inline]
    pub const fn new(price_micros: i64, size_micros: i64) -> Self {
        Self {
            price_micros,
            size_micros,
        }
    }

    /// Whether this level represents an explicit removal.
    #[inline]
    pub const fn is_zero(&self) -> bool {
        self.size_micros == 0
    }
}

/// A single incremental WS update.
///
/// One update touches one side of one market at one price.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BookUpdate {
    /// Monotonic exchange-side sequence number. Strictly `+1` between
    /// consecutive updates on the same market.
    pub sequence: u64,
    pub side: BookSide,
    pub level: PriceLevel,
    /// Wall-clock arrival timestamp at the host. Used for observability,
    /// never for ordering.
    pub received_at_unix_ms: i64,
}

/// A full snapshot fetched from REST.
///
/// Contains the absolute book state as of the exchange's view at
/// `taken_at_unix_ms`, plus the *sequence cursor* — the largest WS
/// sequence number that has already been applied to this snapshot
/// server-side. Any buffered WS delta with `sequence <= this` is
/// redundant and must be dropped; any with `sequence == this + 1` is
/// the immediate continuation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BookSnapshot {
    pub sequence: u64,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub taken_at_unix_ms: i64,
}

/// In-memory order book.
///
/// Bids/asks are kept as `BTreeMap<price_micros, size_micros>`. Reading
/// the best bid/ask is `O(log n)` worst case but in practice extremely
/// fast at exchange depth (a few hundred levels).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OrderBook {
    bids: BTreeMap<i64, i64>,
    asks: BTreeMap<i64, i64>,
    /// Last applied sequence number. Snapshot defines the starting
    /// value; every subsequent applied delta increments by 1.
    last_sequence: Option<u64>,
}

impl OrderBook {
    /// Construct an empty book.
    pub fn new() -> Self {
        Self::default()
    }

    /// Whether any snapshot or update has been applied yet.
    #[inline]
    pub fn is_initialized(&self) -> bool {
        self.last_sequence.is_some()
    }

    /// Reset the book to empty (used on degrade → re-snapshot).
    pub fn clear(&mut self) {
        self.bids.clear();
        self.asks.clear();
        self.last_sequence = None;
    }

    /// Apply a REST snapshot. Resets any prior state.
    pub fn apply_snapshot(&mut self, snap: &BookSnapshot) {
        self.bids.clear();
        self.asks.clear();
        for lvl in &snap.bids {
            if !lvl.is_zero() {
                self.bids.insert(lvl.price_micros, lvl.size_micros);
            }
        }
        for lvl in &snap.asks {
            if !lvl.is_zero() {
                self.asks.insert(lvl.price_micros, lvl.size_micros);
            }
        }
        self.last_sequence = Some(snap.sequence);
    }

    /// Apply a single incremental update.
    ///
    /// Returns an error if `update.sequence` is not exactly
    /// `last_sequence + 1` (gap detected).
    pub fn apply_update(&mut self, update: &BookUpdate) -> Result<(), ApplyError> {
        match self.last_sequence {
            None => return Err(ApplyError::NotInitialized),
            Some(last) => {
                let expected = last.saturating_add(1);
                if update.sequence != expected {
                    return Err(ApplyError::SequenceGap {
                        expected,
                        got: update.sequence,
                    });
                }
            }
        }
        let side = match update.side {
            BookSide::Bid => &mut self.bids,
            BookSide::Ask => &mut self.asks,
        };
        if update.level.is_zero() {
            side.remove(&update.level.price_micros);
        } else {
            side.insert(update.level.price_micros, update.level.size_micros);
        }
        self.last_sequence = Some(update.sequence);
        Ok(())
    }

    /// Highest bid (price, size) if any.
    pub fn best_bid(&self) -> Option<PriceLevel> {
        self.bids
            .iter()
            .next_back()
            .map(|(p, s)| PriceLevel::new(*p, *s))
    }

    /// Lowest ask (price, size) if any.
    pub fn best_ask(&self) -> Option<PriceLevel> {
        self.asks
            .iter()
            .next()
            .map(|(p, s)| PriceLevel::new(*p, *s))
    }

    /// Mid-price in micros, if both sides have liquidity.
    pub fn mid_micros(&self) -> Option<i64> {
        let bid = self.best_bid()?.price_micros;
        let ask = self.best_ask()?.price_micros;
        Some((bid + ask) / 2)
    }

    /// Spread (ask − bid) in micros, if both sides have liquidity.
    pub fn spread_micros(&self) -> Option<i64> {
        let bid = self.best_bid()?.price_micros;
        let ask = self.best_ask()?.price_micros;
        Some(ask - bid)
    }

    /// Last applied sequence number.
    #[inline]
    pub fn last_sequence(&self) -> Option<u64> {
        self.last_sequence
    }

    /// Number of bid price levels.
    #[inline]
    pub fn bid_depth(&self) -> usize {
        self.bids.len()
    }

    /// Number of ask price levels.
    #[inline]
    pub fn ask_depth(&self) -> usize {
        self.asks.len()
    }
}

/// Errors returned by [`OrderBook::apply_update`].
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ApplyError {
    /// Update arrived before any snapshot was applied.
    #[error("order book has no snapshot yet; cannot apply update")]
    NotInitialized,
    /// Sequence number was not `last_sequence + 1`.
    #[error("sequence gap detected: expected {expected}, got {got}")]
    SequenceGap { expected: u64, got: u64 },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apply_snapshot_resets_state() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&BookSnapshot {
            sequence: 100,
            bids: vec![
                PriceLevel::new(450_000, 5_000_000),
                PriceLevel::new(440_000, 10_000_000),
            ],
            asks: vec![
                PriceLevel::new(460_000, 3_000_000),
                PriceLevel::new(470_000, 7_000_000),
            ],
            taken_at_unix_ms: 1_700_000_000_000,
        });

        assert_eq!(book.last_sequence(), Some(100));
        assert_eq!(book.bid_depth(), 2);
        assert_eq!(book.ask_depth(), 2);
        assert_eq!(book.best_bid(), Some(PriceLevel::new(450_000, 5_000_000)));
        assert_eq!(book.best_ask(), Some(PriceLevel::new(460_000, 3_000_000)));
        assert_eq!(book.spread_micros(), Some(10_000));
        assert_eq!(book.mid_micros(), Some(455_000));
    }

    #[test]
    fn snapshot_ignores_zero_size_levels() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&BookSnapshot {
            sequence: 1,
            bids: vec![
                PriceLevel::new(450_000, 5_000_000),
                PriceLevel::new(440_000, 0), // ignored
            ],
            asks: vec![],
            taken_at_unix_ms: 0,
        });
        assert_eq!(book.bid_depth(), 1);
    }

    #[test]
    fn apply_update_rejects_when_uninitialized() {
        let mut book = OrderBook::new();
        let err = book
            .apply_update(&BookUpdate {
                sequence: 1,
                side: BookSide::Bid,
                level: PriceLevel::new(100, 1),
                received_at_unix_ms: 0,
            })
            .unwrap_err();
        assert_eq!(err, ApplyError::NotInitialized);
    }

    #[test]
    fn apply_update_detects_sequence_gap() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&BookSnapshot {
            sequence: 100,
            bids: vec![],
            asks: vec![],
            taken_at_unix_ms: 0,
        });
        let err = book
            .apply_update(&BookUpdate {
                sequence: 102, // expected 101
                side: BookSide::Bid,
                level: PriceLevel::new(450_000, 5_000_000),
                received_at_unix_ms: 0,
            })
            .unwrap_err();
        assert_eq!(
            err,
            ApplyError::SequenceGap {
                expected: 101,
                got: 102
            }
        );
    }

    #[test]
    fn apply_update_zero_size_removes_level() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&BookSnapshot {
            sequence: 10,
            bids: vec![
                PriceLevel::new(450_000, 5_000_000),
                PriceLevel::new(440_000, 3_000_000),
            ],
            asks: vec![],
            taken_at_unix_ms: 0,
        });
        book.apply_update(&BookUpdate {
            sequence: 11,
            side: BookSide::Bid,
            level: PriceLevel::new(450_000, 0),
            received_at_unix_ms: 0,
        })
        .unwrap();
        assert_eq!(book.bid_depth(), 1);
        assert_eq!(book.best_bid(), Some(PriceLevel::new(440_000, 3_000_000)));
        assert_eq!(book.last_sequence(), Some(11));
    }

    #[test]
    fn apply_update_replaces_size_at_existing_price() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&BookSnapshot {
            sequence: 5,
            bids: vec![PriceLevel::new(450_000, 5_000_000)],
            asks: vec![],
            taken_at_unix_ms: 0,
        });
        book.apply_update(&BookUpdate {
            sequence: 6,
            side: BookSide::Bid,
            level: PriceLevel::new(450_000, 9_000_000),
            received_at_unix_ms: 0,
        })
        .unwrap();
        assert_eq!(book.best_bid(), Some(PriceLevel::new(450_000, 9_000_000)));
    }

    #[test]
    fn clear_resets_state() {
        let mut book = OrderBook::new();
        book.apply_snapshot(&BookSnapshot {
            sequence: 1,
            bids: vec![PriceLevel::new(1, 1)],
            asks: vec![PriceLevel::new(2, 1)],
            taken_at_unix_ms: 0,
        });
        book.clear();
        assert!(!book.is_initialized());
        assert_eq!(book.bid_depth(), 0);
        assert_eq!(book.ask_depth(), 0);
    }

    #[test]
    fn best_bid_ask_empty_book_returns_none() {
        let book = OrderBook::new();
        assert_eq!(book.best_bid(), None);
        assert_eq!(book.best_ask(), None);
        assert_eq!(book.spread_micros(), None);
        assert_eq!(book.mid_micros(), None);
    }
}
