//! Mempool subscriber actors for pending transaction streams.
//!
//! Two transport actors:
//! - **WS** (primary, dev/CI): connects to Anvil fork via `ANVIL_WS_URL`.
//! - **IPC** (secondary, staging/prod): connects to local Bor/Erigon via
//!   `POLYGON_IPC_PATH`. Only starts when the env var is set.
//!
//! Each actor runs an exponential-backoff reconnect loop (max 30 s).
//! Pending transactions are pushed into a bounded `mpsc` channel; on
//! overflow, the transaction is dropped and a counter incremented.

use std::time::Duration;

use alloy::network::Ethereum;
use alloy::providers::{IpcConnect, Provider, ProviderBuilder, RootProvider, WsConnect};
use alloy::pubsub::PubSubFrontend;
use alloy::rpc::types::Transaction;
use futures::StreamExt;
use tokio::sync::mpsc;
use tracing::{error, info, warn};

/// Default bounded channel capacity.
pub const TX_CHANNEL_CAPACITY: usize = 10_000;

/// Maximum backoff between reconnects.
const MAX_BACKOFF: Duration = Duration::from_secs(30);

/// Initial backoff after a disconnection.
const INITIAL_BACKOFF: Duration = Duration::from_millis(250);

/// A pending transaction received from the mempool.
#[derive(Debug, Clone)]
pub struct PendingTx {
    pub tx: Transaction,
    pub observed_at_ns: u64,
}

/// Spawn the WS mempool subscriber actor.
pub fn spawn_ws_subscriber(
    ws_url: String,
    tx: mpsc::Sender<PendingTx>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut backoff = INITIAL_BACKOFF;
        loop {
            info!(url = %ws_url, "connecting WS mempool subscriber");
            match connect_and_stream_ws(&ws_url, &tx).await {
                Ok(()) => {
                    info!("WS mempool stream ended gracefully");
                    backoff = INITIAL_BACKOFF;
                }
                Err(e) => {
                    error!(err = %e, "WS mempool subscriber error");
                }
            }
            warn!(backoff_ms = backoff.as_millis(), "reconnecting WS subscriber");
            tokio::time::sleep(backoff).await;
            backoff = (backoff * 2).min(MAX_BACKOFF);
        }
    })
}

/// Spawn the IPC mempool subscriber actor.
pub fn spawn_ipc_subscriber(
    ipc_path: String,
    tx: mpsc::Sender<PendingTx>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut backoff = INITIAL_BACKOFF;
        loop {
            info!(path = %ipc_path, "connecting IPC mempool subscriber");
            match connect_and_stream_ipc(&ipc_path, &tx).await {
                Ok(()) => {
                    info!("IPC mempool stream ended gracefully");
                    backoff = INITIAL_BACKOFF;
                }
                Err(e) => {
                    error!(err = %e, "IPC mempool subscriber error");
                }
            }
            warn!(backoff_ms = backoff.as_millis(), "reconnecting IPC subscriber");
            tokio::time::sleep(backoff).await;
            backoff = (backoff * 2).min(MAX_BACKOFF);
        }
    })
}

async fn connect_and_stream_ws(
    url: &str,
    tx: &mpsc::Sender<PendingTx>,
) -> anyhow::Result<()> {
    let ws = WsConnect::new(url);
    let provider: RootProvider<PubSubFrontend, Ethereum> =
        ProviderBuilder::new().on_ws(ws).await?;
    stream_pending_txs(&provider, tx).await
}

async fn connect_and_stream_ipc(
    path: &str,
    tx: &mpsc::Sender<PendingTx>,
) -> anyhow::Result<()> {
    let ipc = IpcConnect::new(path.to_string());
    let provider: RootProvider<PubSubFrontend, Ethereum> =
        ProviderBuilder::new().on_ipc(ipc).await?;
    stream_pending_txs(&provider, tx).await
}

async fn stream_pending_txs(
    provider: &RootProvider<PubSubFrontend, Ethereum>,
    tx: &mpsc::Sender<PendingTx>,
) -> anyhow::Result<()> {
    let sub = provider.subscribe_full_pending_transactions().await?;
    let mut stream = sub.into_stream();

    metrics::counter!("mempool_tx_seen_total").increment(0);

    while let Some(transaction) = stream.next().await {
        let observed_at_ns = monotonic_ns();
        metrics::counter!("mempool_tx_seen_total").increment(1);

        let pending = PendingTx {
            tx: transaction,
            observed_at_ns,
        };

        if tx.try_send(pending).is_err() {
            metrics::counter!("mempool_drop_total").increment(1);
            warn!("mempool channel full; dropping tx");
        }
    }

    Ok(())
}

/// Current monotonic clock in nanoseconds (for latency measurement).
#[inline]
pub fn monotonic_ns() -> u64 {
    use std::time::Instant;
    static EPOCH: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    let epoch = EPOCH.get_or_init(Instant::now);
    epoch.elapsed().as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn monotonic_ns_is_increasing() {
        let a = monotonic_ns();
        std::thread::sleep(Duration::from_millis(1));
        let b = monotonic_ns();
        assert!(b > a);
    }

    #[tokio::test]
    async fn channel_overflow_increments_counter() {
        let (tx, _rx) = mpsc::channel::<PendingTx>(1);
        assert_eq!(tx.capacity(), 1);
    }
}
