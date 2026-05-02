//! sniper-executor: Fast Loop executor service.
//!
//! Phase 1 foundation skeleton:
//!
//!   * Connects to NATS JetStream (`NATS_URL`) and subscribes to
//!     `dl.v1.triggers.fired`. Trigger messages are decoded zero-copy from
//!     the JetStream `Bytes` payload via `prost::Message::decode` — no
//!     intermediate `Vec<u8>` allocation.
//!   * Consumes `dl.v1.control.kill` from Redis Streams, accepting the
//!     current gateway JSON envelope and the future protobuf wire shape.
//!   * Publishes `dl.v1.exec.submitted` / `filled` / `reverted` to Redis
//!     Streams when paper execution is enabled.
//!   * Publishes `dl.v1.exec.status` heartbeats every ~1s so gateway-api
//!     can proxy them over WebSocket to the Command Center UI.
//!   * Kill handling is split in two: process shutdown still uses an
//!     `AtomicBool`, while operator kill commands live in a scoped shared
//!     state that blocks new trigger execution and emits `KillAck`s.
//!
//! Phase 2 (execution logic) and Phase 3 (HSM-signed live trading) plug
//! into the `on_trigger` branch below.

mod control;
mod nonce;
mod paper;

use std::collections::HashSet;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use control::{
    blocks_market as kill_blocks_market, complete_if_drained, decode_kill_command, engage,
    engaged as kill_engaged, new_shared_state, validate_kill_command, ControlConfig,
    SharedKillState,
};
use dual_loop_common::bus::{ConsumerGroup, JetStreamClient, StreamPublisher};
use dual_loop_common::proto::control::Heartbeat;
use dual_loop_common::proto::execution::{ExecStatus, ExecutorState};
use dual_loop_common::proto::now_unix_ms;
use dual_loop_common::proto::trigger::Trigger;
use dual_loop_common::{topics, WIRE_VERSION};
use nonce::{DueExecution, NonceManagerHandle, NonceSnapshot};
use paper::{plan_paper_execution, BroadcastSink, PaperExecutorConfig};
use prost::Message;
use sniper_risk::{record_risk_outcome, RiskGuard, RiskLimits, TradeIntent};
use tokio::sync::Mutex;
use tokio::time::interval;
use tracing::{debug, error, info, warn};

const TRIGGERS_STREAM: &str = "DL_TRIGGERS";
const EXEC_STATUS_STREAM: &str = "DL_EXEC";
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(1);
const RECONCILE_INTERVAL: Duration = Duration::from_millis(250);

#[derive(Default)]
struct Counters {
    seen: AtomicU64,
    executed: AtomicU64,
    heartbeat_seq: AtomicU64,
}

type RedisPublisherHandle = Arc<Mutex<StreamPublisher>>;

#[derive(Clone)]
struct TriggerExecutionContext {
    redis_publisher: Option<RedisPublisherHandle>,
    nonce_manager: NonceManagerHandle,
    paper_cfg: PaperExecutorConfig,
    sink: BroadcastSink,
    risk_guard: Arc<RiskGuard>,
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
    let redis_url = std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379".into());
    let paper_cfg = PaperExecutorConfig::from_env();
    let control_cfg = ControlConfig::from_env();
    let heartbeat_consumer_name = format!("{}-risk-heartbeat", control_cfg.consumer_name);

    let shutdown = Arc::new(AtomicBool::new(false));
    let kill_state = new_shared_state();
    let counters = Arc::new(Counters::default());
    let nonce_manager = NonceManagerHandle::spawn(paper_cfg.base_nonce);
    let sink = BroadcastSink::Noop;
    let risk_guard = Arc::new(RiskGuard::new(RiskLimits::from_env(), now_unix_ms()));

    let redis_publisher = match StreamPublisher::connect(&redis_url).await {
        Ok(publisher) => Some(Arc::new(Mutex::new(publisher))),
        Err(e) => {
            warn!(error = %e, "Redis connect failed; exec.* publishing disabled");
            None
        }
    };

    info!(
        wire_version = WIRE_VERSION,
        nats_url = %nats_url,
        redis_url = %redis_url,
        redis_available = redis_publisher.is_some(),
        paper_mode,
        auto_fill = paper_cfg.auto_fill,
        base_nonce = paper_cfg.base_nonce,
        control_consumer = %control_cfg.consumer_name,
        heartbeat_consumer = %heartbeat_consumer_name,
        "sniper-executor starting (Phase 2-B paper foundation)"
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
        let shutdown = shutdown.clone();
        let kill_state = kill_state.clone();
        let counters = counters.clone();
        let ctx = TriggerExecutionContext {
            redis_publisher: redis_publisher.clone(),
            nonce_manager: nonce_manager.clone(),
            paper_cfg: paper_cfg.clone(),
            sink,
            risk_guard: risk_guard.clone(),
        };
        tokio::spawn(
            async move { run_trigger_consumer(js, shutdown, kill_state, counters, ctx).await },
        )
    });

    let heartbeat_task = js.clone().map(|js| {
        let shutdown = shutdown.clone();
        let kill_state = kill_state.clone();
        let counters = counters.clone();
        let nonce_manager = nonce_manager.clone();
        let risk_guard = risk_guard.clone();
        tokio::spawn(async move {
            run_heartbeat(
                js,
                shutdown,
                kill_state,
                counters,
                nonce_manager,
                risk_guard,
                paper_mode,
            )
            .await
        })
    });

    let reconcile_task = {
        let shutdown = shutdown.clone();
        let kill_state = kill_state.clone();
        let counters = counters.clone();
        let redis_publisher = redis_publisher.clone();
        let nonce_manager = nonce_manager.clone();
        let risk_guard = risk_guard.clone();
        tokio::spawn(async move {
            run_reconcile_loop(
                shutdown,
                kill_state,
                counters,
                nonce_manager,
                redis_publisher,
                risk_guard,
            )
            .await
        })
    };

    let control_task = {
        let shutdown = shutdown.clone();
        let redis_url = redis_url.clone();
        let redis_publisher = redis_publisher.clone();
        let nonce_manager = nonce_manager.clone();
        let kill_state = kill_state.clone();
        tokio::spawn(async move {
            run_control_consumer(
                redis_url,
                shutdown,
                nonce_manager,
                redis_publisher,
                kill_state,
                control_cfg,
            )
            .await
        })
    };

    let risk_heartbeat_task = {
        let shutdown = shutdown.clone();
        let redis_url = redis_url.clone();
        let risk_guard = risk_guard.clone();
        tokio::spawn(async move {
            run_risk_heartbeat_consumer(redis_url, shutdown, risk_guard, heartbeat_consumer_name)
                .await
        })
    };

    // Drive shutdown via SIGINT/SIGTERM → flip kill switch → children exit.
    shutdown_signal().await;
    info!("shutdown signal received; engaging kill switch and draining");
    shutdown.store(true, Ordering::SeqCst);

    // Give children a moment to flush their last heartbeat / ack in-flight.
    tokio::time::timeout(Duration::from_secs(5), async {
        if let Some(h) = trigger_task {
            let _ = h.await;
        }
        if let Some(h) = heartbeat_task {
            let _ = h.await;
        }
        let _ = reconcile_task.await;
        let _ = control_task.await;
        let _ = risk_heartbeat_task.await;
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
async fn run_trigger_consumer(
    js: JetStreamClient,
    shutdown: Arc<AtomicBool>,
    kill_state: SharedKillState,
    counters: Arc<Counters>,
    ctx: TriggerExecutionContext,
) {
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

    while !shutdown.load(Ordering::Relaxed) {
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

                if !shutdown.load(Ordering::Relaxed) {
                    if kill_blocks_market(&kill_state, trigger.market.as_ref()).await {
                        metrics::counter!("exec_triggers_blocked_total", "reason" => "kill")
                            .increment(1);
                        debug!(
                            market = trigger
                                .market
                                .as_ref()
                                .map(|market| market.polymarket_id.as_str())
                                .unwrap_or(""),
                            "trigger ignored because kill switch is engaged for this scope"
                        );
                    } else {
                        let now_ms = now_unix_ms();
                        submit_paper_execution(&trigger, now_ms, &counters, &ctx).await;
                    }
                }

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

fn trade_intent_from_trigger(
    trigger: &Trigger,
    now_ms: i64,
    cfg: &PaperExecutorConfig,
) -> TradeIntent {
    TradeIntent {
        market_id: trigger
            .market
            .as_ref()
            .map(|market| market.polymarket_id.clone())
            .unwrap_or_default(),
        size_usd: cfg.default_size_usd_micros,
        slippage_bps: trigger.slippage_bps,
        watchlist_valid_until_unix_ms: trigger.watchlist_valid_until_unix_ms,
        issued_at_unix_ms: trigger.issued_at_unix_ms,
        now_unix_ms: now_ms,
    }
}

async fn submit_paper_execution(
    trigger: &Trigger,
    now_ms: i64,
    counters: &Counters,
    ctx: &TriggerExecutionContext,
) -> bool {
    let intent = trade_intent_from_trigger(trigger, now_ms, &ctx.paper_cfg);
    let risk_result = ctx.risk_guard.evaluate(&intent);
    record_risk_outcome(&risk_result);
    if let Err(e) = risk_result {
        metrics::counter!("exec_triggers_blocked_total", "reason" => "risk").increment(1);
        warn!(
            error = %e,
            market = %intent.market_id,
            slippage_bps = intent.slippage_bps,
            issued_at_unix_ms = intent.issued_at_unix_ms,
            watchlist_valid_until_unix_ms = intent.watchlist_valid_until_unix_ms,
            "trigger blocked by risk guard"
        );
        return false;
    }

    match ctx.nonce_manager.allocate().await {
        Ok(nonce) => {
            let execution = plan_paper_execution(trigger, nonce, now_ms, &ctx.paper_cfg);
            if let Err(e) = publish_event(
                &ctx.redis_publisher,
                topics::EXEC_SUBMITTED,
                &execution.submitted,
            )
            .await
            {
                warn!(error = %e, nonce, "exec.submitted publish failed");
            }
            ctx.sink.submit(&execution);
            if let Err(e) = ctx.nonce_manager.track(execution.clone()).await {
                error!(error = %e, nonce, "failed to track in-flight execution");
                false
            } else {
                counters.executed.fetch_add(1, Ordering::Relaxed);
                metrics::counter!("exec_submitted_total").increment(1);
                info!(
                    nonce,
                    market = %execution.market_id(),
                    auto_fill = execution.fill.is_some(),
                    tx_hash = %hex::encode(execution.submitted.tx_hash.as_ref()),
                    "paper execution submitted"
                );
                true
            }
        }
        Err(e) => {
            error!(error = %e, "nonce allocation failed");
            false
        }
    }
}

/// Periodic `ExecStatus` publisher for the Command Center.
async fn run_heartbeat(
    js: JetStreamClient,
    shutdown: Arc<AtomicBool>,
    kill_state: SharedKillState,
    counters: Arc<Counters>,
    nonce_manager: NonceManagerHandle,
    risk_guard: Arc<RiskGuard>,
    paper_mode: bool,
) {
    let mut tick = interval(HEARTBEAT_INTERVAL);
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let total = metrics::counter!("sniper_executor_heartbeat_total");

    loop {
        tick.tick().await;
        let seq = counters.heartbeat_seq.fetch_add(1, Ordering::Relaxed) + 1;
        let shutdown_engaged = shutdown.load(Ordering::Relaxed);
        let engaged = shutdown_engaged || kill_engaged(&kill_state).await;
        let snapshot = match nonce_manager.snapshot(now_unix_ms()).await {
            Ok(snapshot) => snapshot,
            Err(e) => {
                warn!(error = %e, "nonce snapshot failed");
                NonceSnapshot::default()
            }
        };
        metrics::gauge!("executor_next_nonce").set(snapshot.next_nonce as f64);
        metrics::gauge!("executor_in_flight_total").set(snapshot.in_flight as f64);
        metrics::gauge!("executor_oldest_in_flight_age_ms")
            .set(snapshot.oldest_in_flight_age_ms.unwrap_or(0) as f64);
        risk_guard.emit_metrics(now_unix_ms());

        let status = ExecStatus {
            heartbeat_seq: seq,
            emitted_at_unix_ms: now_unix_ms(),
            state: if engaged {
                if snapshot.in_flight > 0 {
                    ExecutorState::Draining as i32
                } else {
                    ExecutorState::Halted as i32
                }
            } else if snapshot.in_flight > 0 {
                ExecutorState::WaitingFill as i32
            } else {
                ExecutorState::Idle as i32
            },
            current_market: snapshot.current_market,
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

        if shutdown_engaged {
            // Flush one last status then exit; gateway-api will observe
            // the HALTED state and propagate to UI.
            break;
        }
    }

    info!("heartbeat task shutting down");
}

async fn run_reconcile_loop(
    shutdown: Arc<AtomicBool>,
    kill_state: SharedKillState,
    counters: Arc<Counters>,
    nonce_manager: NonceManagerHandle,
    redis_publisher: Option<RedisPublisherHandle>,
    risk_guard: Arc<RiskGuard>,
) {
    let mut tick = interval(RECONCILE_INTERVAL);
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tick.tick().await;
        let now_ms = now_unix_ms();
        match nonce_manager.poll_due(now_ms).await {
            Ok(due) => {
                for event in due {
                    match event {
                        DueExecution::Fill { nonce, execution } => {
                            if let Some(fill) = execution.fill.as_ref() {
                                if let Err(e) =
                                    publish_event(&redis_publisher, topics::EXEC_FILLED, fill).await
                                {
                                    warn!(error = %e, nonce, "exec.filled publish failed");
                                }
                                metrics::counter!("exec_filled_total").increment(1);
                                risk_guard.record_fill(fill);
                                risk_guard.emit_metrics(now_ms);
                                info!(
                                    nonce,
                                    market = %execution.market_id(),
                                    tx_hash = %hex::encode(fill.tx_hash.as_ref()),
                                    "paper fill emitted"
                                );
                            }
                        }
                        DueExecution::Revert { nonce, execution } => {
                            if let Err(e) = publish_event(
                                &redis_publisher,
                                topics::EXEC_REVERTED,
                                &execution.timeout_revert,
                            )
                            .await
                            {
                                warn!(error = %e, nonce, "exec.reverted publish failed");
                            }
                            metrics::counter!("exec_reverted_total", "reason" => "timeout")
                                .increment(1);
                            risk_guard.record_revert(&execution.timeout_revert);
                            risk_guard.emit_metrics(now_ms);
                            info!(
                                nonce,
                                market = %execution.market_id(),
                                tx_hash = %hex::encode(execution.timeout_revert.tx_hash.as_ref()),
                                "paper timeout emitted"
                            );
                        }
                    }
                }
            }
            Err(e) => {
                error!(error = %e, "reconcile loop failed");
                break;
            }
        }

        let kill_is_engaged = kill_engaged(&kill_state).await;
        if kill_is_engaged || shutdown.load(Ordering::Relaxed) {
            match nonce_manager.snapshot(now_ms).await {
                Ok(snapshot) => {
                    if kill_is_engaged && snapshot.in_flight == 0 {
                        if let Some(ack) = complete_if_drained(&kill_state).await {
                            if let Err(e) =
                                publish_event(&redis_publisher, topics::CONTROL_KILL_ACK, &ack)
                                    .await
                            {
                                warn!(error = %e, "kill complete ack publish failed");
                            }
                        }
                    }
                    if shutdown.load(Ordering::Relaxed) && snapshot.in_flight == 0 {
                        break;
                    }
                }
                Err(e) => {
                    warn!(error = %e, "nonce snapshot failed during drain");
                    if shutdown.load(Ordering::Relaxed) {
                        break;
                    }
                }
            }
        }
    }

    let _ = counters;
    info!("reconcile loop shutting down");
}

async fn run_control_consumer(
    redis_url: String,
    shutdown: Arc<AtomicBool>,
    nonce_manager: NonceManagerHandle,
    redis_publisher: Option<RedisPublisherHandle>,
    kill_state: SharedKillState,
    control_cfg: ControlConfig,
) {
    let mut consumer = match ConsumerGroup::connect(
        &redis_url,
        topics::CONTROL_KILL,
        topics::consumer_groups::SNIPER_EXECUTOR,
        &control_cfg.consumer_name,
    )
    .await
    {
        Ok(consumer) => consumer,
        Err(e) => {
            error!(error = %e, "control consumer failed to connect; kill-path disabled");
            return;
        }
    };

    let mut seen_nonces = HashSet::new();
    info!(topic = topics::CONTROL_KILL, "control consumer up");

    while !shutdown.load(Ordering::Relaxed) {
        let messages = match consumer.read_next().await {
            Ok(messages) => messages,
            Err(e) => {
                warn!(error = %e, "control consumer read failed; backing off");
                tokio::time::sleep(Duration::from_millis(500)).await;
                continue;
            }
        };

        for message in messages {
            let ack = match decode_kill_command(&message) {
                Ok(command) => {
                    let now_ms = now_unix_ms();
                    match validate_kill_command(
                        &command,
                        &control_cfg.hmac_secret,
                        &mut seen_nonces,
                        now_ms,
                    ) {
                        Ok(()) => {
                            let snapshot = match nonce_manager.snapshot(now_ms).await {
                                Ok(snapshot) => snapshot,
                                Err(e) => {
                                    warn!(error = %e, "nonce snapshot failed during kill accept");
                                    NonceSnapshot::default()
                                }
                            };
                            let ack = engage(&kill_state, &command, snapshot.in_flight).await;
                            info!(
                                scope = command.scope,
                                nonce = command.nonce,
                                market = command
                                    .market
                                    .as_ref()
                                    .map(|market| market.polymarket_id.as_str())
                                    .unwrap_or(""),
                                "kill command accepted"
                            );
                            ack
                        }
                        Err(ack) => ack,
                    }
                }
                Err(e) => {
                    warn!(error = %e, "kill command decode failed");
                    if let Err(ack_error) = consumer.ack(&message.id).await {
                        warn!(error = %ack_error, id = %message.id, "control ack failed");
                    }
                    continue;
                }
            };

            if let Err(e) = publish_event(&redis_publisher, topics::CONTROL_KILL_ACK, &ack).await {
                warn!(error = %e, "kill ack publish failed");
            }
            if let Err(e) = consumer.ack(&message.id).await {
                warn!(error = %e, id = %message.id, "control ack failed");
            }
        }
    }

    info!("control consumer shutting down");
}

async fn run_risk_heartbeat_consumer(
    redis_url: String,
    shutdown: Arc<AtomicBool>,
    risk_guard: Arc<RiskGuard>,
    consumer_name: String,
) {
    let mut consumer = match ConsumerGroup::connect(
        &redis_url,
        topics::CONTROL_HEARTBEAT,
        topics::consumer_groups::SNIPER_RISK,
        &consumer_name,
    )
    .await
    {
        Ok(consumer) => consumer,
        Err(e) => {
            error!(error = %e, "heartbeat consumer failed to connect; dead-man heartbeats disabled");
            return;
        }
    };

    info!(
        topic = topics::CONTROL_HEARTBEAT,
        "risk heartbeat consumer up"
    );

    while !shutdown.load(Ordering::Relaxed) {
        let messages = match consumer.read_next().await {
            Ok(messages) => messages,
            Err(e) => {
                warn!(error = %e, "heartbeat consumer read failed; backing off");
                tokio::time::sleep(Duration::from_millis(500)).await;
                continue;
            }
        };

        for message in messages {
            match message.decode::<Heartbeat>() {
                Ok(heartbeat) => {
                    risk_guard.record_heartbeat(heartbeat.heartbeat_at_unix_ms);
                    risk_guard.emit_metrics(now_unix_ms());
                    debug!(
                        operator_id = %heartbeat.operator_id,
                        heartbeat_at_unix_ms = heartbeat.heartbeat_at_unix_ms,
                        "operator heartbeat recorded"
                    );
                }
                Err(e) => warn!(error = %e, "heartbeat decode failed"),
            }

            if let Err(e) = consumer.ack(&message.id).await {
                warn!(error = %e, id = %message.id, "heartbeat ack failed");
            }
        }
    }

    info!("risk heartbeat consumer shutting down");
}

async fn publish_event<M: Message>(
    redis_publisher: &Option<RedisPublisherHandle>,
    topic: &str,
    message: &M,
) -> Result<()> {
    let Some(redis_publisher) = redis_publisher else {
        return Ok(());
    };

    let mut publisher = redis_publisher.lock().await;
    publisher.publish(topic, message).await?;
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;
    use bytes::Bytes;
    use dual_loop_common::proto::common::{MarketId, Money, Uuid};

    fn risk_limits(max_slippage_bps: u32) -> RiskLimits {
        RiskLimits {
            per_market_cap_usd: 10_000_000,
            global_cap_usd: 100_000_000,
            max_slippage_bps,
            daily_budget_usd: 1_000_000_000,
            stale_signal_threshold_ms: 60_000,
            dead_man_timeout_ms: 300_000,
            cb_consecutive_fails: 3,
            cb_failure_rate: 0.5,
            cb_window_secs: 60,
        }
    }

    fn sample_trigger(now_ms: i64, slippage_bps: u32) -> Trigger {
        Trigger {
            message_id: Some(Uuid {
                value: Bytes::from_static(&[0xEF; 16]),
            }),
            market: Some(MarketId {
                polymarket_id: "risk-market-1".to_string(),
                ctf_address: "0xrisk".to_string(),
                market_specific_key: "0xabc".to_string(),
            }),
            kind: 1,
            evidence_hash: Bytes::from_static(&[0x55; 32]),
            eta_block: 7,
            observed_at_ns: 100,
            expected_price: Some(Money {
                amount_micros: 650_000,
            }),
            slippage_bps,
            source_count: 1,
            issued_at_unix_ms: now_ms - 1_000,
            watchlist_valid_until_unix_ms: now_ms + 60_000,
        }
    }

    fn execution_context(max_slippage_bps: u32, now_ms: i64) -> TriggerExecutionContext {
        TriggerExecutionContext {
            redis_publisher: None,
            nonce_manager: NonceManagerHandle::spawn(0),
            paper_cfg: PaperExecutorConfig::default(),
            sink: BroadcastSink::Noop,
            risk_guard: Arc::new(RiskGuard::new(risk_limits(max_slippage_bps), now_ms)),
        }
    }

    #[test]
    fn trade_intent_uses_trigger_risk_fields() {
        let now_ms = 1_000_000;
        let trigger = sample_trigger(now_ms, 25);

        let intent = trade_intent_from_trigger(&trigger, now_ms, &PaperExecutorConfig::default());

        assert_eq!(intent.market_id, "risk-market-1");
        assert_eq!(
            intent.size_usd,
            PaperExecutorConfig::default().default_size_usd_micros
        );
        assert_eq!(intent.slippage_bps, 25);
        assert_eq!(intent.issued_at_unix_ms, now_ms - 1_000);
        assert_eq!(intent.watchlist_valid_until_unix_ms, now_ms + 60_000);
    }

    #[tokio::test]
    async fn risk_reject_prevents_nonce_allocation_and_tracking() {
        let now_ms = 1_000_000;
        let ctx = execution_context(10, now_ms);
        let counters = Counters::default();
        let trigger = sample_trigger(now_ms, 25);

        let submitted = submit_paper_execution(&trigger, now_ms, &counters, &ctx).await;
        let snapshot = ctx
            .nonce_manager
            .snapshot(now_ms)
            .await
            .expect("nonce snapshot");

        assert!(!submitted);
        assert_eq!(counters.executed.load(Ordering::Relaxed), 0);
        assert_eq!(snapshot.next_nonce, 0);
        assert_eq!(snapshot.in_flight, 0);
    }

    #[tokio::test]
    async fn risk_accept_allows_paper_submission_and_tracking() {
        let now_ms = 1_000_000;
        let ctx = execution_context(50, now_ms);
        let counters = Counters::default();
        let trigger = sample_trigger(now_ms, 25);

        let submitted = submit_paper_execution(&trigger, now_ms, &counters, &ctx).await;
        let snapshot = ctx
            .nonce_manager
            .snapshot(now_ms)
            .await
            .expect("nonce snapshot");

        assert!(submitted);
        assert_eq!(counters.executed.load(Ordering::Relaxed), 1);
        assert_eq!(snapshot.next_nonce, 1);
        assert_eq!(snapshot.in_flight, 1);
    }
}
