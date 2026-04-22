//! Cross-language contract test: TS publishes a `WatchlistUpsert` to Redis
//! Streams, Rust consumer decodes it and asserts the payload.
//!
//! Disabled by default (no live Redis in the default `cargo test` env).
//! The CI job `bus-contract-tests` sets `REDIS_URL` and runs
//! `cargo test --test cross_lang -- --ignored` after `bun run` publishes.
//! Locally: `make dev-up && make bus-cross-lang-test`.
//!
//! Required env vars:
//!   REDIS_URL      — e.g. redis://127.0.0.1:6379
//!   BUN_PUBLISHER  — path to the bun entrypoint; defaults to the repo
//!                    `scripts/contract/publish_watchlist_upsert.ts`.

use std::process::Command;
use std::time::Duration;

use dual_loop_common::bus::{ConsumerGroup, StreamPublisher};
use dual_loop_common::proto::watchlist::WatchlistUpsert;
use dual_loop_common::topics;

fn redis_url() -> Option<String> {
    std::env::var("REDIS_URL").ok()
}

fn bun_publisher_path() -> String {
    std::env::var("BUN_PUBLISHER").unwrap_or_else(|_| {
        // Assume repo root is two levels above services/common/.
        let manifest = env!("CARGO_MANIFEST_DIR");
        format!(
            "{}/../../scripts/contract/publish_watchlist_upsert.ts",
            manifest
        )
    })
}

fn unique_stream(base: &str) -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("{base}.xlang.{nanos}")
}

/// Issue a `FLUSHDB` to clean leftover streams between runs.
async fn flush(url: &str) -> anyhow::Result<()> {
    let client = redis::Client::open(url)?;
    let mut conn = client.get_multiplexed_async_connection().await?;
    let _: String = redis::cmd("FLUSHDB").query_async(&mut conn).await?;
    Ok(())
}

#[tokio::test]
#[ignore = "requires live Redis + bun; run via CI `bus-contract-tests` or `make bus-cross-lang-test`"]
async fn ts_publishes_rust_consumes_watchlist_upsert() {
    let Some(url) = redis_url() else {
        panic!("REDIS_URL must be set for cross_lang tests");
    };

    // Fresh DB for deterministic assertions.
    flush(&url).await.expect("flushdb");

    // Override the canonical topic with a per-run suffix so parallel CI
    // invocations don't step on each other. We pass it via env to the
    // bun script — but our bun script is hardcoded to the canonical topic,
    // so just rely on FLUSHDB + the canonical topic.
    let topic = topics::WATCHLIST_UPSERT;

    // 1. Start consumer first.
    let mut cg = ConsumerGroup::connect(&url, topic, "test-cross-lang", "consumer-1")
        .await
        .expect("consumer connect");

    // 2. Invoke the bun publisher.
    let bun_script = bun_publisher_path();
    let status = Command::new("bun")
        .arg("run")
        .arg(&bun_script)
        .env("REDIS_URL", &url)
        .status()
        .expect("spawn bun");
    assert!(status.success(), "bun publisher failed with {status:?}");

    // 3. Pull one message (retry until the reader sees it).
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    let msg = loop {
        let msgs = cg.read_next().await.expect("read_next");
        if let Some(m) = msgs.into_iter().next() {
            break m;
        }
        if std::time::Instant::now() >= deadline {
            panic!("timed out waiting for TS-published message on {topic}");
        }
    };

    // 4. Decode and assert.
    let upsert: WatchlistUpsert = msg.decode().expect("decode WatchlistUpsert");
    let market = upsert.market.as_ref().expect("market");
    assert_eq!(market.polymarket_id, "cross-lang-001");
    assert_eq!(market.ctf_address, "0xfeed");
    assert_eq!(upsert.version, 7);
    assert_eq!(upsert.edge_ppm, 42_000);
    assert_eq!(upsert.p_fundamental_ppm, 700_000);
    assert_eq!(upsert.confidence_ppm, 950_000);
    assert_eq!(upsert.side_hint, 1); // SIDE_YES
    let money = upsert.max_position_usd.as_ref().expect("max_position_usd");
    assert_eq!(money.amount_micros, 5_000_000);
    assert_eq!(upsert.max_slippage_bps, 25);
    assert_eq!(upsert.ttl_ms, 60_000);
    assert_eq!(upsert.issued_at_unix_ms, 1_700_000_000_000);
    assert_eq!(msg.trace_id.as_deref(), Some("cross-lang-test"));

    cg.ack(&msg.id).await.expect("ack");

    // Sanity: reusing the publisher from Rust should also work.
    let mut rp = StreamPublisher::connect(&url).await.expect("publisher");
    let _ = rp
        .publish(&unique_stream("rust-sanity"), &upsert)
        .await
        .expect("publish back");
}
