//! Driver configuration with environment variable parsing.
//!
//! `DriverConfig` holds all runtime parameters for the multi-market orderbook
//! driver, including NATS connection, database path, resource limits, and
//! operational thresholds.

use std::path::PathBuf;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("Invalid config value: {0}")]
    InvalidValue(String),
}

/// Driver configuration loaded from environment variables.
///
/// # Examples
///
/// Default configuration:
///
/// ```
/// use sniper_orderbook::driver::DriverConfig;
///
/// let config = DriverConfig::default();
/// assert_eq!(config.nats_url, "nats://127.0.0.1:4222");
/// assert_eq!(config.db_path.to_str().unwrap(), "/app/data/pythia.db");
/// assert_eq!(config.max_trackers, 100);
/// assert_eq!(config.memory_threshold_mb, 2048);
/// assert_eq!(config.cpu_threshold_pct, 80.0);
/// assert_eq!(config.graceful_shutdown_timeout_secs, 30);
/// assert_eq!(config.tracker_shutdown_timeout_secs, 10);
/// assert_eq!(config.backoff_max_duration_secs, 32);
/// assert_eq!(config.circuit_breaker_threshold, 5);
/// assert_eq!(config.resource_check_interval_secs, 5);
/// ```
pub struct DriverConfig {
    pub nats_url: String,
    pub db_path: PathBuf,
    pub max_trackers: usize,
    pub memory_threshold_mb: u64,
    pub cpu_threshold_pct: f64,
    pub graceful_shutdown_timeout_secs: u64,
    pub tracker_shutdown_timeout_secs: u64,
    pub backoff_max_duration_secs: u64,
    pub circuit_breaker_threshold: u32,
    pub resource_check_interval_secs: u64,
}
