//! TrackerTask: async wrapper for running one sniper_orderbook::Tracker per market.
//!
//! Provides lifecycle management, health monitoring, and graceful shutdown
//! for individual orderbook trackers.

use crate::state::TrackerState;
use crate::tracker::{Tracker, TrackerConfig};
use std::time::Duration;
use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;

#[cfg(feature = "polymarket-live")]
use super::polymarket_client::{PolymarketRestClient, PolymarketWsSubscriber};

pub type AssetId = String;

/// Health state of a tracker task.
#[derive(Debug, Clone, PartialEq)]
pub enum TrackerHealth {
    /// Actively tracking orderbook updates
    Tracking,
    /// Initial synchronization in progress
    Syncing,
    /// Degraded state with reason
    Degraded(String),
    /// Shutdown in progress
    Stopping,
}

/// Handle for managing a spawned tracker task.
pub struct TrackerHandle {
    pub asset_id: AssetId,
    pub task: JoinHandle<Result<(), TrackerError>>,
    pub shutdown_tx: oneshot::Sender<()>,
    pub health_rx: watch::Receiver<TrackerHealth>,
}

/// Errors that can occur during tracker operation.
#[derive(Debug, thiserror::Error)]
pub enum TrackerError {
    #[error("WebSocket connection failed: {0}")]
    WebSocketConnection(String),

    #[error("Snapshot request failed: {0}")]
    SnapshotRequest(String),

    #[error("NATS publish failed: {0}")]
    NatsPublish(String),

    #[error("Degraded: {0}")]
    Degraded(String),

    #[error("Shutdown signal received")]
    ShutdownSignal,
}

/// TrackerTask manages the lifecycle of a single orderbook tracker.
pub struct TrackerTask {
    asset_id: AssetId,
    token_id: String,
    tracker: Tracker,
    #[cfg(feature = "polymarket-live")]
    rest_client: PolymarketRestClient,
    #[cfg(feature = "polymarket-live")]
    ws_subscriber: Option<PolymarketWsSubscriber>,
    health_tx: watch::Sender<TrackerHealth>,
    shutdown_rx: oneshot::Receiver<()>,
}

impl TrackerTask {
    /// Spawns a new tracker task for the given asset.
    pub fn spawn(asset_id: AssetId, token_id: String) -> TrackerHandle {
        let (shutdown_tx, shutdown_rx) = oneshot::channel();
        let (health_tx, health_rx) = watch::channel(TrackerHealth::Syncing);

        #[cfg(feature = "polymarket-live")]
        {
            let rest_client = match PolymarketRestClient::new(token_id.clone()) {
                Ok(client) => client,
                Err(e) => {
                    // If client init fails, spawn a task that immediately errors
                    let task = tokio::spawn(async move {
                        Err(TrackerError::WebSocketConnection(format!(
                            "RestClient init failed: {}",
                            e
                        )))
                    });
                    return TrackerHandle {
                        asset_id,
                        task,
                        shutdown_tx,
                        health_rx,
                    };
                }
            };

            let task_instance = TrackerTask {
                asset_id: asset_id.clone(),
                token_id,
                tracker: Tracker::new(TrackerConfig::default()),
                rest_client,
                ws_subscriber: None,
                health_tx,
                shutdown_rx,
            };

            let task = tokio::spawn(task_instance.run());

            TrackerHandle {
                asset_id,
                task,
                shutdown_tx,
                health_rx,
            }
        }

        #[cfg(not(feature = "polymarket-live"))]
        {
            let task_instance = TrackerTask {
                asset_id: asset_id.clone(),
                token_id,
                tracker: Tracker::new(TrackerConfig::default()),
                health_tx,
                shutdown_rx,
            };

            let task = tokio::spawn(task_instance.run());

            TrackerHandle {
                asset_id,
                task,
                shutdown_tx,
                health_rx,
            }
        }
    }

    async fn run(self) -> Result<(), TrackerError> {
        #[cfg(feature = "polymarket-live")]
        {
            self.run_with_polymarket().await
        }
        #[cfg(not(feature = "polymarket-live"))]
        {
            self.run_stub().await
        }
    }

    #[cfg(feature = "polymarket-live")]
    async fn run_with_polymarket(mut self) -> Result<(), TrackerError> {
        // 1. Connect WebSocket
        let ws = PolymarketWsSubscriber::connect(self.token_id.clone())
            .await
            .map_err(TrackerError::WebSocketConnection)?;
        self.ws_subscriber = Some(ws);

        // 2. Notify tracker of WS connection
        let _ = self.tracker.on_ws_connected();
        self.update_health_from_tracker();

        // 3. Request snapshot
        let snapshot = self
            .rest_client
            .fetch_snapshot(0) // sequence=0 for initial snapshot
            .await
            .map_err(TrackerError::SnapshotRequest)?;

        // 4. Feed snapshot to tracker
        let _ = self.tracker.on_snapshot_received(snapshot);
        self.update_health_from_tracker();

        // 5. Main loop: process WS messages until shutdown
        loop {
            tokio::select! {
                biased; // prioritize shutdown over WS messages
                _ = &mut self.shutdown_rx => {
                    // Graceful shutdown
                    if let Some(ws) = self.ws_subscriber.take() {
                        ws.close().await;
                    }
                    self.health_tx.send(TrackerHealth::Stopping).ok();
                    tokio::time::sleep(Duration::from_millis(10)).await;
                    return Err(TrackerError::ShutdownSignal);
                }
                msg = async {
                    match self.ws_subscriber.as_mut() {
                        Some(ws) => ws.next_update().await,
                        None => Err("WS subscriber not initialized".to_string()),
                    }
                } => {
                    match msg {
                        Ok(Some(update)) => {
                            let _ = self.tracker.on_ws_message(update);
                            self.update_health_from_tracker();
                        }
                        Ok(None) => {
                            // Graceful WS close from server
                            let _ = self.tracker.on_ws_disconnected();
                            self.update_health_from_tracker();
                            return Err(TrackerError::WebSocketConnection("WS closed by server".to_string()));
                        }
                        Err(e) => {
                            // WS error
                            let _ = self.tracker.on_ws_disconnected();
                            self.update_health_from_tracker();
                            return Err(TrackerError::WebSocketConnection(e));
                        }
                    }
                }
            }
        }
    }

    #[cfg(not(feature = "polymarket-live"))]
    async fn run_stub(mut self) -> Result<(), TrackerError> {
        // Stub implementation for tests without polymarket-live feature
        self.health_tx.send(TrackerHealth::Syncing).ok();

        match self.shutdown_rx.await {
            Ok(()) => {
                self.health_tx.send(TrackerHealth::Stopping).ok();
                tokio::time::sleep(Duration::from_millis(10)).await;
                Err(TrackerError::ShutdownSignal)
            }
            Err(_) => {
                self.health_tx.send(TrackerHealth::Stopping).ok();
                tokio::time::sleep(Duration::from_millis(10)).await;
                Err(TrackerError::ShutdownSignal)
            }
        }
    }

    fn update_health_from_tracker(&mut self) {
        let health = match self.tracker.state() {
            TrackerState::Tracking => TrackerHealth::Tracking,
            TrackerState::Syncing | TrackerState::BufferingWs | TrackerState::FetchingSnapshot => {
                TrackerHealth::Syncing
            }
            TrackerState::Degraded { reason } => {
                TrackerHealth::Degraded(format!("{:?}", reason))
            }
            TrackerState::Connecting => TrackerHealth::Syncing,
            TrackerState::Stopped => TrackerHealth::Stopping,
        };
        self.health_tx.send(health).ok();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tracker_health_equality() {
        assert_eq!(TrackerHealth::Tracking, TrackerHealth::Tracking);
        assert_eq!(TrackerHealth::Syncing, TrackerHealth::Syncing);
        assert_eq!(TrackerHealth::Stopping, TrackerHealth::Stopping);
        assert_eq!(
            TrackerHealth::Degraded("test".to_string()),
            TrackerHealth::Degraded("test".to_string())
        );
        assert_ne!(
            TrackerHealth::Degraded("a".to_string()),
            TrackerHealth::Degraded("b".to_string())
        );
        assert_ne!(TrackerHealth::Tracking, TrackerHealth::Syncing);
    }

    #[tokio::test]
    async fn test_tracker_spawn_and_shutdown() {
        // Spawn a tracker task
        let handle = TrackerTask::spawn("test-asset".to_string(), "token-123".to_string());

        // Verify initial health is Syncing
        let initial_health = handle.health_rx.borrow().clone();
        assert_eq!(initial_health, TrackerHealth::Syncing);

        // Send shutdown signal
        handle.shutdown_tx.send(()).expect("shutdown send failed");

        // Await task completion
        let result = handle.task.await.expect("task panicked");

        // Verify task returns ShutdownSignal error
        assert!(matches!(result, Err(TrackerError::ShutdownSignal)));
    }

    #[tokio::test]
    async fn test_tracker_handle_drop_triggers_shutdown() {
        let handle = TrackerTask::spawn("test".to_string(), "token".to_string());

        // Drop shutdown sender without sending signal
        drop(handle.shutdown_tx);

        // Task should exit with ShutdownSignal error
        let result = handle.task.await.expect("task panicked");
        assert!(matches!(result, Err(TrackerError::ShutdownSignal)));
    }

    // Integration test with real Polymarket clients (marked #[ignore])
    #[tokio::test]
    #[ignore = "requires live Polymarket API and valid token_id"]
    #[cfg(feature = "polymarket-live")]
    async fn test_tracker_with_polymarket_integration() {
        // This test requires a valid Polymarket token_id
        let handle = TrackerTask::spawn(
            "test-asset".to_string(),
            "71321045679252212594626385532706912750332728571942532289631379312455583992563".to_string(),
        );

        // Wait briefly to see if connection succeeds
        tokio::time::sleep(Duration::from_secs(2)).await;

        // Check health status
        let health = handle.health_rx.borrow().clone();
        println!("Health after 2s: {:?}", health);

        // Shutdown
        handle.shutdown_tx.send(()).ok();
        let _ = handle.task.await;
    }
}
