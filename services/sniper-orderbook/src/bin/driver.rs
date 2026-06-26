//! Main binary entry point for the multi-market orderbook driver.
//!
//! Initializes tracing, loads configuration from environment variables,
//! connects to NATS and SQLite, spawns the driver event loop, and handles
//! graceful shutdown on Ctrl+C.

use sniper_orderbook::driver::{DriverConfig, MultiMarketDriver};
use tokio::signal;
use tracing::{error, info};
use tracing_subscriber;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // 1. Initialize tracing
    tracing_subscriber::fmt()
        .with_target(false)
        .with_thread_ids(true)
        .with_level(true)
        .init();

    info!("Starting multi-market orderbook driver");

    // 2. Load config from environment
    let config = DriverConfig::from_env()?;
    info!(
        "Config loaded: max_trackers={}, nats_url={}",
        config.max_trackers, config.nats_url
    );

    // 3. Initialize driver
    let mut driver = MultiMarketDriver::new(config).await?;
    info!("Driver initialized, connected to NATS and database");

    // 4. Spawn event loop in background task
    let driver_task = tokio::spawn(async move {
        match driver.run().await {
            Ok(()) => {
                info!("Driver event loop exited normally");
                Ok(())
            }
            Err(e) => {
                error!("Driver event loop error: {}", e);
                Err(e)
            }
        }
    });

    info!("Driver running, press Ctrl+C to shutdown");

    // 5. Race driver task against Ctrl+C
    tokio::select! {
        result = driver_task => {
            match result {
                Ok(Ok(())) => {
                    info!("Driver task completed successfully");
                }
                Ok(Err(e)) => {
                    error!("Driver task failed: {}", e);
                    std::process::exit(1);
                }
                Err(e) => {
                    error!("Driver task panicked: {}", e);
                    std::process::exit(1);
                }
            }
        }
        result = signal::ctrl_c() => {
            match result {
                Ok(()) => {
                    info!("Received Ctrl+C, initiating graceful shutdown");
                }
                Err(err) => {
                    error!("Unable to listen for shutdown signal: {}", err);
                    std::process::exit(1);
                }
            }
        }
    }

    // 6. MVP shutdown approach (Option A)
    // Since driver.run() is an infinite loop and driver is moved into task,
    // proper shutdown coordination requires adding shutdown signal to run()
    // (see coordinator.rs:260 TODO). For MVP, we exit and let OS clean up connections.
    //
    // TODO Post-MVP: Implement Option B with watch channel:
    // - Create (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false)
    // - Pass shutdown_rx to driver.run()
    // - Add tokio::select! arm in run() to monitor shutdown signal
    // - After ctrl_c: shutdown_tx.send(true)?; driver_task.await??;
    info!("Shutdown complete");
    std::process::exit(0);
}
