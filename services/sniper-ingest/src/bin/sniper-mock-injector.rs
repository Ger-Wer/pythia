use std::time::Duration;

use clap::{Parser, ValueEnum};
use dual_loop_common::abi::addresses;
use dual_loop_common::bus::StreamPublisher;
use dual_loop_common::proto::common::{MarketId, Money, Side};
use dual_loop_common::proto::mock::MockMempoolTx;
use dual_loop_common::proto::new_uuid_v7;
use dual_loop_common::proto::signal::{Signal, SignalPriority};
use dual_loop_common::proto::watchlist::{TriggerKind, TriggerPolicy, WatchlistUpsert};
use dual_loop_common::topics;
use prost::bytes::Bytes;
use serde::Serialize;
use sniper_ingest::mempool::monotonic_ns;
use sniper_ingest::mock::MOCK_MEMPOOL_TOPIC;

#[derive(Clone, Debug, ValueEnum)]
enum MockKind {
    LargeFill,
    OracleResolution,
    AdminAction,
    NewsConfirmed,
}

impl MockKind {
    fn as_trigger_kind(&self) -> TriggerKind {
        match self {
            Self::LargeFill => TriggerKind::LargeFill,
            Self::OracleResolution => TriggerKind::OracleResolution,
            Self::AdminAction => TriggerKind::AdminAction,
            Self::NewsConfirmed => TriggerKind::NewsConfirmed,
        }
    }
}

#[derive(Debug, Parser)]
#[command(name = "sniper-mock-injector")]
#[command(about = "Publish a mock Dual-Loop Signal + Watchlist + mempool tx for Paper Mode E2E")]
struct Cli {
    #[arg(long)]
    redis_url: Option<String>,

    #[arg(long, default_value = "paper-e2e-001")]
    market_id: String,

    #[arg(long, default_value = "Will the Paper Mode mock execution complete end-to-end?")]
    market_question: String,

    #[arg(long)]
    ctf_address: Option<String>,

    #[arg(long, default_value_t = 1)]
    version: u64,

    #[arg(long, value_enum, default_value_t = MockKind::LargeFill)]
    kind: MockKind,

    #[arg(long, default_value_t = 150_000)]
    edge_ppm: i64,

    #[arg(long, default_value_t = 720_000)]
    p_fundamental_ppm: i64,

    #[arg(long, default_value_t = 540_000)]
    p_market_ppm: i64,

    #[arg(long, default_value_t = 680_000)]
    confidence_low_ppm: i64,

    #[arg(long, default_value_t = 760_000)]
    confidence_high_ppm: i64,

    #[arg(long, default_value_t = 40)]
    expected_value_bps: i64,

    #[arg(long, default_value_t = 5_000_000)]
    suggested_position_micros: i64,

    #[arg(long, default_value_t = 5_000_000)]
    max_position_usd_micros: i64,

    #[arg(long, default_value_t = 25)]
    max_slippage_bps: u32,

    #[arg(long, default_value_t = 600_000)]
    ttl_ms: i64,

    #[arg(long, default_value_t = 1_500)]
    settle_delay_ms: u64,
}

#[derive(Serialize)]
struct InjectorSummary {
    market_id: String,
    ctf_address: String,
    signal_topic: &'static str,
    watchlist_topic: &'static str,
    mock_topic: &'static str,
    tx_hash_hex: String,
    signal_stream_id: String,
    watchlist_stream_id: String,
    mock_stream_id: String,
}

fn synthetic_tx_hash(seed: &Bytes) -> Bytes {
    let mut raw = [0u8; 32];
    let copy_len = seed.len().min(16);
    raw[..copy_len].copy_from_slice(&seed[..copy_len]);
    raw[16..(16 + copy_len)].copy_from_slice(&seed[..copy_len]);
    Bytes::copy_from_slice(&raw)
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    let redis_url = cli
        .redis_url
        .clone()
        .or_else(|| std::env::var("REDIS_URL").ok())
        .unwrap_or_else(|| "redis://127.0.0.1:6379".to_string());
    let mut publisher = StreamPublisher::connect(&redis_url).await?;

    let issued_at_unix_ms = dual_loop_common::proto::now_unix_ms();
    let signal_id = format!("sig-{}", cli.market_id);
    let ctf_address = cli
        .ctf_address
        .unwrap_or_else(|| format!("{:#x}", addresses::POLYMARKET_CTF_EXCHANGE));
    let market = MarketId {
        polymarket_id: cli.market_id.clone(),
        ctf_address: ctf_address.clone(),
    };
    let message_id = new_uuid_v7();
    let tx_hash = synthetic_tx_hash(&message_id.value);
    let trigger_kind = cli.kind.as_trigger_kind();

    let signal = Signal {
        message_id: Some(new_uuid_v7()),
        signal_id,
        server_version: cli.version,
        market: Some(market.clone()),
        market_question: cli.market_question.clone(),
        edge_ppm: cli.edge_ppm,
        p_fundamental_ppm: cli.p_fundamental_ppm,
        p_market_ppm: cli.p_market_ppm,
        confidence_low_ppm: cli.confidence_low_ppm,
        confidence_high_ppm: cli.confidence_high_ppm,
        expected_value_bps: cli.expected_value_bps,
        side_hint: Side::Yes as i32,
        suggested_position: Some(Money {
            amount_micros: cli.suggested_position_micros,
        }),
        expires_at_unix_ms: issued_at_unix_ms + cli.ttl_ms,
        llm_rationale: "Mock Paper Mode signal injected for end-to-end validation.".to_string(),
        evidence_urls: vec!["https://example.test/mock-paper-e2e".to_string()],
        issued_at_unix_ms,
        priority: SignalPriority::High as i32,
    };

    let watchlist = WatchlistUpsert {
        message_id: Some(new_uuid_v7()),
        market: Some(market.clone()),
        version: cli.version,
        edge_ppm: cli.edge_ppm,
        p_fundamental_ppm: cli.p_fundamental_ppm,
        confidence_ppm: cli.confidence_high_ppm,
        side_hint: Side::Yes as i32,
        max_position_usd: Some(Money {
            amount_micros: cli.max_position_usd_micros,
        }),
        max_slippage_bps: cli.max_slippage_bps,
        ttl_ms: cli.ttl_ms,
        issued_at_unix_ms,
        trigger_policy: Some(TriggerPolicy {
            min_independent_sources: 1,
            allowed_kinds: vec![trigger_kind as i32],
            max_price_impact_bps: cli.max_slippage_bps,
        }),
    };

    let signal_stream_id = publisher.publish(topics::SIGNALS_EMITTED, &signal).await?;
    let watchlist_stream_id = publisher
        .publish(topics::WATCHLIST_UPSERT, &watchlist)
        .await?;

    tokio::time::sleep(Duration::from_millis(cli.settle_delay_ms)).await;

    let mock_tx = MockMempoolTx {
        message_id: Some(message_id),
        market: Some(market),
        kind: trigger_kind as i32,
        tx_hash: tx_hash.clone(),
        observed_at_ns: monotonic_ns(),
    };
    let mock_stream_id = publisher.publish(MOCK_MEMPOOL_TOPIC, &mock_tx).await?;

    let summary = InjectorSummary {
        market_id: cli.market_id,
        ctf_address,
        signal_topic: topics::SIGNALS_EMITTED,
        watchlist_topic: topics::WATCHLIST_UPSERT,
        mock_topic: MOCK_MEMPOOL_TOPIC,
        tx_hash_hex: hex::encode(tx_hash.as_ref()),
        signal_stream_id,
        watchlist_stream_id,
        mock_stream_id,
    };

    println!("{}", serde_json::to_string_pretty(&summary)?);
    Ok(())
}
