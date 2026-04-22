//! sniper-ingest: Fast Loop ingest service.
//!
//! Phase 1: consumes `dl.v1.watchlist.upsert` / `dl.v1.watchlist.remove`
//! from Redis Streams and keeps an in-memory [`WatchlistStore`]. Also
//! runs a TTL reaper. Emits Prometheus metrics. Real mempool-ingestion,
//! oracle decoding, trigger matching land in Phase 2.

use anyhow::Result;
use sniper_ingest::watchlist::WatchlistStore;
use std::net::SocketAddr;
use tokio::time::{interval, Duration};
use tracing::{info, warn};

#[tokio::main]
async fn main() -> Result<()> {
    let service = "sniper_ingest";
    let metrics_addr: SocketAddr = std::env::var("METRICS_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:9101".to_string())
        .parse()?;

    dual_loop_common::observability::init(service, metrics_addr)?;
    tracing::info!(
        wire_version = dual_loop_common::WIRE_VERSION,
        "sniper-ingest starting (Phase 1)"
    );

    let store = WatchlistStore::new();

    // TTL reaper.
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    let reaper = store.spawn_ttl_reaper(shutdown_rx.clone());

    // Optional bus consumer. If REDIS_URL is set, run the consumer group;
    // otherwise just idle with the TTL reaper so the binary remains usable
    // in minimal dev setups (matches Phase 0 skeleton behaviour).
    let bus_tasks = match std::env::var("REDIS_URL") {
        Ok(url) => Some(spawn_bus_consumers(url, store.clone(), shutdown_rx.clone()).await?),
        Err(_) => {
            warn!("REDIS_URL not set; running without bus consumers");
            None
        }
    };

    let heartbeat = metrics::counter!("sniper_ingest_heartbeat_total");
    let mut tick = interval(Duration::from_secs(5));
    let shutdown = shutdown_signal();
    tokio::pin!(shutdown);

    loop {
        tokio::select! {
            _ = tick.tick() => {
                heartbeat.increment(1);
                metrics::gauge!("watchlist_entries_total").set(store.len() as f64);
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
    reaper.abort();

    Ok(())
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
