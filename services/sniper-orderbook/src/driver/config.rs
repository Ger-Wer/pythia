//! Driver configuration with environment variable parsing.
//!
//! `DriverConfig` holds all runtime parameters for the multi-market orderbook
//! driver, including NATS connection, database path, resource limits, and
//! operational thresholds.

use std::env;
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
///
/// Loading from environment variables:
///
/// ```
/// use sniper_orderbook::driver::DriverConfig;
/// use std::env;
///
/// // Set custom values
/// env::set_var("NATS_URL", "nats://custom:4222");
/// env::set_var("DRIVER_DB_PATH", "/custom/path/db.db");
/// env::set_var("DRIVER_MAX_TRACKERS", "50");
/// env::set_var("DRIVER_MEMORY_THRESHOLD_MB", "4096");
/// env::set_var("DRIVER_CPU_THRESHOLD_PCT", "90.5");
/// env::set_var("DRIVER_GRACEFUL_SHUTDOWN_TIMEOUT_SECS", "60");
/// env::set_var("DRIVER_TRACKER_SHUTDOWN_TIMEOUT_SECS", "20");
/// env::set_var("DRIVER_BACKOFF_MAX_DURATION_SECS", "64");
/// env::set_var("DRIVER_CIRCUIT_BREAKER_THRESHOLD", "10");
/// env::set_var("DRIVER_RESOURCE_CHECK_INTERVAL_SECS", "15");
///
/// let config = DriverConfig::from_env().unwrap();
/// assert_eq!(config.nats_url, "nats://custom:4222");
/// assert_eq!(config.db_path.to_str().unwrap(), "/custom/path/db.db");
/// assert_eq!(config.max_trackers, 50);
/// assert_eq!(config.memory_threshold_mb, 4096);
/// assert_eq!(config.cpu_threshold_pct, 90.5);
/// assert_eq!(config.graceful_shutdown_timeout_secs, 60);
/// assert_eq!(config.tracker_shutdown_timeout_secs, 20);
/// assert_eq!(config.backoff_max_duration_secs, 64);
/// assert_eq!(config.circuit_breaker_threshold, 10);
/// assert_eq!(config.resource_check_interval_secs, 15);
///
/// // Clean up
/// env::remove_var("NATS_URL");
/// env::remove_var("DRIVER_DB_PATH");
/// env::remove_var("DRIVER_MAX_TRACKERS");
/// env::remove_var("DRIVER_MEMORY_THRESHOLD_MB");
/// env::remove_var("DRIVER_CPU_THRESHOLD_PCT");
/// env::remove_var("DRIVER_GRACEFUL_SHUTDOWN_TIMEOUT_SECS");
/// env::remove_var("DRIVER_TRACKER_SHUTDOWN_TIMEOUT_SECS");
/// env::remove_var("DRIVER_BACKOFF_MAX_DURATION_SECS");
/// env::remove_var("DRIVER_CIRCUIT_BREAKER_THRESHOLD");
/// env::remove_var("DRIVER_RESOURCE_CHECK_INTERVAL_SECS");
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

impl Default for DriverConfig {
    fn default() -> Self {
        Self {
            nats_url: "nats://127.0.0.1:4222".to_string(),
            db_path: PathBuf::from("/app/data/pythia.db"),
            max_trackers: 100,
            memory_threshold_mb: 2048,
            cpu_threshold_pct: 80.0,
            graceful_shutdown_timeout_secs: 30,
            tracker_shutdown_timeout_secs: 10,
            backoff_max_duration_secs: 32,
            circuit_breaker_threshold: 5,
            resource_check_interval_secs: 5,
        }
    }
}

impl DriverConfig {
    /// Load configuration from environment variables with fallback to defaults.
    pub fn from_env() -> Result<Self, ConfigError> {
        let defaults = Self::default();

        Ok(Self {
            nats_url: env::var("NATS_URL").unwrap_or(defaults.nats_url),
            db_path: env::var("DRIVER_DB_PATH")
                .map(PathBuf::from)
                .unwrap_or(defaults.db_path),
            max_trackers: parse_env("DRIVER_MAX_TRACKERS")?
                .unwrap_or(defaults.max_trackers),
            memory_threshold_mb: parse_env("DRIVER_MEMORY_THRESHOLD_MB")?
                .unwrap_or(defaults.memory_threshold_mb),
            cpu_threshold_pct: parse_env("DRIVER_CPU_THRESHOLD_PCT")?
                .unwrap_or(defaults.cpu_threshold_pct),
            graceful_shutdown_timeout_secs: parse_env("DRIVER_GRACEFUL_SHUTDOWN_TIMEOUT_SECS")?
                .unwrap_or(defaults.graceful_shutdown_timeout_secs),
            tracker_shutdown_timeout_secs: parse_env("DRIVER_TRACKER_SHUTDOWN_TIMEOUT_SECS")?
                .unwrap_or(defaults.tracker_shutdown_timeout_secs),
            backoff_max_duration_secs: parse_env("DRIVER_BACKOFF_MAX_DURATION_SECS")?
                .unwrap_or(defaults.backoff_max_duration_secs),
            circuit_breaker_threshold: parse_env("DRIVER_CIRCUIT_BREAKER_THRESHOLD")?
                .unwrap_or(defaults.circuit_breaker_threshold),
            resource_check_interval_secs: parse_env("DRIVER_RESOURCE_CHECK_INTERVAL_SECS")?
                .unwrap_or(defaults.resource_check_interval_secs),
        })
    }
}

/// Parse environment variable with proper error handling.
fn parse_env<T: std::str::FromStr>(key: &str) -> Result<Option<T>, ConfigError> {
    match env::var(key) {
        Ok(val) => val
            .parse::<T>()
            .map(Some)
            .map_err(|_| ConfigError::InvalidValue(format!("{} = {}", key, val))),
        Err(env::VarError::NotPresent) => Ok(None),
        Err(env::VarError::NotUnicode(_)) => Err(ConfigError::InvalidValue(format!(
            "{} contains invalid UTF-8",
            key
        ))),
    }
}
