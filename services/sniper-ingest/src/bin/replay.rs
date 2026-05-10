//! Replay harness for mempool dumps.
//!
//! Reads a JSONL file of recorded pending transactions and feeds them
//! through the [`TriggerMatcher`] at real (or scaled) timing. Emits
//! triggered events as JSONL to stdout for diff-ing against baselines.
//!
//! ## Determinism modes (gate #9)
//!
//! Two flags work together to make replay output **byte-for-byte identical**
//! across re-runs and across machines:
//!
//! * `--clock-mode frozen` — replaces `Instant::now()` and wall-clock time
//!   inside the matcher with a [`FrozenClock`]. Eliminates run-to-run latency
//!   jitter and machine-clock differences.
//! * `--deterministic-ids` — replaces UUIDv7 (which embeds wall-clock + RNG
//!   tail) with [`DeterministicIdGenerator`] (BLAKE3(seed || counter)).
//!
//! Combined with `--watchlist PATH` (so triggers actually fire) and the
//! `tick_index` field (instead of the run-dependent `latency_ns`), replays
//! produce stable, diff-able output.
//!
//! ## Usage
//!
//! ```text
//! replay \
//!   --dump tests/fixtures/replay/dump.jsonl \
//!   --watchlist tests/fixtures/replay/watchlist.jsonl \
//!   --clock-mode frozen \
//!   --deterministic-ids
//! ```

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::{Parser, ValueEnum};
use serde::{Deserialize, Serialize};
use sniper_ingest::matcher::{
    DeterministicIdGenerator, FrozenClock, IdGenerator, MatcherClock, RealClock, TriggerMatcher,
    UuidV7Generator,
};
use sniper_ingest::watchlist::WatchlistStore;

use alloy_primitives::Address;
use dual_loop_common::abi;
use dual_loop_common::proto::common::{MarketId, Money};
use dual_loop_common::proto::watchlist::{TriggerKind, TriggerPolicy, WatchlistUpsert};

/// Replay mempool dumps through the trigger matcher.
#[derive(Parser, Debug)]
#[command(name = "replay", about = "Replay mempool dumps through TriggerMatcher")]
struct Cli {
    /// Path to the JSONL mempool dump file.
    #[arg(long)]
    dump: PathBuf,

    /// Optional path to a JSONL watchlist file. Each line is a watchlist
    /// entry (see `WatchlistEntryJson`). Without this, no triggers fire.
    #[arg(long)]
    watchlist: Option<PathBuf>,

    /// Clock mode: `wall` (production timing — `Instant::now`,
    /// `StampedTime::now`) or `frozen` (deterministic — fixed wall time,
    /// monotonic ticks per call).
    #[arg(long, value_enum, default_value_t = ClockMode::Wall)]
    clock_mode: ClockMode,

    /// Anchor wall-clock time (Unix epoch ms) for `--clock-mode frozen`.
    /// Defaults to `1_700_000_000_000` (Tue 2023-11-14T22:13:20Z) — chosen
    /// once, kept stable.
    #[arg(long, default_value_t = 1_700_000_000_000)]
    frozen_now_ms: i64,

    /// Use deterministic UUIDs (BLAKE3(seed || counter)) instead of UUIDv7.
    /// Required for byte-identical replay output across runs.
    #[arg(long)]
    deterministic_ids: bool,

    /// Seed label fed into the deterministic ID generator. Different labels
    /// yield different ID sequences; the same label yields the same sequence.
    #[arg(long, default_value = "replay")]
    deterministic_id_seed: String,

    /// Playback speed multiplier (1.0 = real-time, 10.0 = 10x faster).
    /// Ignored when `--clock-mode frozen` (replay runs as fast as possible).
    #[arg(long, default_value = "1.0")]
    speed: f64,

    /// Index of the first transaction to process (0-based).
    #[arg(long, default_value = "0")]
    start_tx: usize,

    /// Maximum number of transactions to process.
    #[arg(long, default_value = "18446744073709551615")]
    limit: u64,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum ClockMode {
    /// Production clock — `StampedTime::now` / `Instant::now`.
    Wall,
    /// Deterministic clock — wall-clock anchored at `--frozen-now-ms`.
    Frozen,
}

/// One line in the mempool dump JSONL.
#[derive(Debug, Deserialize)]
struct DumpEntry {
    ts_ns: u64,
    tx: serde_json::Value,
}

/// Output trigger record. `tick_index` (event ordinal in the input dump)
/// replaces the previous `latency_ns` field, which was non-deterministic
/// (depended on machine speed). Field order is fixed for stable diffs.
#[derive(Debug, Serialize)]
struct TriggerOutput {
    tick_index: u64,
    ts_ns: u64,
    market_id: String,
    ctf_address: String,
    kind: String,
    source_count: u32,
    issued_at_unix_ms: i64,
    watchlist_valid_until_unix_ms: i64,
    message_id_hex: String,
    evidence_hash_hex: String,
}

/// Human-writable JSON row for golden watchlist fixtures. Maps directly
/// onto `WatchlistUpsert`.
#[derive(Debug, Deserialize)]
struct WatchlistEntryJson {
    polymarket_id: String,
    ctf_address: String,
    #[serde(default)]
    market_specific_key: String,
    #[serde(default = "default_version")]
    version: u64,
    #[serde(default)]
    edge_ppm: i64,
    p_fundamental_ppm: i64,
    #[serde(default)]
    confidence_ppm: i64,
    #[serde(default)]
    side_hint: i32,
    #[serde(default = "default_max_position_micros")]
    max_position_micros: i64,
    #[serde(default)]
    max_slippage_bps: u32,
    /// Time-to-live in milliseconds (entry expires at `issued_at_unix_ms + ttl_ms`).
    #[serde(default = "default_ttl_ms")]
    ttl_ms: i64,
    /// Defaults to the replay frozen-now-ms anchor when missing.
    issued_at_unix_ms: Option<i64>,
    /// `min_independent_sources` for the trigger policy. 1 = fire on first
    /// matching event.
    #[serde(default = "default_min_sources")]
    min_independent_sources: u32,
    /// Allowed kinds as protobuf enum names (e.g. `LARGE_FILL`,
    /// `ORACLE_RESOLUTION`). Empty list = all kinds allowed.
    #[serde(default)]
    allowed_kinds: Vec<String>,
    #[serde(default)]
    max_price_impact_bps: u32,
}

fn default_version() -> u64 {
    1
}
fn default_max_position_micros() -> i64 {
    10_000_000
}
fn default_ttl_ms() -> i64 {
    3_600_000
}
fn default_min_sources() -> u32 {
    1
}

fn parse_trigger_kind(name: &str) -> Result<i32> {
    match name {
        "ORACLE_RESOLUTION" | "TRIGGER_KIND_ORACLE_RESOLUTION" => {
            Ok(TriggerKind::OracleResolution as i32)
        }
        "LARGE_FILL" | "TRIGGER_KIND_LARGE_FILL" => Ok(TriggerKind::LargeFill as i32),
        "ADMIN_ACTION" | "TRIGGER_KIND_ADMIN_ACTION" => Ok(TriggerKind::AdminAction as i32),
        "NEWS_CONFIRMED" | "TRIGGER_KIND_NEWS_CONFIRMED" => Ok(TriggerKind::NewsConfirmed as i32),
        other => Err(anyhow::anyhow!("unknown TriggerKind: {other}")),
    }
}

fn build_upsert(row: WatchlistEntryJson, default_issued_ms: i64) -> Result<WatchlistUpsert> {
    let allowed: Vec<i32> = row
        .allowed_kinds
        .iter()
        .map(|k| parse_trigger_kind(k))
        .collect::<Result<Vec<_>>>()?;
    Ok(WatchlistUpsert {
        message_id: None,
        market: Some(MarketId {
            polymarket_id: row.polymarket_id,
            ctf_address: row.ctf_address,
            market_specific_key: row.market_specific_key,
        }),
        version: row.version,
        edge_ppm: row.edge_ppm,
        p_fundamental_ppm: row.p_fundamental_ppm,
        confidence_ppm: row.confidence_ppm,
        side_hint: row.side_hint,
        max_position_usd: Some(Money {
            amount_micros: row.max_position_micros,
        }),
        max_slippage_bps: row.max_slippage_bps,
        ttl_ms: row.ttl_ms,
        issued_at_unix_ms: row.issued_at_unix_ms.unwrap_or(default_issued_ms),
        trigger_policy: Some(TriggerPolicy {
            min_independent_sources: row.min_independent_sources,
            allowed_kinds: allowed,
            max_price_impact_bps: row.max_price_impact_bps,
        }),
    })
}

fn load_watchlist(store: &WatchlistStore, path: &PathBuf, default_issued_ms: i64) -> Result<usize> {
    let file = std::fs::File::open(path)
        .with_context(|| format!("failed to open watchlist file: {}", path.display()))?;
    let reader = BufReader::new(file);
    let mut count = 0usize;
    for (idx, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("reading watchlist line {idx}"))?;
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let row: WatchlistEntryJson = serde_json::from_str(trimmed)
            .with_context(|| format!("parsing watchlist line {idx}: {trimmed}"))?;
        let upsert = build_upsert(row, default_issued_ms)?;
        store.apply_upsert(&upsert);
        count += 1;
    }
    Ok(count)
}

fn build_matcher(store: WatchlistStore, cli: &Cli) -> TriggerMatcher {
    let clock: Box<dyn MatcherClock> = match cli.clock_mode {
        ClockMode::Wall => Box::new(RealClock),
        // 1_000 ns step keeps the source-tracker sliding window meaningful
        // (its window is 2 seconds = 2_000_000_000 ns) while still rapidly
        // advancing the deterministic instant.
        ClockMode::Frozen => Box::new(FrozenClock::new(cli.frozen_now_ms, 1_000)),
    };
    let id_gen: Box<dyn IdGenerator> = if cli.deterministic_ids {
        Box::new(DeterministicIdGenerator::from_label(
            &cli.deterministic_id_seed,
        ))
    } else {
        Box::new(UuidV7Generator)
    };
    TriggerMatcher::with_dependencies(store, clock, id_gen)
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        // Tracing logs go to stderr so they never pollute deterministic
        // stdout output.
        .with_writer(std::io::stderr)
        .init();

    let cli = Cli::parse();
    let speed = cli.speed.max(0.001);
    let frozen = matches!(cli.clock_mode, ClockMode::Frozen);

    let file = std::fs::File::open(&cli.dump)
        .with_context(|| format!("failed to open dump file: {}", cli.dump.display()))?;
    let reader = BufReader::new(file);

    let store = WatchlistStore::new();
    if let Some(ref wl) = cli.watchlist {
        let loaded = load_watchlist(&store, wl, cli.frozen_now_ms)?;
        tracing::info!(loaded, "watchlist preloaded");
    }
    let mut matcher = build_matcher(store, &cli);

    let stdout = std::io::stdout();
    let mut out = stdout.lock();

    let mut prev_ts: Option<u64> = None;
    let mut processed = 0u64;
    // Monotonic ordinal: counts every event we see (after start_tx skip),
    // not every trigger fired. Replaces the non-deterministic `latency_ns`
    // field in `TriggerOutput`.
    let mut tick_index: u64 = 0;

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

        // Timing: only sleep in wall-clock mode. Frozen mode runs at full
        // speed and relies on `tick_index` for ordering.
        if !frozen {
            if let Some(prev) = prev_ts {
                if entry.ts_ns > prev {
                    let delta_ns = entry.ts_ns - prev;
                    let sleep_ns = (delta_ns as f64 / speed) as u64;
                    if sleep_ns > 1_000 {
                        std::thread::sleep(Duration::from_nanos(sleep_ns));
                    }
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
                        if let Some(trigger) = matcher.try_match(&decoded, log_addr, entry.ts_ns) {
                            let market_id = trigger
                                .market
                                .as_ref()
                                .map(|m| m.polymarket_id.clone())
                                .unwrap_or_default();
                            let ctf_address = trigger
                                .market
                                .as_ref()
                                .map(|m| m.ctf_address.clone())
                                .unwrap_or_default();
                            let message_id_hex = trigger
                                .message_id
                                .as_ref()
                                .map(|u| hex::encode(&u.value))
                                .unwrap_or_default();
                            let evidence_hash_hex = hex::encode(&trigger.evidence_hash);
                            let output = TriggerOutput {
                                tick_index,
                                ts_ns: entry.ts_ns,
                                market_id,
                                ctf_address,
                                kind: format!("{:?}", trigger.kind),
                                source_count: trigger.source_count,
                                issued_at_unix_ms: trigger.issued_at_unix_ms,
                                watchlist_valid_until_unix_ms: trigger
                                    .watchlist_valid_until_unix_ms,
                                message_id_hex,
                                evidence_hash_hex,
                            };
                            serde_json::to_writer(&mut out, &output).ok();
                            writeln!(out).ok();
                        }
                    }
                }
            }
        }

        tick_index += 1;
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
