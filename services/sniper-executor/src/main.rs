//! sniper-executor: Fast Loop executor service.
//!
//! Phase 0: скелет с инициализацией observability, заглушкой KillSwitch'а и
//! heartbeat. Реальная подпись/broadcast транзакций появятся в Phase 2
//! (paper mode) и Phase 3 (live mode с HSM).

use anyhow::Result;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::time::{interval, Duration};

#[tokio::main]
async fn main() -> Result<()> {
    let service = "sniper_executor";
    let metrics_addr: SocketAddr = std::env::var("METRICS_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:9102".to_string())
        .parse()?;

    dual_loop_common::observability::init(service, metrics_addr)?;

    let kill = Arc::new(AtomicBool::new(false));
    tracing::info!(
        wire_version = dual_loop_common::WIRE_VERSION,
        kill_engaged = kill.load(Ordering::Relaxed),
        "sniper-executor starting (Phase 0 skeleton, paper-only)"
    );

    let counter = metrics::counter!("sniper_executor_heartbeat_total");
    let mut tick = interval(Duration::from_secs(5));
    let shutdown = shutdown_signal();
    tokio::pin!(shutdown);

    loop {
        tokio::select! {
            _ = tick.tick() => {
                if kill.load(Ordering::Relaxed) {
                    tracing::warn!("kill switch engaged; skipping cycle");
                    continue;
                }
                counter.increment(1);
                tracing::debug!("heartbeat");
            }
            _ = &mut shutdown => {
                tracing::info!("shutdown signal received; draining");
                kill.store(true, Ordering::SeqCst);
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
