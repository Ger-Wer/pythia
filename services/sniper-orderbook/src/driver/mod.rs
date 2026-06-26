//! Multi-market orderbook driver orchestration.
//!
//! The driver module provides configuration and orchestration capabilities
//! for managing multiple Polymarket orderbook trackers.

mod config;
mod backoff;
mod resource_monitor;
mod tracker_task;

pub use config::{ConfigError, DriverConfig};
pub use backoff::BackoffState;
pub use resource_monitor::{CanSpawnResult, ResourceMonitor};
pub use tracker_task::{AssetId, TrackerError, TrackerHandle, TrackerHealth, TrackerTask};
