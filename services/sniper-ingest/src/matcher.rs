//! TriggerMatcher: maps decoded on-chain events to watchlist entries and
//! emits `Trigger` messages when policy criteria are met.
//!
//! Hot-path design goals:
//! - Zero heap allocation per event (uses `SmallVec`, `ahash`).
//! - Immutable watchlist snapshot via `arc_swap` for lock-free reads.
//! - Source counting with a sliding 2-second window per market.

use std::time::{Duration, Instant};

use ahash::AHashMap;
use alloy_primitives::Address;
use dual_loop_common::abi::DecodedEvent;
use dual_loop_common::proto::common::Money;
use dual_loop_common::proto::new_uuid_v7;
use dual_loop_common::proto::trigger::Trigger;
use dual_loop_common::proto::watchlist::TriggerKind;
use smallvec::SmallVec;

use crate::watchlist::{MarketKey, WatchlistStore};

#[inline]
fn expected_price_money(p_fundamental_ppm: i64) -> Money {
    // For Polymarket binary markets the fair YES/NO price is quoted in the
    // same 0..1 range as probability, so ppm and USDC micros are numerically
    // aligned on the wire (1_000_000 = 100% = $1.00).
    Money {
        amount_micros: p_fundamental_ppm,
    }
}

/// Window for counting independent sources (2 seconds).
const SOURCE_WINDOW: Duration = Duration::from_secs(2);

/// Converts a numeric `TriggerKind` i32 to the enum.
fn trigger_kind_from_event(event: &DecodedEvent) -> TriggerKind {
    match event {
        DecodedEvent::CtfOrderFilled { .. } | DecodedEvent::CtfOrderMatched { .. } => {
            TriggerKind::LargeFill
        }
        DecodedEvent::CtfTokenRegistered { .. } => TriggerKind::AdminAction,
        DecodedEvent::UmaProposePrice { .. }
        | DecodedEvent::UmaDisputePrice { .. }
        | DecodedEvent::UmaSettle { .. } => TriggerKind::OracleResolution,
        DecodedEvent::ChainlinkAnswerUpdated { .. } => TriggerKind::OracleResolution,
    }
}

/// Source observation within the sliding window.
#[derive(Debug, Clone)]
struct SourceObservation {
    kind: TriggerKind,
    at: Instant,
}

/// Per-market source tracking.
#[derive(Debug, Default)]
struct SourceTracker {
    observations: SmallVec<[SourceObservation; 8]>,
}

impl SourceTracker {
    /// Record an observation and return the count of distinct kinds in the
    /// last [`SOURCE_WINDOW`].
    fn record_and_count(&mut self, kind: TriggerKind, now: Instant) -> u32 {
        // Evict stale entries.
        let cutoff = now - SOURCE_WINDOW;
        self.observations.retain(|o| o.at >= cutoff);

        self.observations.push(SourceObservation { kind, at: now });

        // Count distinct kinds as independent sources.
        let mut seen: SmallVec<[i32; 4]> = SmallVec::new();
        for o in &self.observations {
            let k = o.kind as i32;
            if !seen.contains(&k) {
                seen.push(k);
            }
        }
        seen.len() as u32
    }
}

/// The main matcher that ties decoded events to watchlist entries and
/// emits triggers.
pub struct TriggerMatcher {
    store: WatchlistStore,
    source_trackers: AHashMap<MarketKey, SourceTracker>,
}

impl TriggerMatcher {
    pub fn new(store: WatchlistStore) -> Self {
        Self {
            store,
            source_trackers: AHashMap::new(),
        }
    }

    /// Try to match a decoded event to a watchlist entry and produce a
    /// trigger if all policy checks pass.
    pub fn try_match(
        &mut self,
        event: &DecodedEvent,
        contract_address: Address,
        observed_at_ns: u64,
    ) -> Option<Trigger> {
        let market_key = self.resolve_market_key(event, contract_address)?;
        let kind = trigger_kind_from_event(event);
        let evidence_hash = compute_evidence_hash(event);
        self.try_match_for_market_key(market_key, kind, observed_at_ns, evidence_hash)
    }

    /// Dev-only helper used by the mock mempool injector path. It skips ABI
    /// decoding and feeds a synthetic transaction directly into the normal
    /// watchlist/policy/source-count matching logic.
    pub fn try_match_mock(
        &mut self,
        contract_address: Address,
        kind: TriggerKind,
        observed_at_ns: u64,
        evidence_hash: Vec<u8>,
    ) -> Option<Trigger> {
        let market_key = self.resolve_market_key_from_address(contract_address)?;
        self.try_match_for_market_key(market_key, kind, observed_at_ns, evidence_hash)
    }

    fn try_match_for_market_key(
        &mut self,
        market_key: MarketKey,
        kind: TriggerKind,
        observed_at_ns: u64,
        evidence_hash: Vec<u8>,
    ) -> Option<Trigger> {
        let entry = self.store.get(&market_key)?;

        // Check allowed_kinds.
        if let Some(ref policy) = entry.trigger_policy {
            if !policy.allowed_kinds.is_empty() {
                let kind_i32 = kind as i32;
                if !policy.allowed_kinds.contains(&kind_i32) {
                    return None;
                }
            }

            // TTL check: don't fire if entry is expired.
            let now_ms = dual_loop_common::time::StampedTime::now().wall_clock_unix_ms;
            if now_ms > entry.expires_at_unix_ms {
                return None;
            }

            // Source counting.
            let tracker = self.source_trackers.entry(market_key.clone()).or_default();
            let source_count = tracker.record_and_count(kind, Instant::now());

            if source_count < policy.min_independent_sources {
                return None;
            }

            metrics::counter!("trigger_fired_total", "market" => market_key.0.clone(), "kind" => format!("{kind:?}")).increment(1);

            Some(Trigger {
                message_id: Some(new_uuid_v7()),
                market: Some(dual_loop_common::proto::common::MarketId {
                    polymarket_id: market_key.0,
                    ctf_address: market_key.1,
                }),
                kind: kind as i32,
                evidence_hash: evidence_hash.into(),
                eta_block: 0,
                observed_at_ns,
                expected_price: Some(expected_price_money(entry.p_fundamental_ppm)),
                slippage_bps: entry.max_slippage_bps,
                source_count,
            })
        } else {
            // No policy → fire unconditionally.
            metrics::counter!("trigger_fired_total", "market" => market_key.0.clone(), "kind" => format!("{kind:?}")).increment(1);

            Some(Trigger {
                message_id: Some(new_uuid_v7()),
                market: Some(dual_loop_common::proto::common::MarketId {
                    polymarket_id: market_key.0,
                    ctf_address: market_key.1,
                }),
                kind: kind as i32,
                evidence_hash: evidence_hash.into(),
                eta_block: 0,
                observed_at_ns,
                expected_price: Some(expected_price_money(entry.p_fundamental_ppm)),
                slippage_bps: entry.max_slippage_bps,
                source_count: 1,
            })
        }
    }

    /// Resolve the market key from a decoded event and the contract address.
    fn resolve_market_key(
        &self,
        _event: &DecodedEvent,
        contract_address: Address,
    ) -> Option<MarketKey> {
        self.resolve_market_key_from_address(contract_address)
    }

    fn resolve_market_key_from_address(&self, contract_address: Address) -> Option<MarketKey> {
        let addr_str = format!("{contract_address:#x}");

        // Walk the watchlist to find an entry matching this contract address.
        // In production this would be an indexed lookup; for Phase 2 a linear
        // scan over the (small) watchlist is acceptable.
        for entry in self.store.iter() {
            if entry.market_key.1.eq_ignore_ascii_case(&addr_str) {
                return Some(entry.market_key.clone());
            }
        }

        None
    }
}

/// Compute a simple evidence hash from the decoded event (for dedup).
fn compute_evidence_hash(event: &DecodedEvent) -> Vec<u8> {
    use std::hash::{Hash, Hasher};
    let mut hasher = ahash::AHasher::default();
    // Hash the debug representation as a cheap deterministic fingerprint.
    format!("{event:?}").hash(&mut hasher);
    hasher.finish().to_le_bytes().to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{address, b256, U256};
    use dual_loop_common::abi::addresses;
    use dual_loop_common::proto::common::{MarketId, Money};
    use dual_loop_common::proto::watchlist::{TriggerPolicy, WatchlistUpsert};

    fn make_store_with_entry() -> (WatchlistStore, Address) {
        let store = WatchlistStore::new();
        let ctf_addr = addresses::POLYMARKET_CTF_EXCHANGE;
        let ctf_str = format!("{ctf_addr:#x}");

        let upsert = WatchlistUpsert {
            message_id: None,
            market: Some(MarketId {
                polymarket_id: "test-market-1".to_string(),
                ctf_address: ctf_str,
            }),
            version: 1,
            edge_ppm: 150_000,
            p_fundamental_ppm: 720_000,
            confidence_ppm: 900_000,
            side_hint: 1,
            max_position_usd: Some(Money {
                amount_micros: 10_000_000,
            }),
            max_slippage_bps: 50,
            ttl_ms: 3_600_000,
            issued_at_unix_ms: dual_loop_common::time::StampedTime::now().wall_clock_unix_ms,
            trigger_policy: Some(TriggerPolicy {
                min_independent_sources: 1,
                allowed_kinds: vec![
                    TriggerKind::LargeFill as i32,
                    TriggerKind::OracleResolution as i32,
                ],
                max_price_impact_bps: 100,
            }),
        };
        store.apply_upsert(&upsert);
        (store, ctf_addr)
    }

    #[test]
    fn matcher_fires_on_matching_event() {
        let (store, ctf_addr) = make_store_with_entry();
        let mut matcher = TriggerMatcher::new(store);

        let event = DecodedEvent::CtfOrderFilled {
            order_hash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            maker: address!("1111111111111111111111111111111111111111"),
            taker: address!("2222222222222222222222222222222222222222"),
            maker_asset_id: U256::from(1u64),
            taker_asset_id: U256::from(2u64),
            maker_amount_filled: U256::from(1000u64),
            taker_amount_filled: U256::from(2000u64),
            fee: U256::from(5u64),
        };

        let trigger = matcher.try_match(&event, ctf_addr, 12345);
        assert!(trigger.is_some());
        let t = trigger.unwrap();
        assert_eq!(t.kind, TriggerKind::LargeFill as i32);
        assert!(t.source_count >= 1);
        assert_eq!(
            t.expected_price.as_ref().map(|money| money.amount_micros),
            Some(720_000)
        );
    }

    #[test]
    fn matcher_ignores_unknown_contract() {
        let (store, _) = make_store_with_entry();
        let mut matcher = TriggerMatcher::new(store);

        let event = DecodedEvent::CtfOrderFilled {
            order_hash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            maker: address!("1111111111111111111111111111111111111111"),
            taker: address!("2222222222222222222222222222222222222222"),
            maker_asset_id: U256::from(1u64),
            taker_asset_id: U256::from(2u64),
            maker_amount_filled: U256::from(1000u64),
            taker_amount_filled: U256::from(2000u64),
            fee: U256::from(5u64),
        };

        let trigger = matcher.try_match(&event, Address::ZERO, 12345);
        assert!(trigger.is_none());
    }

    #[test]
    fn source_tracker_counts_distinct_kinds() {
        let mut tracker = SourceTracker::default();
        let now = Instant::now();

        let c1 = tracker.record_and_count(TriggerKind::LargeFill, now);
        assert_eq!(c1, 1);

        let c2 = tracker.record_and_count(TriggerKind::OracleResolution, now);
        assert_eq!(c2, 2);

        // Same kind again — still 2 distinct.
        let c3 = tracker.record_and_count(TriggerKind::LargeFill, now);
        assert_eq!(c3, 2);
    }
}
