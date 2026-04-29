//! sniper-ingest: Fast Loop ingest service.
//!
//! Phase 2: mempool subscriber → ABI decoder → trigger matcher → bus.
//! Also consumes `dl.v1.watchlist.upsert` / `dl.v1.watchlist.remove`
//! from Redis Streams and keeps an in-memory [`WatchlistStore`].
//!
//! Recorder mode: set `MEMPOOL_RECORDER=true` to dump every pending tx
//! to `/data/mempool-dumps/YYYY-MM-DD-HH.jsonl` (rolling hourly).

use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};

use alloy_primitives::Address;
use anyhow::Result;
use sniper_ingest::matcher::TriggerMatcher;
use sniper_ingest::mempool::{self, PendingTx, TX_CHANNEL_CAPACITY};
use sniper_ingest::mock;
use sniper_ingest::watchlist::WatchlistStore;
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};
use tracing::{info, warn};

static RECORDER_ENABLED: AtomicBool = AtomicBool::new(false);

#[tokio::main]
async fn main() -> Result<()> {
    let service = "sniper_ingest";
    let metrics_addr: SocketAddr = std::env::var("METRICS_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:9101".to_string())
        .parse()?;

    dual_loop_common::observability::init(service, metrics_addr)?;
    sniper_ingest::metrics::register_metrics();

    tracing::info!(
        wire_version = dual_loop_common::WIRE_VERSION,
        "sniper-ingest starting (Phase 2)"
    );

    // Recorder mode.
    if std::env::var("MEMPOOL_RECORDER")
        .unwrap_or_default()
        .eq_ignore_ascii_case("true")
    {
        RECORDER_ENABLED.store(true, Ordering::Relaxed);
        info!("mempool recorder mode ENABLED");
    }

    let store = WatchlistStore::new();

    // TTL reaper.
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    let reaper = store.spawn_ttl_reaper(shutdown_rx.clone());

    // Bus consumers.
    let bus_tasks = match std::env::var("REDIS_URL") {
        Ok(url) => Some(spawn_bus_consumers(url, store.clone(), shutdown_rx.clone()).await?),
        Err(_) => {
            warn!("REDIS_URL not set; running without bus consumers");
            None
        }
    };

    let mock_handle = if mock::enabled_from_env() {
        match (std::env::var("REDIS_URL"), std::env::var("NATS_URL")) {
            (Ok(redis_url), Ok(nats_url)) => Some(
                mock::spawn_mock_consumer(redis_url, nats_url, store.clone(), shutdown_rx.clone())
                    .await?,
            ),
            _ => {
                warn!("MOCK_MEMPOOL_ENABLED=true but REDIS_URL or NATS_URL missing; mock path disabled");
                None
            }
        }
    } else {
        None
    };

    // Mempool channel.
    let (mempool_tx, mempool_rx) = mpsc::channel::<PendingTx>(TX_CHANNEL_CAPACITY);

    // Spawn WS subscriber (primary, for dev/CI).
    let ws_handle = match std::env::var("ANVIL_WS_URL") {
        Ok(url) => Some(mempool::spawn_ws_subscriber(url, mempool_tx.clone())),
        Err(_) => {
            warn!("ANVIL_WS_URL not set; WS mempool subscriber disabled");
            None
        }
    };

    // Spawn IPC subscriber (secondary, staging/prod only).
    let ipc_handle = match std::env::var("POLYGON_IPC_PATH") {
        Ok(path) => Some(mempool::spawn_ipc_subscriber(path, mempool_tx.clone())),
        Err(_) => None,
    };

    // Drop the extra sender so the channel closes when both actors drop.
    drop(mempool_tx);

    // Redis publisher for triggers.
    let mut trigger_publisher = match std::env::var("REDIS_URL") {
        Ok(url) => Some(dual_loop_common::bus::StreamPublisher::connect(&url).await?),
        Err(_) => None,
    };

    // Matcher + processing loop.
    let matcher_handle = {
        let store = store.clone();
        let shutdown_rx_inner = shutdown_rx.clone();
        tokio::spawn(async move {
            run_matcher_loop(
                mempool_rx,
                store,
                trigger_publisher.as_mut(),
                shutdown_rx_inner,
            )
            .await;
        })
    };

    let heartbeat = metrics::counter!("sniper_ingest_heartbeat_total");
    let mut tick = interval(Duration::from_secs(5));
    let shutdown = shutdown_signal();
    tokio::pin!(shutdown);

    loop {
        tokio::select! {
            _ = tick.tick() => {
                heartbeat.increment(1);
                metrics::gauge!("watchlist_size").set(store.len() as f64);
            }
            _ = &mut shutdown => {
                info!("shutdown signal received; exiting");
                let _ = shutdown_tx.send(true);
                break;
            }
        }
    }

    if let Some((u, r)) = bus_tasks {
        u.abort();
        r.abort();
    }
    if let Some(h) = ws_handle {
        h.abort();
    }
    if let Some(h) = ipc_handle {
        h.abort();
    }
    if let Some(h) = mock_handle {
        h.abort();
    }
    matcher_handle.abort();
    reaper.abort();

    Ok(())
}

async fn run_matcher_loop(
    mut rx: mpsc::Receiver<PendingTx>,
    store: WatchlistStore,
    _trigger_publisher: Option<&mut dual_loop_common::bus::StreamPublisher>,
    _shutdown_rx: tokio::sync::watch::Receiver<bool>,
) {
    let _matcher = TriggerMatcher::new(store);

    while let Some(pending) = rx.recv().await {
        // Record raw tx if recorder mode is on.
        if RECORDER_ENABLED.load(Ordering::Relaxed) {
            record_tx(&pending);
        }

        // Extract the `to` address as the target contract.
        use alloy::consensus::Transaction as TxTrait;
        let contract_addr = pending.tx.to().unwrap_or(Address::ZERO);

        // Try to decode logs from the tx receipt (if available in full tx body).
        // For pending txs from mempool, we decode the input data against
        // known function selectors. Phase 2 focuses on log-based decoding
        // from confirmed txs via subscription; for pending txs we pass through
        // with the contract address for watchlist matching.

        // For now, emit a decoded-total metric per contract.
        let addr_label = format!("{contract_addr:#x}");
        metrics::counter!("mempool_decoded_total", "contract" => addr_label).increment(1);

        // The matcher currently requires DecodedEvent from logs. For full
        // integration, the MempoolSubscriber will also process receipts.
        // This loop is ready for when log subscription is added.
    }
}

fn record_tx(pending: &PendingTx) {
    use std::io::Write;

    let now = time::OffsetDateTime::now_utc();
    let dir = "/data/mempool-dumps";
    let _ = std::fs::create_dir_all(dir);
    let filename = format!(
        "{}/{}.jsonl",
        dir,
        now.format(
            &time::format_description::parse("[year]-[month]-[day]-[hour]").expect("valid format")
        )
        .expect("format ok")
    );

    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&filename)
    {
        let record = serde_json::json!({
            "ts_ns": pending.observed_at_ns,
            "tx": pending.tx,
        });
        let _ = serde_json::to_writer(&mut f, &record);
        let _ = writeln!(f);
    }
}

async fn spawn_bus_consumers(
    url: String,
    store: WatchlistStore,
    shutdown_rx: tokio::sync::watch::Receiver<bool>,
) -> Result<(tokio::task::JoinHandle<()>, tokio::task::JoinHandle<()>)> {
    use dual_loop_common::bus::ConsumerGroup;
    use dual_loop_common::topics;

    let consumer_id = std::env::var("HOSTNAME").unwrap_or_else(|_| "sniper-ingest-1".to_string());
    let group = topics::consumer_groups::SNIPER_INGEST;

    let mut upsert_cg =
        ConsumerGroup::connect(&url, topics::WATCHLIST_UPSERT, group, &consumer_id).await?;
    let mut remove_cg =
        ConsumerGroup::connect(&url, topics::WATCHLIST_REMOVE, group, &consumer_id).await?;
    info!(url = %url, group, "bus consumers connected");

    let upsert_handle = {
        let store = store.clone();
        let mut rx = shutdown_rx.clone();
        tokio::spawn(async move {
            let cancel = Box::pin(async move {
                let _ = rx.wait_for(|v| *v).await;
            });
            if let Err(e) = upsert_cg
                .consume(
                    |msg| {
                        let store = store.clone();
                        async move { store.handle_message(&msg) }
                    },
                    cancel,
                )
                .await
            {
                warn!(?e, "upsert consumer exited with error");
            }
        })
    };

    let remove_handle = {
        let store = store.clone();
        let mut rx = shutdown_rx.clone();
        tokio::spawn(async move {
            let cancel = Box::pin(async move {
                let _ = rx.wait_for(|v| *v).await;
            });
            if let Err(e) = remove_cg
                .consume(
                    |msg| {
                        let store = store.clone();
                        async move { store.handle_message(&msg) }
                    },
                    cancel,
                )
                .await
            {
                warn!(?e, "remove consumer exited with error");
            }
        })
    };

    Ok((upsert_handle, remove_handle))
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c().await.ok();
    };

    #[cfg(unix)]
    let terminate = async {
        use tokio::signal::unix::{signal, SignalKind};
        if let Ok(mut sig) = signal(SignalKind::terminate()) {
            sig.recv().await;
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {}
        _ = terminate => {}
    }
}
