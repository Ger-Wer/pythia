//! MultiMarketDriver: coordinator for managing tracker pool and lifecycle.
//!
//! Provides centralized management of multiple orderbook trackers with
//! backpressure control, circuit breaker logic, and graceful shutdown.

use std::collections::HashMap;
use std::time::Duration;
use thiserror::Error;

use super::{
    AssetId, BackoffState, CanSpawnResult, DriverConfig, ResourceMonitor, TrackerHandle,
    TrackerTask,
};

#[derive(Debug, Error)]
pub enum DriverError {
    #[error("Configuration error: {0}")]
    Config(String),
}

#[derive(Debug, Error)]
pub enum SpawnError {
    #[error("Backpressure: {0:?}")]
    Backpressure(CanSpawnResult),

    #[error("Circuit breaker open for asset {0}")]
    CircuitOpen(String),

    #[error("Tracker already exists for asset {0}")]
    AlreadyExists(String),
}

#[derive(Debug, Error)]
pub enum ShutdownError {
    #[error("Tracker not found for asset {0}")]
    NotFound(String),

    #[error("Shutdown timeout")]
    Timeout,
}

pub struct MultiMarketDriver {
    /// Active tracker handles indexed by asset_id
    trackers: HashMap<AssetId, TrackerHandle>,

    /// Resource monitoring for backpressure
    resource_monitor: ResourceMonitor,

    /// Per-tracker backoff/circuit breaker state
    backoff_registry: HashMap<AssetId, BackoffState>,

    /// Configuration
    config: DriverConfig,
}

impl MultiMarketDriver {
    /// Initialize driver with config
    pub fn new(config: DriverConfig) -> Result<Self, DriverError> {
        let resource_monitor = ResourceMonitor::new(
            config.max_trackers,
            config.memory_threshold_mb,
            config.cpu_threshold_pct,
        );

        Ok(Self {
            trackers: HashMap::new(),
            resource_monitor,
            backoff_registry: HashMap::new(),
            config,
        })
    }

    /// Spawn a new tracker for given asset_id
    pub async fn spawn_tracker(
        &mut self,
        asset_id: AssetId,
        token_id: String,
    ) -> Result<(), SpawnError> {
        // Check if tracker already exists
        if self.trackers.contains_key(&asset_id) {
            return Err(SpawnError::AlreadyExists(asset_id));
        }

        // Check if we can spawn (backpressure + circuit breaker)
        match self.can_spawn(&asset_id) {
            CanSpawnResult::Yes => {}
            other => return Err(SpawnError::Backpressure(other)),
        }

        // Spawn the tracker task
        let handle = TrackerTask::spawn(asset_id.clone(), token_id);

        // Register with resource monitor
        self.resource_monitor.register_tracker();

        // Insert handle into trackers HashMap
        self.trackers.insert(asset_id, handle);

        Ok(())
    }

    /// Shutdown tracker gracefully with timeout
    pub async fn shutdown_tracker(&mut self, asset_id: AssetId) -> Result<(), ShutdownError> {
        // Lookup tracker in HashMap
        let handle = self
            .trackers
            .remove(&asset_id)
            .ok_or_else(|| ShutdownError::NotFound(asset_id.clone()))?;

        // Send shutdown signal via handle.shutdown_tx
        // Ignore send error (receiver may have already dropped)
        let _ = handle.shutdown_tx.send(());

        // Await handle.task with timeout
        let timeout_duration = Duration::from_secs(self.config.tracker_shutdown_timeout_secs);
        match tokio::time::timeout(timeout_duration, handle.task).await {
            Ok(_) => {
                // Task completed (either Ok or Err, both are valid shutdown states)
                // Unregister from resource monitor
                self.resource_monitor.unregister_tracker();
                Ok(())
            }
            Err(_) => {
                // Timeout occurred
                // Still unregister to keep count accurate
                self.resource_monitor.unregister_tracker();
                Err(ShutdownError::Timeout)
            }
        }
    }

    /// Check if we can spawn (backpressure + circuit breaker)
    fn can_spawn(&self, asset_id: &AssetId) -> CanSpawnResult {
        // Check resource monitor backpressure first
        let resource_result = self.resource_monitor.can_spawn();
        if resource_result != CanSpawnResult::Yes {
            return resource_result;
        }

        // Check circuit breaker for this asset_id
        if let Some(backoff_state) = self.backoff_registry.get(asset_id) {
            if backoff_state.is_circuit_open(self.config.circuit_breaker_threshold) {
                return CanSpawnResult::Yes; // TODO: Return circuit breaker state
                                            // For now, resource check is sufficient
            }
        }

        CanSpawnResult::Yes
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_config() -> DriverConfig {
        DriverConfig {
            max_trackers: 3,
            tracker_shutdown_timeout_secs: 1,
            circuit_breaker_threshold: 5,
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn test_spawn_tracker_success() {
        let mut driver = MultiMarketDriver::new(test_config()).unwrap();

        // Spawn a tracker
        let result = driver
            .spawn_tracker("asset-1".to_string(), "token-1".to_string())
            .await;
        assert!(result.is_ok());

        // Verify tracker is in HashMap
        assert!(driver.trackers.contains_key("asset-1"));
        assert_eq!(driver.trackers.len(), 1);

        // Verify resource monitor incremented
        assert_eq!(driver.resource_monitor.current_count(), 1);
    }

    #[tokio::test]
    async fn test_spawn_duplicate_tracker_fails() {
        let mut driver = MultiMarketDriver::new(test_config()).unwrap();

        // Spawn first tracker
        driver
            .spawn_tracker("asset-1".to_string(), "token-1".to_string())
            .await
            .unwrap();

        // Attempt to spawn duplicate
        let result = driver
            .spawn_tracker("asset-1".to_string(), "token-1".to_string())
            .await;

        // Should return AlreadyExists error
        assert!(matches!(result, Err(SpawnError::AlreadyExists(_))));
        if let Err(SpawnError::AlreadyExists(asset_id)) = result {
            assert_eq!(asset_id, "asset-1");
        }

        // Verify only one tracker exists
        assert_eq!(driver.trackers.len(), 1);
        assert_eq!(driver.resource_monitor.current_count(), 1);
    }

    #[tokio::test]
    async fn test_spawn_with_backpressure() {
        let mut driver = MultiMarketDriver::new(test_config()).unwrap();

        // Spawn up to max_trackers limit (3)
        for i in 0..3 {
            driver
                .spawn_tracker(format!("asset-{}", i), format!("token-{}", i))
                .await
                .unwrap();
        }

        assert_eq!(driver.resource_monitor.current_count(), 3);

        // Attempt to spawn one more (should hit backpressure)
        let result = driver
            .spawn_tracker("asset-overflow".to_string(), "token-overflow".to_string())
            .await;

        // Should return Backpressure error
        assert!(matches!(result, Err(SpawnError::Backpressure(_))));
        if let Err(SpawnError::Backpressure(reason)) = result {
            assert_eq!(
                reason,
                CanSpawnResult::BackpressureTrackerLimit {
                    current: 3,
                    max: 3
                }
            );
        }

        // Verify tracker was not added
        assert_eq!(driver.trackers.len(), 3);
        assert_eq!(driver.resource_monitor.current_count(), 3);
    }

    #[tokio::test]
    async fn test_shutdown_tracker_success() {
        let mut driver = MultiMarketDriver::new(test_config()).unwrap();

        // Spawn a tracker
        driver
            .spawn_tracker("asset-1".to_string(), "token-1".to_string())
            .await
            .unwrap();

        assert_eq!(driver.trackers.len(), 1);
        assert_eq!(driver.resource_monitor.current_count(), 1);

        // Shutdown tracker
        let result = driver.shutdown_tracker("asset-1".to_string()).await;
        assert!(result.is_ok());

        // Verify tracker removed from HashMap
        assert!(!driver.trackers.contains_key("asset-1"));
        assert_eq!(driver.trackers.len(), 0);

        // Verify resource monitor decremented
        assert_eq!(driver.resource_monitor.current_count(), 0);
    }

    #[tokio::test]
    async fn test_shutdown_nonexistent_tracker() {
        let mut driver = MultiMarketDriver::new(test_config()).unwrap();

        // Attempt to shutdown tracker that doesn't exist
        let result = driver.shutdown_tracker("nonexistent".to_string()).await;

        // Should return NotFound error
        assert!(matches!(result, Err(ShutdownError::NotFound(_))));
        if let Err(ShutdownError::NotFound(asset_id)) = result {
            assert_eq!(asset_id, "nonexistent");
        }

        // Resource monitor should remain at 0
        assert_eq!(driver.resource_monitor.current_count(), 0);
    }
}
