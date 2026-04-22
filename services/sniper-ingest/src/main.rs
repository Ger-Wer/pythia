//! sniper-ingest: Fast Loop ingest service.
//!
//! Phase 0: скелет сервиса, который инициализирует observability и
//! печатает heartbeat. Реальная mempool-подписка, OracleDecoder и
//! TriggerMatcher появятся в Phase 2.

use anyhow::Result;
use std::net::SocketAddr;
use tokio::time::{interval, Duration};

#[tokio::main]
async fn main() -> Result<()> {
    let service = "sniper_ingest";
    let metrics_addr: SocketAddr = std::env::var("METRICS_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:9101".to_string())
        .parse()?;

    dual_loop_common::observability::init(service, metrics_addr)?;
    tracing::info!(
        wire_version = dual_loop_common::WIRE_VERSION,
        "sniper-ingest starting (Phase 0 skeleton)"
    );

    let counter = metrics::counter!("sniper_ingest_heartbeat_total");
    let mut tick = interval(Duration::from_secs(5));
    let shutdown = shutdown_signal();
    tokio::pin!(shutdown);

    loop {
        tokio::select! {
            _ = tick.tick() => {
                counter.increment(1);
                tracing::debug!("heartbeat");
            }
            _ = &mut shutdown => {
                tracing::info!("shutdown signal received; exiting");
                break;
            }
        }
    }

    Ok(())
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
