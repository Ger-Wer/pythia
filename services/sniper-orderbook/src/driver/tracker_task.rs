//! TrackerTask: async wrapper for running one sniper_orderbook::Tracker per market.
//!
//! Provides lifecycle management, health monitoring, and graceful shutdown
//! for individual orderbook trackers.

use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;

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
    #[allow(dead_code)] // Used in next task (Task 7: Polymarket integration)
    asset_id: AssetId,
    #[allow(dead_code)] // Used in next task (Task 7: Polymarket integration)
    token_id: String,
    health_tx: watch::Sender<TrackerHealth>,
    shutdown_rx: oneshot::Receiver<()>,
}

impl TrackerTask {
    /// Spawns a new tracker task for the given asset.
    pub fn spawn(asset_id: AssetId, token_id: String) -> TrackerHandle {
        let (shutdown_tx, shutdown_rx) = oneshot::channel();
        let (health_tx, health_rx) = watch::channel(TrackerHealth::Syncing);

        let task_instance = TrackerTask {
            asset_id: asset_id.clone(),
            token_id,
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

    #[allow(unused_mut)] // mut needed for shutdown_rx.await
    async fn run(mut self) -> Result<(), TrackerError> {
        // MVP: just wait for shutdown signal
        // Real implementation will drive sniper_orderbook::Tracker in next task
        self.health_tx.send(TrackerHealth::Syncing).ok();
        
        match self.shutdown_rx.await {
            Ok(()) => {
                self.health_tx.send(TrackerHealth::Stopping).ok();
                Err(TrackerError::ShutdownSignal)
            }
            Err(_) => {
                // Sender dropped, treat as shutdown
                self.health_tx.send(TrackerHealth::Stopping).ok();
                Err(TrackerError::ShutdownSignal)
            }
        }
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
}
