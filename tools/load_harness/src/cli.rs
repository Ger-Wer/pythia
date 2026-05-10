//! CLI surface for `load_harness`.

use clap::{Parser, ValueEnum};

#[derive(Parser, Debug, Clone)]
#[command(
    name = "load_harness",
    about = "Synthetic load injector + E2E latency probe for Dual-Loop (gates #4 #8)"
)]
pub struct Cli {
    /// Redis connection string used for publishing watchlist + mock mempool tx.
    #[arg(long, default_value = "redis://127.0.0.1:6379")]
    pub redis_url: String,

    /// NATS connection string used for consuming `dl.v1.triggers.fired`.
    #[arg(long, default_value = "nats://127.0.0.1:4222")]
    pub nats_url: String,

    /// Target publish rate (mock mempool transactions per minute). The
    /// publisher schedules ticks `60_000 / rate_per_min` ms apart and uses
    /// `tokio::time::interval` with `MissedTickBehavior::Burst` to catch up
    /// after backpressure spikes.
    #[arg(long, default_value_t = 1_000)]
    pub rate_per_min: u64,

    /// Total duration of the publisher loop, in seconds. The consumer
    /// continues for `--drain-secs` more after the publisher stops.
    #[arg(long, default_value_t = 60)]
    pub duration_secs: u64,

    /// p95 end-to-end latency assertion threshold, in ms. Default closes
    /// gate #8 (mempool→trigger p95 < 8ms).
    #[arg(long, default_value_t = 8.0)]
    pub target_p95_ms: f64,

    /// Maximum allowed drop rate (`(published - observed) / published`).
    /// Default 0.0 closes gate #4 ("zero drops at 1k/min").
    #[arg(long, default_value_t = 0.0)]
    pub max_drop_rate: f64,

    /// Minimum publisher achieved-rate ratio (achieved / target). Set
    /// below 1.0 to allow some scheduler slop on slow CI runners while
    /// still failing if the publisher gives up entirely.
    #[arg(long, default_value_t = 0.9)]
    pub min_rate_ratio: f64,

    /// How many distinct synthetic markets to spread the load across.
    /// Each market gets its own `ctf_address`, watchlist entry, and the
    /// publisher round-robins through them.
    #[arg(long, default_value_t = 4)]
    pub markets: usize,

    /// Settle delay between watchlist preload and the start of the
    /// publisher loop. Gives sniper-ingest time to consume + apply
    /// upserts before the first mock tx arrives.
    #[arg(long, default_value_t = 2)]
    pub warmup_secs: u64,

    /// How long to keep the trigger consumer alive after the publisher
    /// stops, to drain in-flight triggers from JetStream.
    #[arg(long, default_value_t = 5)]
    pub drain_secs: u64,

    /// Print stats but do NOT fail on threshold breaches. Useful for
    /// local exploration and for the CI smoke job that uses relaxed
    /// thresholds.
    #[arg(long)]
    pub no_assert: bool,

    /// Output format for the final stats report.
    #[arg(long, value_enum, default_value_t = OutputFormat::Human)]
    pub output: OutputFormat,
}

#[derive(Copy, Clone, Debug, ValueEnum)]
pub enum OutputFormat {
    Human,
    Json,
}

impl Cli {
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.rate_per_min == 0 {
            anyhow::bail!("--rate-per-min must be > 0");
        }
        if self.duration_secs == 0 {
            anyhow::bail!("--duration-secs must be > 0");
        }
        if self.markets == 0 {
            anyhow::bail!("--markets must be > 0");
        }
        if !(0.0..=1.0).contains(&self.max_drop_rate) {
            anyhow::bail!("--max-drop-rate must be in [0.0, 1.0]");
        }
        if !(0.0..=1.0).contains(&self.min_rate_ratio) {
            anyhow::bail!("--min-rate-ratio must be in [0.0, 1.0]");
        }
        if self.target_p95_ms <= 0.0 {
            anyhow::bail!("--target-p95-ms must be > 0.0");
        }
        Ok(())
    }
}
