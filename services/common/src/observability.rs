//! Инициализация tracing + Prometheus metrics для Fast Loop сервисов.

use anyhow::Result;
use metrics_exporter_prometheus::PrometheusBuilder;
use std::net::SocketAddr;
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

/// Настроить structured logging (JSON) и Prometheus scrape endpoint.
///
/// `service` используется как label в метриках.
/// `metrics_addr` — адрес для Prometheus scrape (обычно `0.0.0.0:9100`).
pub fn init(service: &str, metrics_addr: SocketAddr) -> Result<()> {
    // Logging.
    let env_filter = EnvFilter::try_from_default_env()
        .or_else(|_| EnvFilter::try_new("info,tokio=warn,h2=warn"))
        .expect("valid env filter");

    tracing_subscriber::registry()
        .with(env_filter)
        .with(fmt::layer().with_target(true).json())
        .try_init()
        .ok();

    // Metrics.
    PrometheusBuilder::new()
        .with_http_listener(metrics_addr)
        .add_global_label("service", service)
        .install()?;

    tracing::info!(service, %metrics_addr, "observability initialized");
    Ok(())
}
