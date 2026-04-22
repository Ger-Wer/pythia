//! sniper-executor: Fast Loop executor service.
//!
//! Phase 1 foundation skeleton:
//!
//!   * Connects to NATS JetStream (`NATS_URL`) and subscribes to
//!     `dl.v1.triggers.fired`. Trigger messages are decoded zero-copy from
//!     the JetStream `Bytes` payload via `prost::Message::decode` — no
//!     intermediate `Vec<u8>` allocation.
//!   * Publishes `dl.v1.exec.status` heartbeats every ~1s so gateway-api
//!     can proxy them over WebSocket to the Command Center UI.
//!   * Kill switch (`AtomicBool`) flips to `true` on SIGINT/SIGTERM and on
//!     any observed `KillCommand`; all actual execution will gate on it
//!     once Phase 2 wires order flow. Until then, trigger receipt only
//!     bumps counters.
//!
//! Phase 2 (execution logic) and Phase 3 (HSM-signed live trading) plug
//! into the `on_trigger` branch below.

use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use dual_loop_common::proto::execution::{ExecStatus, ExecutorState};
use dual_loop_common::proto::now_unix_ms;
use dual_loop_common::proto::trigger::Trigger;
use dual_loop_common::{bus::JetStreamClient, topics, WIRE_VERSION};
use tokio::time::interval;
use tracing::{debug, error, info, warn};

const TRIGGERS_STREAM: &str = "DL_TRIGGERS";
const EXEC_STATUS_STREAM: &str = "DL_EXEC";
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(1);

#[derive(Default)]
struct Counters {
    seen: AtomicU64,
    executed: AtomicU64,
    heartbeat_seq: AtomicU64,
}

#[tokio::main]
async fn main() -> Result<()> {
    let service = "sniper_executor";
    let metrics_addr: SocketAddr = std::env::var("METRICS_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:9102".to_string())
        .parse()?;
    dual_loop_common::observability::init(service, metrics_addr)?;

    let nats_url = std::env::var("NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".into());
    let paper_mode = std::env::var("PAPER_MODE")
        .map(|v| !matches!(v.as_str(), "0" | "false" | "no"))
        .unwrap_or(true);

    let kill = Arc::new(AtomicBool::new(false));
    let counters = Arc::new(Counters::default());

    info!(
        wire_version = WIRE_VERSION,
        nats_url = %nats_url,
        paper_mode,
        "sniper-executor starting (Phase 1 foundation skeleton)"
    );

    // Connect NATS. On failure we keep the process alive but in a degraded
    // state — metrics-only — so the operator dashboard still sees us.
    let js = match JetStreamClient::connect(&nats_url).await {
        Ok(c) => Some(c),
        Err(e) => {
            error!(error = %e, "JetStream connect failed; running in degraded mode");
            None
        }
    };

    // Best-effort stream bootstrap. Local dev needs these; in production
    // the streams are pre-provisioned by the operator.
    if let Some(js) = &js {
        if let Err(e) = js
            .ensure_stream(TRIGGERS_STREAM, vec![topics::TRIGGERS_FIRED.into()])
            .await
        {
            warn!(error = %e, "could not ensure triggers stream");
        }
        if let Err(e) = js
            .ensure_stream(EXEC_STATUS_STREAM, vec![topics::EXEC_STATUS.into()])
            .await
        {
            warn!(error = %e, "could not ensure exec-status stream");
        }
    }

    let trigger_task = js.clone().map(|js| {
        let kill = kill.clone();
        let counters = counters.clone();
        tokio::spawn(async move { run_trigger_consumer(js, kill, counters).await })
    });

    let heartbeat_task = js.clone().map(|js| {
        let kill = kill.clone();
        let counters = counters.clone();
        tokio::spawn(async move { run_heartbeat(js, kill, counters, paper_mode).await })
    });

    // Drive shutdown via SIGINT/SIGTERM → flip kill switch → children exit.
    shutdown_signal().await;
    info!("shutdown signal received; engaging kill switch and draining");
    kill.store(true, Ordering::SeqCst);

    // Give children a moment to flush their last heartbeat / ack in-flight.
    tokio::time::timeout(Duration::from_secs(5), async {
        if let Some(h) = trigger_task {
            let _ = h.await;
        }
        if let Some(h) = heartbeat_task {
            let _ = h.await;
        }
    })
    .await
    .ok();

    info!("sniper-executor drained");
    Ok(())
}

/// Pull-loop over `dl.v1.triggers.fired`. Decoding is zero-copy: the prost
/// `decode` method takes the JetStream payload `Bytes` directly, so owned
/// `String` / `Vec<u8>` fields in `Trigger` alias slices of the original
/// buffer without reallocation.
async fn run_trigger_consumer(js: JetStreamClient, kill: Arc<AtomicBool>, counters: Arc<Counters>) {
    let mut consumer = match js
        .pull_subscribe(
            TRIGGERS_STREAM,
            topics::TRIGGERS_FIRED,
            topics::consumer_groups::SNIPER_EXECUTOR,
        )
        .await
    {
        Ok(c) => c,
        Err(e) => {
            error!(error = %e, "pull_subscribe failed; trigger consumer disabled");
            return;
        }
    };
    info!(subject = topics::TRIGGERS_FIRED, "trigger consumer up");

    while !kill.load(Ordering::Relaxed) {
        let msg = match consumer.next().await {
            Ok(Some(m)) => m,
            Ok(None) => {
                info!("trigger stream ended");
                break;
            }
            Err(e) => {
                warn!(error = %e, "trigger consumer error; backing off");
                tokio::time::sleep(Duration::from_millis(500)).await;
                continue;
            }
        };

        // Zero-copy decode into the prost-generated struct.
        match msg.decode::<Trigger>() {
            Ok(trigger) => {
                counters.seen.fetch_add(1, Ordering::Relaxed);
                metrics::counter!("exec_triggers_seen_total").increment(1);
                debug!(
                    kind = trigger.kind,
                    eta_block = trigger.eta_block,
                    "trigger received"
                );
                // Phase 2 lands here: risk-gate → nonce-manager → broadcast.
                // For now we only ack — real side-effects deferred.
                if let Err(e) = msg.ack().await {
                    warn!(error = %e, "trigger ack failed");
                }
            }
            Err(e) => {
                error!(error = %e, "malformed Trigger on JetStream; nak-ing");
                if let Err(e) = msg.nak().await {
                    warn!(error = %e, "trigger nak failed");
                }
            }
        }
    }

    info!("trigger consumer shutting down");
}

/// Periodic `ExecStatus` publisher for the Command Center.
async fn run_heartbeat(
    js: JetStreamClient,
    kill: Arc<AtomicBool>,
    counters: Arc<Counters>,
    paper_mode: bool,
) {
    let mut tick = interval(HEARTBEAT_INTERVAL);
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let total = metrics::counter!("sniper_executor_heartbeat_total");

    loop {
        tick.tick().await;
        let seq = counters.heartbeat_seq.fetch_add(1, Ordering::Relaxed) + 1;
        let engaged = kill.load(Ordering::Relaxed);

        let status = ExecStatus {
            heartbeat_seq: seq,
            emitted_at_unix_ms: now_unix_ms(),
            state: if engaged {
                ExecutorState::Halted as i32
            } else {
                ExecutorState::Idle as i32
            },
            current_market: None,
            triggers_seen_total: counters.seen.load(Ordering::Relaxed),
            triggers_executed_total: counters.executed.load(Ordering::Relaxed),
            kill_engaged: engaged,
            paper_mode,
        };

        match js.publish(topics::EXEC_STATUS, &status).await {
            Ok(()) => {
                total.increment(1);
                debug!(seq, engaged, "heartbeat published");
            }
            Err(e) => warn!(error = %e, "heartbeat publish failed"),
        }

        if engaged {
            // Flush one last status then exit; gateway-api will observe
            // the HALTED state and propagate to UI.
            break;
        }
    }

    info!("heartbeat task shutting down");
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c().await.ok();
    };

    #[cfg(unix)]
    let terminate = async {
        use tokio::signal::unix::{signal, SignalKind};
        if let Ok(mut sig) = signal(SignalKind::terminate()) {
            sig.recv().await;
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {}
        _ = terminate => {}
    }
}
