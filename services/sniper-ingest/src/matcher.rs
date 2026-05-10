//! TriggerMatcher: maps decoded on-chain events to watchlist entries and
//! emits `Trigger` messages when policy criteria are met.
//!
//! Hot-path design goals:
//! - Zero heap allocation per event (uses `SmallVec`, `ahash`).
//! - Immutable watchlist snapshot via `arc_swap` for lock-free reads.
//! - Source counting with a sliding 2-second window per market.

use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use ahash::AHashMap;
use alloy_primitives::Address;
use blake3::Hasher as Blake3Hasher;
use dual_loop_common::abi::DecodedEvent;
use dual_loop_common::proto::common::{Money, Uuid};
use dual_loop_common::proto::new_uuid_v7;
use dual_loop_common::proto::trigger::Trigger;
use dual_loop_common::proto::watchlist::TriggerKind;
use smallvec::SmallVec;

use crate::watchlist::{MarketKey, WatchlistStore};

/// Source of "now" for the matcher.
///
/// Production uses [`RealClock`]. Replay/tests inject a deterministic
/// implementation so that `issued_at_unix_ms`, source-window TTLs and the
/// emitted `Trigger.evidence_hash` (in deterministic-id mode) are stable
/// across runs.
pub trait MatcherClock: Send + Sync {
    /// Wall-clock as Unix epoch milliseconds.
    fn now_ms(&self) -> i64;
    /// Monotonic instant for sliding-window source counting and latency
    /// measurement.
    fn instant_now(&self) -> Instant;
}

/// Production clock — wraps `StampedTime::now()` and `Instant::now()`.
#[derive(Debug, Default, Clone, Copy)]
pub struct RealClock;

impl MatcherClock for RealClock {
    #[inline]
    fn now_ms(&self) -> i64 {
        dual_loop_common::time::StampedTime::now().wall_clock_unix_ms
    }
    #[inline]
    fn instant_now(&self) -> Instant {
        Instant::now()
    }
}

/// Frozen wall-clock + monotonic instant. Calls advance the instant by
/// `step_ns` per `instant_now()` so the source-tracker sliding window can
/// still operate deterministically.
#[derive(Debug)]
pub struct FrozenClock {
    now_ms: AtomicI64,
    base_instant: Instant,
    elapsed_ns: AtomicU64,
    step_ns: u64,
}

impl FrozenClock {
    /// Create a frozen clock anchored at `now_ms` with monotonic ticks of
    /// `step_ns` per call to [`MatcherClock::instant_now`].
    pub fn new(now_ms: i64, step_ns: u64) -> Self {
        Self {
            now_ms: AtomicI64::new(now_ms),
            base_instant: Instant::now(),
            elapsed_ns: AtomicU64::new(0),
            step_ns,
        }
    }

    /// Advance `now_ms` to a new value. The monotonic instant continues to
    /// tick from where it was.
    pub fn set_now_ms(&self, now_ms: i64) {
        self.now_ms.store(now_ms, Ordering::Relaxed);
    }
}

impl MatcherClock for FrozenClock {
    #[inline]
    fn now_ms(&self) -> i64 {
        self.now_ms.load(Ordering::Relaxed)
    }
    #[inline]
    fn instant_now(&self) -> Instant {
        let prev = self.elapsed_ns.fetch_add(self.step_ns, Ordering::Relaxed);
        self.base_instant + Duration::from_nanos(prev)
    }
}

/// Generates `Trigger.message_id` UUIDs.
///
/// Replay/golden tests use [`DeterministicIdGenerator`] so the same input
/// dump always produces the same UUIDs (byte-for-byte equality). Production
/// uses [`UuidV7Generator`] for time-ordered globally-unique IDs.
pub trait IdGenerator: Send + Sync {
    fn next_id(&self) -> Uuid;
}

/// Production ID generator — UUID v7 (time-ordered, random tail).
#[derive(Debug, Default, Clone, Copy)]
pub struct UuidV7Generator;

impl IdGenerator for UuidV7Generator {
    #[inline]
    fn next_id(&self) -> Uuid {
        new_uuid_v7()
    }
}

/// Deterministic ID generator: BLAKE3(seed || counter), output truncated to
/// 16 bytes. Identical seeds + identical call ordering → identical IDs.
#[derive(Debug)]
pub struct DeterministicIdGenerator {
    seed: [u8; 32],
    counter: Mutex<u64>,
}

impl DeterministicIdGenerator {
    /// Create from a 32-byte seed.
    pub fn new(seed: [u8; 32]) -> Self {
        Self {
            seed,
            counter: Mutex::new(0),
        }
    }

    /// Convenience: derive a 32-byte seed from an arbitrary string label.
    pub fn from_label(label: &str) -> Self {
        let mut hasher = Blake3Hasher::new();
        hasher.update(b"sniper-ingest:deterministic-id-gen:v1:");
        hasher.update(label.as_bytes());
        let mut seed = [0u8; 32];
        seed.copy_from_slice(hasher.finalize().as_bytes());
        Self::new(seed)
    }
}

impl IdGenerator for DeterministicIdGenerator {
    fn next_id(&self) -> Uuid {
        let mut guard = self.counter.lock().expect("id-gen mutex poisoned");
        let n = *guard;
        *guard = n.wrapping_add(1);
        drop(guard);

        let mut hasher = Blake3Hasher::new();
        hasher.update(&self.seed);
        hasher.update(&n.to_le_bytes());
        let digest = hasher.finalize();
        let mut bytes = [0u8; 16];
        bytes.copy_from_slice(&digest.as_bytes()[..16]);
        Uuid {
            value: prost::bytes::Bytes::copy_from_slice(&bytes),
        }
    }
}

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
    clock: Box<dyn MatcherClock>,
    id_gen: Box<dyn IdGenerator>,
}

impl TriggerMatcher {
    /// Create a matcher with production clock + UUIDv7 ID generation. This
    /// is the path used by the live `sniper-ingest` daemon.
    pub fn new(store: WatchlistStore) -> Self {
        Self::with_dependencies(store, Box::new(RealClock), Box::new(UuidV7Generator))
    }

    /// Create a matcher with explicit clock + ID generator. Used by replay
    /// and unit tests to pin both wall-clock time and message_id sequences.
    pub fn with_dependencies(
        store: WatchlistStore,
        clock: Box<dyn MatcherClock>,
        id_gen: Box<dyn IdGenerator>,
    ) -> Self {
        Self {
            store,
            source_trackers: AHashMap::new(),
            clock,
            id_gen,
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

    /// Match a pending tx that was decoded down to a canonical per-market
    /// key (for Polymarket: lowercase `conditionId`).
    pub fn try_match_market_specific_key(
        &mut self,
        market_specific_key: &str,
        kind: TriggerKind,
        observed_at_ns: u64,
        evidence_hash: Vec<u8>,
    ) -> Option<Trigger> {
        let entry = self
            .store
            .find_by_market_specific_key(market_specific_key)?;
        self.try_match_for_market_key(entry.market_key, kind, observed_at_ns, evidence_hash)
    }

    fn try_match_for_market_key(
        &mut self,
        market_key: MarketKey,
        kind: TriggerKind,
        observed_at_ns: u64,
        evidence_hash: Vec<u8>,
    ) -> Option<Trigger> {
        // Histogram start: the first thing we do once we know the market
        // could match. The corresponding stop is right before each Some(_)
        // arm below so the histogram captures the full work the matcher
        // does for an observed event that ultimately fires a trigger.
        let started_at = self.clock.instant_now();

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
            let now_ms = self.clock.now_ms();
            if now_ms > entry.expires_at_unix_ms {
                return None;
            }

            // Source counting.
            let tracker = self.source_trackers.entry(market_key.clone()).or_default();
            let source_count = tracker.record_and_count(kind, self.clock.instant_now());

            if source_count < policy.min_independent_sources {
                return None;
            }

            metrics::counter!("trigger_fired_total", "market" => market_key.0.clone(), "kind" => format!("{kind:?}")).increment(1);
            record_matcher_latency(started_at, self.clock.instant_now());

            Some(Trigger {
                message_id: Some(self.id_gen.next_id()),
                market: Some(dual_loop_common::proto::common::MarketId {
                    polymarket_id: market_key.0,
                    ctf_address: market_key.1,
                    market_specific_key: entry.market_specific_key.clone().unwrap_or_default(),
                }),
                kind: kind as i32,
                evidence_hash: evidence_hash.into(),
                eta_block: 0,
                observed_at_ns,
                expected_price: Some(expected_price_money(entry.p_fundamental_ppm)),
                slippage_bps: entry.max_slippage_bps,
                source_count,
                issued_at_unix_ms: now_ms,
                watchlist_valid_until_unix_ms: entry.expires_at_unix_ms,
            })
        } else {
            // No policy → fire unconditionally.
            let now_ms = self.clock.now_ms();
            metrics::counter!("trigger_fired_total", "market" => market_key.0.clone(), "kind" => format!("{kind:?}")).increment(1);
            record_matcher_latency(started_at, self.clock.instant_now());

            Some(Trigger {
                message_id: Some(self.id_gen.next_id()),
                market: Some(dual_loop_common::proto::common::MarketId {
                    polymarket_id: market_key.0,
                    ctf_address: market_key.1,
                    market_specific_key: entry.market_specific_key.clone().unwrap_or_default(),
                }),
                kind: kind as i32,
                evidence_hash: evidence_hash.into(),
                eta_block: 0,
                observed_at_ns,
                expected_price: Some(expected_price_money(entry.p_fundamental_ppm)),
                slippage_bps: entry.max_slippage_bps,
                source_count: 1,
                issued_at_unix_ms: now_ms,
                watchlist_valid_until_unix_ms: entry.expires_at_unix_ms,
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

/// Record a `trigger_matcher_latency_ns` histogram sample. Called only on
/// the success path so the metric measures the cost of fired triggers,
/// not no-op rejections.
#[inline]
fn record_matcher_latency(started_at: Instant, ended_at: Instant) {
    let elapsed_ns = ended_at
        .saturating_duration_since(started_at)
        .as_nanos()
        .min(u64::MAX as u128) as u64;
    metrics::histogram!("trigger_matcher_latency_ns").record(elapsed_ns as f64);
}

/// Compute a deterministic evidence hash from the decoded event (for dedup).
///
/// Uses BLAKE3 with a fixed domain-separation prefix, so the same event
/// produces the same hash across processes / machines / replay runs. The
/// previous implementation used `ahash::AHasher::default()`, which seeds
/// each process with random bytes for HashDoS resistance and therefore
/// broke replay byte-equality (gate #9).
fn compute_evidence_hash(event: &DecodedEvent) -> Vec<u8> {
    let mut hasher = Blake3Hasher::new();
    hasher.update(b"sniper-ingest:evidence-hash:v1:");
    hasher.update(format!("{event:?}").as_bytes());
    let digest = hasher.finalize();
    // Truncate to 8 bytes — same length the old AHash output produced, so
    // downstream code that assumes a compact fingerprint is unaffected.
    digest.as_bytes()[..8].to_vec()
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
                market_specific_key:
                    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_string(),
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
        assert!(t.issued_at_unix_ms > 0);
        assert!(t.watchlist_valid_until_unix_ms > t.issued_at_unix_ms);
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

    #[test]
    fn resolves_market_by_market_specific_key() {
        let store = WatchlistStore::new();
        let shared_ctf = format!("{:#x}", addresses::POLYMARKET_CTF_EXCHANGE);

        for (market_id, key) in [
            (
                "test-market-1",
                "0x1111111111111111111111111111111111111111111111111111111111111111",
            ),
            (
                "test-market-2",
                "0x2222222222222222222222222222222222222222222222222222222222222222",
            ),
        ] {
            store.apply_upsert(&WatchlistUpsert {
                message_id: None,
                market: Some(MarketId {
                    polymarket_id: market_id.to_string(),
                    ctf_address: shared_ctf.clone(),
                    market_specific_key: key.to_string(),
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
                    allowed_kinds: vec![TriggerKind::OracleResolution as i32],
                    max_price_impact_bps: 100,
                }),
            });
        }

        let mut matcher = TriggerMatcher::new(store);
        let trigger = matcher.try_match_market_specific_key(
            "0x2222222222222222222222222222222222222222222222222222222222222222",
            TriggerKind::OracleResolution,
            12345,
            vec![0x22; 32],
        );
        let resolved = matcher
            .store
            .find_by_market_specific_key(
                "0x2222222222222222222222222222222222222222222222222222222222222222",
            )
            .expect("entry");
        assert_eq!(resolved.market_key.0, "test-market-2");
        assert_eq!(
            trigger
                .as_ref()
                .and_then(|value| value.market.as_ref())
                .map(|market| market.polymarket_id.as_str()),
            Some("test-market-2")
        );
        assert!(trigger
            .as_ref()
            .map(|value| value.watchlist_valid_until_unix_ms > value.issued_at_unix_ms)
            .unwrap_or(false));
    }

    // --- gate #9 (replay determinism) foundation tests ---

    #[test]
    fn frozen_clock_pins_wall_time_and_advances_instant() {
        let clock = FrozenClock::new(1_700_000_000_000, 1_000);
        // Wall time stays put no matter how often we ask.
        assert_eq!(clock.now_ms(), 1_700_000_000_000);
        assert_eq!(clock.now_ms(), 1_700_000_000_000);

        // Monotonic instant advances by `step_ns` per call.
        let i1 = clock.instant_now();
        let i2 = clock.instant_now();
        let i3 = clock.instant_now();
        assert!(i2 > i1);
        assert!(i3 > i2);
        let delta = i2.saturating_duration_since(i1).as_nanos();
        assert_eq!(delta, 1_000);

        // set_now_ms is sticky.
        clock.set_now_ms(1_700_000_005_000);
        assert_eq!(clock.now_ms(), 1_700_000_005_000);
    }

    #[test]
    fn frozen_clock_pins_issued_at_unix_ms() {
        let (store, ctf_addr) = make_store_with_entry();
        let frozen_now = 1_710_000_000_000_i64;
        let matcher = TriggerMatcher::with_dependencies(
            store,
            Box::new(FrozenClock::new(frozen_now, 1_000)),
            Box::new(UuidV7Generator),
        );
        let mut matcher = matcher;

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

        let trigger = matcher
            .try_match(&event, ctf_addr, 12345)
            .expect("matcher should fire");
        // Frozen clock pins issued_at to the anchor exactly.
        assert_eq!(trigger.issued_at_unix_ms, frozen_now);
        // watchlist_valid_until_unix_ms comes from the upsert's own
        // `issued_at_unix_ms + ttl_ms` (set inside `make_store_with_entry`),
        // not from the matcher clock — it's a property of the watchlist
        // entry, not the matcher tick. Assert only that it is sane.
        assert!(trigger.watchlist_valid_until_unix_ms > 0);
    }

    #[test]
    fn deterministic_id_generator_is_reproducible() {
        let g1 = DeterministicIdGenerator::from_label("test-seed");
        let g2 = DeterministicIdGenerator::from_label("test-seed");
        let a1 = g1.next_id();
        let a2 = g1.next_id();
        let b1 = g2.next_id();
        let b2 = g2.next_id();
        // Same seed + same call ordering → identical IDs.
        assert_eq!(a1.value, b1.value);
        assert_eq!(a2.value, b2.value);
        // Sequential IDs differ.
        assert_ne!(a1.value, a2.value);
        // 16-byte UUID-shaped output.
        assert_eq!(a1.value.len(), 16);

        // A different seed produces a different sequence.
        let g3 = DeterministicIdGenerator::from_label("other-seed");
        let c1 = g3.next_id();
        assert_ne!(a1.value, c1.value);
    }

    #[test]
    fn evidence_hash_is_deterministic_across_calls() {
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
        let h1 = compute_evidence_hash(&event);
        let h2 = compute_evidence_hash(&event);
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 8);
    }
}
