//! In-memory watchlist, fed by `dl.v1.watchlist.upsert` /
//! `dl.v1.watchlist.remove` Redis Streams.
//!
//! Behaviour contract:
//! * `upsert` replaces any existing entry for the same (polymarket_id,
//!   ctf_address) key. Stale versions are ignored (version < current).
//! * `remove` drops the entry unconditionally.
//! * A background reaper (see [`WatchlistStore::spawn_ttl_reaper`]) sweeps
//!   expired entries every `TTL_SWEEP_INTERVAL`.
//!
//! Prometheus metrics:
//! * `watchlist_entries_total` (gauge)
//! * `watchlist_upserts_total` (counter)
//! * `watchlist_removes_total` (counter)
//! * `watchlist_ttl_expirations_total` (counter)

use std::sync::Arc;
use std::time::Duration;

use dashmap::DashMap;
use dual_loop_common::bus::ConsumerMessage;
use dual_loop_common::proto::watchlist::{TriggerPolicy, WatchlistRemove, WatchlistUpsert};
use dual_loop_common::proto::{ppm_to_prob, PROBABILITY_PPM_PER_UNIT};
use dual_loop_common::time::StampedTime;
use tracing::{debug, info, warn};

/// Composite key: (polymarket_id, ctf_address).
pub type MarketKey = (String, String);

/// One watchlist entry kept in memory. All fields are already in integer
/// fixed-point (no float on the wire — see `schemas/dual_loop/v1/common.proto`).
#[derive(Debug, Clone)]
pub struct WatchlistEntry {
    pub market_key: MarketKey,
    pub version: u64,
    /// Signed probability difference in ppm (1_000_000 = 100%).
    pub edge_ppm: i64,
    /// 0..1_000_000.
    pub p_fundamental_ppm: i64,
    /// 0..1_000_000.
    pub confidence_ppm: i64,
    pub side_hint: i32,
    pub max_position_micros: i64,
    pub max_slippage_bps: u32,
    /// Wall-clock deadline when this entry expires (unix ms).
    pub expires_at_unix_ms: i64,
    pub trigger_policy: Option<TriggerPolicy>,
}

impl WatchlistEntry {
    pub fn from_upsert(msg: &WatchlistUpsert) -> Option<Self> {
        let market = msg.market.as_ref()?;
        let issued_ms = msg.issued_at_unix_ms;
        let ttl_ms = msg.ttl_ms.max(0);
        let expires_at_unix_ms = if issued_ms > 0 {
            issued_ms.saturating_add(ttl_ms)
        } else {
            StampedTime::now().wall_clock_unix_ms.saturating_add(ttl_ms)
        };
        let max_position_micros = msg
            .max_position_usd
            .as_ref()
            .map(|m| m.amount_micros)
            .unwrap_or(0);
        Some(Self {
            market_key: (market.polymarket_id.clone(), market.ctf_address.clone()),
            version: msg.version,
            edge_ppm: msg.edge_ppm,
            p_fundamental_ppm: msg.p_fundamental_ppm,
            confidence_ppm: msg.confidence_ppm,
            side_hint: msg.side_hint,
            max_position_micros,
            max_slippage_bps: msg.max_slippage_bps,
            expires_at_unix_ms,
            trigger_policy: msg.trigger_policy.clone(),
        })
    }

    /// Convenience: edge as a unit-interval float for display/metrics.
    #[inline]
    pub fn edge_unit(&self) -> f64 {
        ppm_to_prob(self.edge_ppm)
    }

    /// Convenience: probability as a unit-interval float for display.
    #[inline]
    pub fn p_fundamental_unit(&self) -> f64 {
        ppm_to_prob(self.p_fundamental_ppm)
    }

    /// True iff the `_ppm` fields are within the valid range.
    #[inline]
    pub fn is_sane(&self) -> bool {
        (-PROBABILITY_PPM_PER_UNIT..=PROBABILITY_PPM_PER_UNIT).contains(&self.edge_ppm)
            && (0..=PROBABILITY_PPM_PER_UNIT).contains(&self.p_fundamental_ppm)
            && (0..=PROBABILITY_PPM_PER_UNIT).contains(&self.confidence_ppm)
    }
}

/// In-memory store. Cheap to clone; backed by an `Arc<DashMap>`.
#[derive(Clone, Default)]
pub struct WatchlistStore {
    inner: Arc<DashMap<MarketKey, WatchlistEntry>>,
}

/// How often the TTL reaper runs.
pub const TTL_SWEEP_INTERVAL: Duration = Duration::from_secs(10);

impl WatchlistStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    pub fn get(&self, key: &MarketKey) -> Option<WatchlistEntry> {
        self.inner.get(key).map(|e| e.clone())
    }

    /// Iterate over all entries (snapshot). Used by TriggerMatcher for
    /// contract-address lookups.
    pub fn iter(&self) -> Vec<WatchlistEntry> {
        self.inner.iter().map(|kv| kv.value().clone()).collect()
    }

    /// Apply an upsert. Ignores stale versions. Emits metrics.
    pub fn apply_upsert(&self, msg: &WatchlistUpsert) {
        let Some(entry) = WatchlistEntry::from_upsert(msg) else {
            warn!("WatchlistUpsert missing market field; ignoring");
            return;
        };
        let key = entry.market_key.clone();

        let inserted = {
            let mut accepted = true;
            self.inner
                .entry(key.clone())
                .and_modify(|current| {
                    if entry.version < current.version {
                        accepted = false;
                    } else {
                        *current = entry.clone();
                    }
                })
                .or_insert_with(|| entry.clone());
            accepted
        };

        if inserted {
            metrics::counter!("watchlist_upserts_total").increment(1);
            metrics::gauge!("watchlist_entries_total").set(self.inner.len() as f64);
            debug!(market = ?key, version = msg.version, "watchlist upsert applied");
        } else {
            debug!(market = ?key, version = msg.version, "watchlist upsert skipped (stale)");
        }
    }

    /// Apply a remove. No-op if entry is absent.
    pub fn apply_remove(&self, msg: &WatchlistRemove) {
        let Some(market) = msg.market.as_ref() else {
            warn!("WatchlistRemove missing market field; ignoring");
            return;
        };
        let key: MarketKey = (market.polymarket_id.clone(), market.ctf_address.clone());
        if self.inner.remove(&key).is_some() {
            metrics::counter!("watchlist_removes_total").increment(1);
            metrics::gauge!("watchlist_entries_total").set(self.inner.len() as f64);
            debug!(market = ?key, "watchlist remove applied");
        }
    }

    /// Remove expired entries. Returns number expired.
    pub fn sweep_expired(&self, now_unix_ms: i64) -> usize {
        let mut expired: Vec<MarketKey> = Vec::new();
        for kv in self.inner.iter() {
            if kv.value().expires_at_unix_ms <= now_unix_ms {
                expired.push(kv.key().clone());
            }
        }
        let count = expired.len();
        for key in expired {
            self.inner.remove(&key);
        }
        if count > 0 {
            metrics::counter!("watchlist_ttl_expirations_total").increment(count as u64);
            metrics::gauge!("watchlist_entries_total").set(self.inner.len() as f64);
            debug!(count, "watchlist TTL sweep");
        }
        count
    }

    /// Spawn the TTL reaper task. Returns the join handle.
    pub fn spawn_ttl_reaper(
        &self,
        mut shutdown: tokio::sync::watch::Receiver<bool>,
    ) -> tokio::task::JoinHandle<()> {
        let store = self.clone();
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(TTL_SWEEP_INTERVAL);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    _ = ticker.tick() => {
                        let now = StampedTime::now().wall_clock_unix_ms;
                        store.sweep_expired(now);
                    }
                    _ = shutdown.changed() => {
                        if *shutdown.borrow() {
                            info!("watchlist TTL reaper shutting down");
                            return;
                        }
                    }
                }
            }
        })
    }

    /// Dispatch one consumer-group message. Returns `Ok(())` on success so
    /// the caller can `XACK` it. Unknown topics are treated as success
    /// (ack + skip) to avoid poison pills, but logged loudly.
    pub fn handle_message(&self, msg: &ConsumerMessage) -> anyhow::Result<()> {
        use dual_loop_common::topics;
        match msg.topic.as_str() {
            topics::WATCHLIST_UPSERT => {
                let upsert: WatchlistUpsert = msg.decode()?;
                self.apply_upsert(&upsert);
            }
            topics::WATCHLIST_REMOVE => {
                let remove: WatchlistRemove = msg.decode()?;
                self.apply_remove(&remove);
            }
            other => {
                warn!(topic = other, id = %msg.id, "unexpected topic on watchlist consumer");
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use dual_loop_common::proto::common::{MarketId, Money};

    fn sample_upsert(version: u64, ttl_ms: i64, issued_ms: i64) -> WatchlistUpsert {
        WatchlistUpsert {
            message_id: None,
            market: Some(MarketId {
                polymarket_id: "abc123".into(),
                ctf_address: "0xdead".into(),
            }),
            version,
            edge_ppm: 30_000,
            p_fundamental_ppm: 620_000,
            confidence_ppm: 800_000,
            side_hint: 1,
            max_position_usd: Some(Money {
                amount_micros: 1_000_000,
            }),
            max_slippage_bps: 50,
            ttl_ms,
            issued_at_unix_ms: issued_ms,
            trigger_policy: None,
        }
    }

    #[test]
    fn upsert_and_remove_roundtrip() {
        let s = WatchlistStore::new();
        s.apply_upsert(&sample_upsert(1, 60_000, 1));
        assert_eq!(s.len(), 1);
        let entry = s.get(&("abc123".into(), "0xdead".into())).unwrap();
        assert_eq!(entry.version, 1);

        s.apply_remove(&WatchlistRemove {
            message_id: None,
            market: Some(MarketId {
                polymarket_id: "abc123".into(),
                ctf_address: "0xdead".into(),
            }),
            version: 1,
            reason: 0,
            issued_at_unix_ms: 0,
        });
        assert!(s.is_empty());
    }

    #[test]
    fn stale_version_ignored() {
        let s = WatchlistStore::new();
        s.apply_upsert(&sample_upsert(5, 60_000, 1));
        s.apply_upsert(&sample_upsert(3, 60_000, 1)); // stale
        let entry = s.get(&("abc123".into(), "0xdead".into())).unwrap();
        assert_eq!(entry.version, 5);
    }

    #[test]
    fn ttl_sweep_drops_expired() {
        let s = WatchlistStore::new();
        s.apply_upsert(&sample_upsert(1, 1, 1)); // expires_at = 2
        let expired = s.sweep_expired(1_000);
        assert_eq!(expired, 1);
        assert!(s.is_empty());
    }

    #[test]
    fn ttl_sweep_keeps_fresh() {
        let s = WatchlistStore::new();
        // issued at now, ttl 1h
        let now = StampedTime::now().wall_clock_unix_ms;
        s.apply_upsert(&sample_upsert(1, 3_600_000, now));
        assert_eq!(s.sweep_expired(now), 0);
        assert_eq!(s.len(), 1);
    }
}
