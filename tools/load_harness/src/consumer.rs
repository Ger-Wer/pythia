//! NATS JetStream consumer for `dl.v1.triggers.fired`.

use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Context;
use dashmap::DashMap;
use dual_loop_common::bus::JetStreamClient;
use dual_loop_common::proto::trigger::Trigger;
use dual_loop_common::topics;
use tokio::sync::watch;
use tracing::{debug, info, warn};

use crate::bus::triggers_stream_name;
use crate::stats::LatencySample;

/// Stop signal handed to the consumer. `false` while the run is active,
/// flipped to `true` once the publisher finishes; the consumer then drains
/// for `drain_secs` more before returning.
pub type StopRx = watch::Receiver<bool>;
pub type StopTx = watch::Sender<bool>;

/// Spawn the trigger consumer. It returns the full set of latency samples
/// collected during the run (including those that arrived during the
/// drain window after `stop_rx` flips to `true`).
pub async fn spawn(
    nats_client: JetStreamClient,
    publish_times: Arc<DashMap<Vec<u8>, Instant>>,
    mut stop_rx: StopRx,
    drain_secs: u64,
) -> anyhow::Result<tokio::task::JoinHandle<anyhow::Result<Vec<LatencySample>>>> {
    // Unique per run so two harness runs never share consumer state.
    let consumer_name = format!(
        "load-harness-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default(),
    );

    let mut consumer = nats_client
        .pull_subscribe(
            triggers_stream_name(),
            topics::TRIGGERS_FIRED,
            &consumer_name,
        )
        .await
        .context("pull_subscribe DL_TRIGGERS / dl.v1.triggers.fired")?;
    info!(consumer = %consumer_name, "trigger consumer subscribed");

    let handle = tokio::spawn(async move {
        let mut samples: Vec<LatencySample> = Vec::with_capacity(8 * 1024);
        let mut drain_deadline: Option<Instant> = None;

        loop {
            // Detect the stop transition.
            if !*stop_rx.borrow() {
                // Cheap, no-blocking check; nothing to do, fall through
                // to the timed `next()` race.
            } else if drain_deadline.is_none() {
                drain_deadline = Some(Instant::now() + Duration::from_secs(drain_secs));
                info!(drain_secs, "consumer entering drain mode after stop signal");
            }

            if let Some(deadline) = drain_deadline {
                if Instant::now() >= deadline {
                    info!(
                        samples = samples.len(),
                        "consumer drain deadline reached, stopping"
                    );
                    break;
                }
            }

            // Bound `next()` so we re-check the stop signal regularly.
            let timeout_ms: u64 = if let Some(deadline) = drain_deadline {
                deadline
                    .saturating_duration_since(Instant::now())
                    .as_millis()
                    .min(500) as u64
            } else {
                500
            };

            let next_fut = consumer.next();
            tokio::pin!(next_fut);

            let msg = tokio::select! {
                biased;
                changed = stop_rx.changed() => {
                    // `changed()` only resolves on a state transition.
                    // We loop to re-check `*stop_rx.borrow()` at the top.
                    let _ = changed;
                    continue;
                }
                _ = tokio::time::sleep(Duration::from_millis(timeout_ms)) => {
                    continue;
                }
                result = &mut next_fut => match result {
                    Ok(Some(msg)) => msg,
                    Ok(None) => {
                        warn!("trigger consumer stream ended unexpectedly");
                        break;
                    }
                    Err(e) => {
                        warn!(error = %e, "trigger consumer error; continuing");
                        continue;
                    }
                }
            };

            match msg.decode::<Trigger>() {
                Ok(trigger) => {
                    let now = Instant::now();
                    let evidence = trigger.evidence_hash.to_vec();
                    if let Some((_, publish_at)) = publish_times.remove(&evidence) {
                        let latency_ns =
                            now.saturating_duration_since(publish_at).as_nanos() as u64;
                        samples.push(LatencySample {
                            evidence_hash: evidence,
                            latency_ns,
                        });
                    } else {
                        debug!(
                            evidence_hex = hex::encode(&evidence),
                            "trigger arrived without matching publish (filtered out)"
                        );
                    }

                    if let Err(e) = msg.ack().await {
                        warn!(error = %e, "trigger ack failed");
                    }
                }
                Err(e) => {
                    warn!(error = %e, "trigger decode failed; nacking");
                    if let Err(e) = msg.nak().await {
                        warn!(error = %e, "trigger nak failed");
                    }
                }
            }
        }

        Ok(samples)
    });

    Ok(handle)
}

/// Build a `watch::channel<bool>` initialised to `false`. Caller flips
/// it to `true` when the publisher has finished.
pub fn stop_channel() -> (StopTx, StopRx) {
    watch::channel(false)
}
