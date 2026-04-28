//! Replay harness for mempool dumps.
//!
//! Reads a JSONL file of recorded pending transactions and feeds them
//! through the TriggerMatcher at real (or scaled) timings. Emits
//! triggered events as JSONL to stdout for diff-ing against baselines.
//!
//! Usage:
//!   replay --dump mempool.jsonl --speed 1.0 --start-tx 0 --limit 10000

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::Parser;
use serde::{Deserialize, Serialize};
use sniper_ingest::matcher::TriggerMatcher;
use sniper_ingest::watchlist::WatchlistStore;

use alloy_primitives::Address;
use dual_loop_common::abi;

/// Replay mempool dumps through the trigger matcher.
#[derive(Parser, Debug)]
#[command(name = "replay", about = "Replay mempool dumps through TriggerMatcher")]
struct Cli {
    /// Path to the JSONL mempool dump file.
    #[arg(long)]
    dump: PathBuf,

    /// Playback speed multiplier (1.0 = real-time, 10.0 = 10x faster).
    #[arg(long, default_value = "1.0")]
    speed: f64,

    /// Index of the first transaction to process (0-based).
    #[arg(long, default_value = "0")]
    start_tx: usize,

    /// Maximum number of transactions to process.
    #[arg(long, default_value = "18446744073709551615")]
    limit: u64,
}

/// One line in the mempool dump JSONL.
#[derive(Debug, Deserialize)]
struct DumpEntry {
    ts_ns: u64,
    tx: serde_json::Value,
}

/// Output trigger record.
#[derive(Debug, Serialize)]
struct TriggerOutput {
    ts_ns: u64,
    market_id: String,
    kind: String,
    source_count: u32,
    latency_ns: u64,
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cli = Cli::parse();
    let speed = cli.speed.max(0.001);

    let file = std::fs::File::open(&cli.dump)
        .with_context(|| format!("failed to open dump file: {}", cli.dump.display()))?;
    let reader = BufReader::new(file);

    let store = WatchlistStore::new();
    // In production the store would be populated from the bus. For replay,
    // users should pre-populate via a separate watchlist dump or the store
    // starts empty (no triggers fire unless entries are loaded).
    let mut matcher = TriggerMatcher::new(store);

    let stdout = std::io::stdout();
    let mut out = stdout.lock();

    let mut prev_ts: Option<u64> = None;
    let mut processed = 0u64;

    for (idx, line) in reader.lines().enumerate() {
        if idx < cli.start_tx {
            continue;
        }
        if processed >= cli.limit {
            break;
        }

        let line = line.context("reading dump line")?;
        if line.is_empty() {
            continue;
        }

        let entry: DumpEntry = match serde_json::from_str(&line) {
            Ok(e) => e,
            Err(e) => {
                tracing::warn!(line_num = idx, err = %e, "skipping malformed dump line");
                continue;
            }
        };

        // Timing: sleep the delta between consecutive timestamps.
        if let Some(prev) = prev_ts {
            if entry.ts_ns > prev {
                let delta_ns = entry.ts_ns - prev;
                let sleep_ns = (delta_ns as f64 / speed) as u64;
                if sleep_ns > 1_000 {
                    std::thread::sleep(Duration::from_nanos(sleep_ns));
                }
            }
        }
        prev_ts = Some(entry.ts_ns);

        // Extract `to` address from the tx JSON to determine target contract.
        let contract_addr = entry
            .tx
            .get("to")
            .and_then(|v| v.as_str())
            .and_then(|s| s.parse::<Address>().ok())
            .unwrap_or(Address::ZERO);

        // Extract logs if present (for receipt-based replay).
        if let Some(logs) = entry.tx.get("logs").and_then(|v| v.as_array()) {
            for log_val in logs {
                if let Ok(log_data) = parse_log_data(log_val) {
                    let log_addr = log_val
                        .get("address")
                        .and_then(|v| v.as_str())
                        .and_then(|s| s.parse::<Address>().ok())
                        .unwrap_or(contract_addr);

                    if let Some(decoded) = abi::try_decode_log(log_addr, &log_data) {
                        let start = sniper_ingest::mempool::monotonic_ns();
                        if let Some(trigger) = matcher.try_match(&decoded, log_addr, entry.ts_ns) {
                            let latency_ns = sniper_ingest::mempool::monotonic_ns() - start;
                            let market_id = trigger
                                .market
                                .as_ref()
                                .map(|m| m.polymarket_id.clone())
                                .unwrap_or_default();
                            let output = TriggerOutput {
                                ts_ns: entry.ts_ns,
                                market_id,
                                kind: format!("{:?}", trigger.kind),
                                source_count: trigger.source_count,
                                latency_ns,
                            };
                            serde_json::to_writer(&mut out, &output).ok();
                            writeln!(out).ok();
                        }
                    }
                }
            }
        }

        processed += 1;
    }

    tracing::info!(processed, "replay complete");
    Ok(())
}

fn parse_log_data(log_val: &serde_json::Value) -> Result<alloy_primitives::LogData> {
    use alloy_primitives::B256;

    let topics: Vec<B256> = log_val
        .get("topics")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|t| t.as_str()?.parse::<B256>().ok())
                .collect()
        })
        .unwrap_or_default();

    let data_hex = log_val.get("data").and_then(|v| v.as_str()).unwrap_or("0x");
    let data_bytes = hex_decode(data_hex)?;

    alloy_primitives::LogData::new(topics, data_bytes.into())
        .ok_or_else(|| anyhow::anyhow!("invalid log data"))
}

fn hex_decode(s: &str) -> Result<Vec<u8>> {
    let s = s.strip_prefix("0x").unwrap_or(s);
    if s.is_empty() {
        return Ok(vec![]);
    }
    Ok(hex::decode(s)?)
}
