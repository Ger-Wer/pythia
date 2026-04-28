//! PositionStore: concurrent position tracking via DashMap.
//!
//! Subscribes to `dl.v1.exec.filled` and `dl.v1.exec.reverted` to maintain
//! a real-time view of open positions. Persists snapshots to Redis every 30s
//! for crash recovery.

use std::sync::Arc;

use dashmap::DashMap;
use parking_lot::RwLock;
use tracing::{debug, warn};

/// Unique market identifier (polymarket_id).
pub type MarketId = String;

/// A single open position.
#[derive(Debug, Clone)]
pub struct Position {
    pub side: Side,
    pub size_usd: i64,
    pub entry_price_micros: i64,
    pub opened_at_unix_ms: i64,
}

/// Side of the position.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Yes,
    No,
}

/// Thread-safe position store backed by DashMap.
#[derive(Debug, Clone)]
pub struct PositionStore {
    positions: Arc<DashMap<MarketId, Position>>,
    /// Cached total exposure for O(1) reads on hot path.
    total_exposure: Arc<RwLock<i64>>,
}

impl PositionStore {
    pub fn new() -> Self {
        Self {
            positions: Arc::new(DashMap::new()),
            total_exposure: Arc::new(RwLock::new(0)),
        }
    }

    /// Get position for a specific market.
    pub fn get(&self, market: &str) -> Option<Position> {
        self.positions.get(market).map(|r| r.value().clone())
    }

    /// Total USD exposure across all open positions.
    pub fn total_exposure_usd(&self) -> i64 {
        *self.total_exposure.read()
    }

    /// Number of open positions.
    pub fn count(&self) -> usize {
        self.positions.len()
    }

    /// Exposure for a single market (0 if no position).
    pub fn market_exposure_usd(&self, market: &str) -> i64 {
        self.positions.get(market).map(|p| p.size_usd).unwrap_or(0)
    }

    /// Record a fill: open or increase a position.
    pub fn record_fill(
        &self,
        market_id: &str,
        side: Side,
        filled_size_usd: i64,
        entry_price_micros: i64,
        filled_at_unix_ms: i64,
    ) {
        let mut total = self.total_exposure.write();
        self.positions
            .entry(market_id.to_string())
            .and_modify(|pos| {
                // Increase existing position.
                let old_size = pos.size_usd;
                let new_size = old_size + filled_size_usd;
                if new_size > 0 {
                    // Weighted average entry price.
                    pos.entry_price_micros = ((pos.entry_price_micros as i128 * old_size as i128
                        + entry_price_micros as i128 * filled_size_usd as i128)
                        / new_size as i128) as i64;
                }
                pos.size_usd = new_size;
                pos.side = side;
                debug!(market = %market_id, old_size, new_size, "position increased");
            })
            .or_insert_with(|| {
                debug!(market = %market_id, size = filled_size_usd, "position opened");
                Position {
                    side,
                    size_usd: filled_size_usd,
                    entry_price_micros,
                    opened_at_unix_ms: filled_at_unix_ms,
                }
            });
        *total = self.recalculate_exposure();
    }

    /// Revert a pending fill (e.g. tx reverted on-chain).
    pub fn record_revert(&self, market_id: &str, reverted_size_usd: i64) {
        let mut total = self.total_exposure.write();
        let should_remove = {
            if let Some(mut pos) = self.positions.get_mut(market_id) {
                pos.size_usd = (pos.size_usd - reverted_size_usd).max(0);
                debug!(
                    market = %market_id,
                    remaining = pos.size_usd,
                    "position reverted"
                );
                pos.size_usd == 0
            } else {
                warn!(market = %market_id, "revert for unknown position");
                false
            }
        };
        if should_remove {
            self.positions.remove(market_id);
        }
        *total = self.recalculate_exposure();
    }

    fn recalculate_exposure(&self) -> i64 {
        self.positions.iter().map(|r| r.value().size_usd).sum()
    }

    /// Serialize current state for Redis persistence.
    pub fn snapshot(&self) -> Vec<(String, Position)> {
        self.positions
            .iter()
            .map(|r| (r.key().clone(), r.value().clone()))
            .collect()
    }

    /// Restore from a previously persisted snapshot.
    pub fn restore(&self, entries: Vec<(String, Position)>) {
        self.positions.clear();
        for (k, v) in entries {
            self.positions.insert(k, v);
        }
        let mut total = self.total_exposure.write();
        *total = self.recalculate_exposure();
    }
}

impl Default for PositionStore {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_and_get_position() {
        let store = PositionStore::new();
        store.record_fill("m1", Side::Yes, 1000, 500_000, 100);
        let pos = store.get("m1").unwrap();
        assert_eq!(pos.size_usd, 1000);
        assert_eq!(pos.entry_price_micros, 500_000);
        assert_eq!(pos.side, Side::Yes);
    }

    #[test]
    fn multiple_fills_same_market() {
        let store = PositionStore::new();
        store.record_fill("m1", Side::Yes, 1000, 500_000, 100);
        store.record_fill("m1", Side::Yes, 2000, 600_000, 200);
        let pos = store.get("m1").unwrap();
        assert_eq!(pos.size_usd, 3000);
        // Weighted average: (500000*1000 + 600000*2000) / 3000 = 566666
        assert_eq!(pos.entry_price_micros, 566_666);
    }

    #[test]
    fn total_exposure_across_markets() {
        let store = PositionStore::new();
        store.record_fill("m1", Side::Yes, 1000, 500_000, 100);
        store.record_fill("m2", Side::No, 2000, 300_000, 200);
        assert_eq!(store.total_exposure_usd(), 3000);
        assert_eq!(store.count(), 2);
    }

    #[test]
    fn revert_decreases_position() {
        let store = PositionStore::new();
        store.record_fill("m1", Side::Yes, 3000, 500_000, 100);
        store.record_revert("m1", 1000);
        let pos = store.get("m1").unwrap();
        assert_eq!(pos.size_usd, 2000);
        assert_eq!(store.total_exposure_usd(), 2000);
    }

    #[test]
    fn revert_full_removes_position() {
        let store = PositionStore::new();
        store.record_fill("m1", Side::Yes, 1000, 500_000, 100);
        store.record_revert("m1", 1000);
        assert!(store.get("m1").is_none());
        assert_eq!(store.total_exposure_usd(), 0);
        assert_eq!(store.count(), 0);
    }

    #[test]
    fn revert_unknown_market_does_not_panic() {
        let store = PositionStore::new();
        store.record_revert("unknown", 500);
        assert_eq!(store.count(), 0);
    }

    #[test]
    fn snapshot_and_restore() {
        let store = PositionStore::new();
        store.record_fill("m1", Side::Yes, 1000, 500_000, 100);
        store.record_fill("m2", Side::No, 2000, 300_000, 200);

        let snap = store.snapshot();

        let store2 = PositionStore::new();
        store2.restore(snap);
        assert_eq!(store2.total_exposure_usd(), 3000);
        assert_eq!(store2.count(), 2);
        assert_eq!(store2.get("m1").unwrap().size_usd, 1000);
    }
}
