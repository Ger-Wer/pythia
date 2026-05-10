//! Watchlist preload + mock mempool publisher loop.

use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Context;
use bytes::Bytes;
use dashmap::DashMap;
use dual_loop_common::bus::StreamPublisher;
use dual_loop_common::proto::common::{Money, Side, Uuid};
use dual_loop_common::proto::mock::MockMempoolTx;
use dual_loop_common::proto::watchlist::{TriggerKind, TriggerPolicy, WatchlistUpsert};
use dual_loop_common::topics;
use tokio::time::{interval_at, MissedTickBehavior};
use tracing::{info, warn};

use crate::synth::SyntheticMarket;

const MOCK_MEMPOOL_TOPIC: &str = "dl.v1.mempool.mock";

/// What a publisher loop reports back to main once it stops.
#[derive(Debug, Clone)]
pub struct PublisherReport {
    pub published: u64,
    pub elapsed_ms: u64,
    pub actual_rate_per_min: f64,
}

/// Push a watchlist entry per synthetic market. Each entry allows
/// `LARGE_FILL` triggers with `min_independent_sources = 1`, so the very
/// first matching mock tx fires.
pub async fn preload_watchlist(
    redis_pub: &mut StreamPublisher,
    markets: &[SyntheticMarket],
) -> anyhow::Result<()> {
    // Anchor wall-clock once for the whole preload so all entries share
    // the same `issued_at_unix_ms` (and thus the same expiry window).
    let issued_at_unix_ms = dual_loop_common::time::StampedTime::now().wall_clock_unix_ms;

    for (i, market) in markets.iter().enumerate() {
        let upsert = WatchlistUpsert {
            message_id: Some(uuid_v7_like(i as u64)),
            market: Some(market.to_market_id()),
            version: 1,
            edge_ppm: 200_000,
            p_fundamental_ppm: 720_000,
            confidence_ppm: 760_000,
            side_hint: Side::Yes as i32,
            max_position_usd: Some(Money {
                amount_micros: 5_000_000,
            }),
            max_slippage_bps: 25,
            // 1h TTL — comfortably longer than any reasonable harness run.
            ttl_ms: 3_600_000,
            issued_at_unix_ms,
            trigger_policy: Some(TriggerPolicy {
                min_independent_sources: 1,
                allowed_kinds: vec![TriggerKind::LargeFill as i32],
                max_price_impact_bps: 25,
            }),
        };

        redis_pub
            .publish(topics::WATCHLIST_UPSERT, &upsert)
            .await
            .with_context(|| format!("publish watchlist for market {}", market.polymarket_id))?;
    }
    Ok(())
}

/// Spawn the publisher task. It returns a `PublisherReport` when the
/// scheduled duration elapses.
pub fn spawn(
    mut redis_pub: StreamPublisher,
    markets: Vec<SyntheticMarket>,
    publish_times: Arc<DashMap<Vec<u8>, Instant>>,
    rate_per_min: u64,
    duration_secs: u64,
) -> tokio::task::JoinHandle<anyhow::Result<PublisherReport>> {
    tokio::spawn(async move {
        if rate_per_min == 0 {
            anyhow::bail!("rate_per_min must be > 0");
        }
        // Spacing in nanoseconds between consecutive publishes.
        let interval_ns = (60_000_000_000u64) / rate_per_min;
        let interval_dur = Duration::from_nanos(interval_ns);
        let total = Duration::from_secs(duration_secs);

        let started_at = Instant::now();
        let mut ticker = interval_at(tokio::time::Instant::now(), interval_dur);
        // Burst: catch up after a slow tick so a publisher hiccup doesn't
        // permanently lower the achieved rate. This is the right knob for
        // a load generator (vs `Skip` which bias-hides slow stretches).
        ticker.set_missed_tick_behavior(MissedTickBehavior::Burst);

        let mut counter: u64 = 0;
        let mut next_market: usize = 0;

        loop {
            ticker.tick().await;
            let elapsed = started_at.elapsed();
            if elapsed >= total {
                break;
            }

            counter += 1;
            let market = &markets[next_market];
            next_market = (next_market + 1) % markets.len();

            let tx_hash = encode_tx_hash(counter);
            let mock = MockMempoolTx {
                message_id: Some(uuid_v7_like(counter)),
                market: Some(market.to_market_id()),
                kind: TriggerKind::LargeFill as i32,
                tx_hash: Bytes::copy_from_slice(&tx_hash),
                // observed_at_ns is per-host monotonic ns; we deliberately
                // do NOT use it for latency measurement (publisher and
                // sniper-ingest live in different processes and may have
                // different monotonic origins). Keep it populated so
                // sniper-ingest's mock path stays well-typed.
                observed_at_ns: 0,
            };

            // Record publish time BEFORE the publish so a slow XADD only
            // makes us look slower (conservative — never falsely under-
            // reports latency).
            let publish_at = Instant::now();
            publish_times.insert(tx_hash.to_vec(), publish_at);

            if let Err(e) = redis_pub.publish(MOCK_MEMPOOL_TOPIC, &mock).await {
                warn!(error = %e, counter, "publish failed; dropping tx and continuing");
                publish_times.remove(&tx_hash.to_vec());
            }
        }

        let elapsed = started_at.elapsed();
        let elapsed_ms = elapsed.as_millis() as u64;
        let actual_rate_per_min = if elapsed.as_secs_f64() > 0.0 {
            (counter as f64) / elapsed.as_secs_f64() * 60.0
        } else {
            0.0
        };

        info!(
            published = counter,
            elapsed_ms, actual_rate_per_min, "publisher loop finished"
        );

        Ok(PublisherReport {
            published: counter,
            elapsed_ms,
            actual_rate_per_min,
        })
    })
}

/// Encode the counter into a 32-byte synthetic tx_hash. Big-endian into
/// the trailing 8 bytes — leaves the high bytes zero so a hex dump is
/// readable, and guarantees uniqueness within `u64::MAX` publishes.
fn encode_tx_hash(counter: u64) -> [u8; 32] {
    let mut hash = [0u8; 32];
    hash[24..].copy_from_slice(&counter.to_be_bytes());
    hash
}

/// Build a UUIDv7-shaped `Uuid` deterministically from a counter. Used
/// for `message_id` fields where idempotency is required but ordering
/// against wall-clock UUIDv7 is not. (We don't pull `uuid::Uuid::now_v7`
/// here because reproducibility is more useful for debugging a harness
/// run than wall-clock ordering.)
fn uuid_v7_like(counter: u64) -> Uuid {
    let mut bytes = [0u8; 16];
    bytes[0..8].copy_from_slice(&counter.to_be_bytes());
    bytes[8] = 0x70; // version-7 nibble in the right slot
    bytes[9] = 0xa0; // RFC 4122 variant nibble
    Uuid {
        value: prost::bytes::Bytes::copy_from_slice(&bytes),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_tx_hash_is_unique() {
        let a = encode_tx_hash(1);
        let b = encode_tx_hash(2);
        assert_ne!(a, b);
        assert_eq!(a.len(), 32);
    }

    #[test]
    fn encode_tx_hash_round_trip() {
        let h = encode_tx_hash(0xdead_beef);
        let last8: [u8; 8] = h[24..].try_into().unwrap();
        assert_eq!(u64::from_be_bytes(last8), 0xdead_beef);
    }

    #[test]
    fn uuid_v7_like_has_version_and_variant() {
        let u = uuid_v7_like(42);
        assert_eq!(u.value.len(), 16);
        assert_eq!(u.value[8], 0x70);
        assert_eq!(u.value[9], 0xa0);
    }
}
