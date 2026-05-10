//! Synthetic load injector + end-to-end latency probe for Dual-Loop
//! Fast Loop (gates #4 and #8 of the Pythia Paper Operator Release).
//!
//! What this binary does, conceptually:
//!
//! 1. Pre-seeds `dl.v1.watchlist.upsert` with a small, deterministic set of
//!    synthetic markets. Each watchlist entry allows a single trigger kind
//!    (`LARGE_FILL`) and `min_independent_sources = 1`, so every matching
//!    mock tx fires a trigger.
//!
//! 2. Spawns a publisher task that injects `dual_loop.v1.MockMempoolTx`
//!    messages on `dl.v1.mempool.mock` (Redis Streams) at a configurable
//!    rate (`--rate-per-min`). Each tx carries a unique 32-byte `tx_hash`
//!    encoding a monotonic counter; the harness records `Instant::now()`
//!    at publish time keyed by that hash.
//!
//! 3. Spawns a consumer task that pulls `dl.v1.triggers.fired` from NATS
//!    JetStream (`DL_TRIGGERS` stream). For each trigger it looks up the
//!    `evidence_hash` (which is the original `tx_hash` for mock-injected
//!    txs — see `services/sniper-ingest/src/mock.rs::handle_mock_message`)
//!    and computes end-to-end latency = `receive - publish`, all measured
//!    on the harness's own `Instant` clock.
//!
//! 4. After `--duration-secs`, the publisher stops; the consumer keeps
//!    draining for `--drain-secs` more after the publisher signals stop.
//!    Final report:
//!    * total published / total observed / drop rate
//!    * p50 / p95 / p99 / max latency
//!    * a single-line JSON or human-readable summary on stdout
//!
//! 5. Unless `--no-assert` is set, asserts:
//!    * `drop_rate <= --max-drop-rate`        (default 0.0, gate #4)
//!    * `p95_latency_ms < --target-p95-ms`    (default 8.0, gate #8)
//!    * `actual_rate / target_rate >= --min-rate-ratio`  (publisher kept up)
//!
//!    Any failure exits with code 1 so CI fails loudly.
//!
//! Why measure end-to-end on the harness clock, not via Prometheus pull?
//!   * The matcher histogram (`trigger_matcher_latency_ns`, recorded in
//!     PR-A) only covers the matcher's hot path. The gate is
//!     mempool→trigger, which includes Redis Streams round-trip,
//!     sniper-ingest's consumer loop, NATS publish + JetStream durable
//!     write, and the harness's NATS consumer delivery.
//!   * Prometheus pull would race sniper-ingest startup, need extra port
//!     mappings, and add CI flakes for no real signal gain.
//!   * Bus-level measurement is also strictly conservative (latency
//!     reported here is always >= the matcher's internal histogram), so
//!     passing the gate at this layer trivially passes any narrower
//!     internal gate.
//!
//! Why a separate `tools/` workspace, not under `services/`?
//!   * `services/Dockerfile` only copies `services/**`. Keeping the
//!     harness in `tools/` guarantees it never bleeds into production
//!     images.
//!   * Tools are free to pull dev-only deps (clap, hdrhistogram) without
//!     polluting production hot-path crates.

mod bus;
mod cli;
mod consumer;
mod publisher;
mod stats;
mod synth;

use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Context;
use clap::Parser;
use dashmap::DashMap;
use tokio::time::sleep;
use tracing::{info, warn};

use cli::{Cli, OutputFormat};
use stats::{LatencySample, RunReport};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    init_tracing();
    let cli = Cli::parse();
    cli.validate().context("invalid cli arguments")?;

    info!(
        rate_per_min = cli.rate_per_min,
        duration_secs = cli.duration_secs,
        markets = cli.markets,
        target_p95_ms = cli.target_p95_ms,
        max_drop_rate = cli.max_drop_rate,
        warmup_secs = cli.warmup_secs,
        drain_secs = cli.drain_secs,
        redis_url = %cli.redis_url,
        nats_url = %cli.nats_url,
        no_assert = cli.no_assert,
        "load_harness starting"
    );

    // ----- bus connect & stream/consumer setup -----
    let mut redis_pub = bus::connect_redis(&cli.redis_url).await?;
    let nats_client = bus::connect_nats(&cli.nats_url).await?;
    bus::ensure_triggers_stream(&nats_client).await?;

    // ----- watchlist preload -----
    let markets = synth::synthetic_markets(cli.markets);
    publisher::preload_watchlist(&mut redis_pub, &markets).await?;
    info!(
        count = markets.len(),
        "watchlist preloaded; settling for {}s", cli.warmup_secs
    );
    sleep(Duration::from_secs(cli.warmup_secs)).await;

    // ----- shared state -----
    let publish_times: Arc<DashMap<Vec<u8>, Instant>> = Arc::new(DashMap::new());
    let (stop_tx, stop_rx) = consumer::stop_channel();

    // ----- consumer task (NATS JetStream pull) -----
    let consumer_handle = consumer::spawn(
        nats_client.clone(),
        publish_times.clone(),
        stop_rx,
        cli.drain_secs,
    )
    .await?;

    // ----- publisher task (Redis Streams) -----
    let publisher_handle = publisher::spawn(
        redis_pub,
        markets.clone(),
        publish_times.clone(),
        cli.rate_per_min,
        cli.duration_secs,
    );

    // Wait for publisher to finish its scheduled run.
    let publisher_report = publisher_handle
        .await
        .context("publisher task panicked")??;
    info!(
        published = publisher_report.published,
        elapsed_ms = publisher_report.elapsed_ms,
        actual_rate_per_min = publisher_report.actual_rate_per_min,
        "publisher finished, draining triggers for {}s",
        cli.drain_secs
    );

    // Tell the consumer to drain then stop.
    stop_tx.send(true).ok();

    let samples: Vec<LatencySample> = consumer_handle.await.context("consumer task panicked")??;

    // ----- aggregate -----
    let report = RunReport::compute(&publisher_report, &samples)?;
    emit_report(&report, cli.output)?;

    if cli.no_assert {
        info!("--no-assert set; skipping gate checks");
        return Ok(());
    }

    enforce_gates(&report, &cli)
}

fn init_tracing() {
    use tracing_subscriber::EnvFilter;
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .try_init();
}

fn emit_report(report: &RunReport, output: OutputFormat) -> anyhow::Result<()> {
    match output {
        OutputFormat::Human => {
            println!("{}", report.human());
        }
        OutputFormat::Json => {
            println!("{}", serde_json::to_string(report)?);
        }
    }
    Ok(())
}

fn enforce_gates(report: &RunReport, cli: &Cli) -> anyhow::Result<()> {
    let mut failures: Vec<String> = Vec::new();

    if report.drop_rate > cli.max_drop_rate {
        failures.push(format!(
            "drop_rate {:.4} > --max-drop-rate {:.4} (published={}, observed={}, missing={})",
            report.drop_rate,
            cli.max_drop_rate,
            report.published,
            report.observed,
            report.published.saturating_sub(report.observed),
        ));
    }

    if report.observed > 0 {
        if report.p95_ms > cli.target_p95_ms {
            failures.push(format!(
                "p95_latency_ms {:.3} > --target-p95-ms {:.3} (gate #8)",
                report.p95_ms, cli.target_p95_ms,
            ));
        }
    } else {
        warn!("no triggers observed; latency assertion skipped (drop_rate already covered it)");
    }

    let target_rate_per_min = cli.rate_per_min as f64;
    let achieved_ratio = if target_rate_per_min > 0.0 {
        report.actual_rate_per_min / target_rate_per_min
    } else {
        1.0
    };
    if achieved_ratio < cli.min_rate_ratio {
        failures.push(format!(
            "publisher kept only {:.1}% of target rate ({:.1}/min < {:.1}/min × {:.1}%) — couldn't sustain --rate-per-min",
            achieved_ratio * 100.0,
            report.actual_rate_per_min,
            target_rate_per_min,
            cli.min_rate_ratio * 100.0,
        ));
    }

    if !failures.is_empty() {
        for failure in &failures {
            eprintln!("FAIL: {failure}");
        }
        anyhow::bail!("{} load-harness gate(s) failed", failures.len());
    }

    info!("all gates passed");
    Ok(())
}
