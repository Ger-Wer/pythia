//! Thin wrappers around Redis Streams + NATS JetStream connect logic.

use std::time::Duration;

use anyhow::Context;
use dual_loop_common::topics;

const TRIGGERS_STREAM: &str = "DL_TRIGGERS";
/// JetStream stream name for the mock mempool topic. The harness creates
/// it just like sniper-ingest's mock consumer would, so the harness can
/// spin up against a fresh nats with `-js` enabled and not depend on
/// sniper-ingest having been started first.
const MOCK_MEMPOOL_STREAM: &str = "DL_MEMPOOL_MOCK";

/// Open a Redis multiplexed publisher with a generous MAXLEN so a quick
/// burst of synthetic txs doesn't clip itself.
pub async fn connect_redis(url: &str) -> anyhow::Result<dual_loop_common::bus::StreamPublisher> {
    let pub_ = dual_loop_common::bus::StreamPublisher::connect(url)
        .await
        .with_context(|| format!("connect redis at {url}"))?;
    Ok(pub_.with_maxlen_approx(Some(100_000)))
}

/// Connect to NATS, return a JetStream-backed client.
pub async fn connect_nats(url: &str) -> anyhow::Result<dual_loop_common::bus::JetStreamClient> {
    // Tiny retry: the CI runner sometimes reaches the harness before the
    // nats container has finished booting JetStream. Five quick attempts
    // amortize that without hiding real misconfigurations.
    let mut last_err: Option<anyhow::Error> = None;
    for attempt in 1..=5 {
        match dual_loop_common::bus::JetStreamClient::connect(url).await {
            Ok(client) => return Ok(client),
            Err(e) => {
                tracing::warn!(
                    attempt,
                    error = %e,
                    "nats connect failed, retrying"
                );
                last_err = Some(anyhow::anyhow!(e));
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }
    Err(last_err.unwrap_or_else(|| anyhow::anyhow!("nats connect: unknown error")))
        .with_context(|| format!("connect nats at {url}"))
}

/// Idempotent JetStream stream provisioning for `dl.v1.triggers.fired`.
/// Sniper-ingest does the same on startup; if both run, JetStream returns
/// the existing stream config to whoever calls second.
pub async fn ensure_triggers_stream(
    client: &dual_loop_common::bus::JetStreamClient,
) -> anyhow::Result<()> {
    client
        .ensure_stream(TRIGGERS_STREAM, vec![topics::TRIGGERS_FIRED.into()])
        .await
        .context("ensure DL_TRIGGERS stream")?;
    Ok(())
}

/// Stream name used by the trigger consumer.
pub const fn triggers_stream_name() -> &'static str {
    TRIGGERS_STREAM
}

/// Stream name used by the mock mempool topic. Provisioned only if the
/// harness happens to be the first thing to publish on it.
#[allow(dead_code)]
pub const fn mock_mempool_stream_name() -> &'static str {
    MOCK_MEMPOOL_STREAM
}
