use std::str::FromStr;
use std::time::Duration;

use alloy_primitives::Address;
use anyhow::{anyhow, Result};
use dual_loop_common::bus::{ConsumerGroup, JetStreamClient};
use dual_loop_common::proto::mock::MockMempoolTx;
use dual_loop_common::proto::watchlist::TriggerKind;
use dual_loop_common::topics;
use tracing::{debug, info, warn};

use crate::matcher::TriggerMatcher;
use crate::watchlist::WatchlistStore;

pub const MOCK_MEMPOOL_TOPIC: &str = "dl.v1.mempool.mock";
const TRIGGERS_STREAM: &str = "DL_TRIGGERS";

pub fn enabled_from_env() -> bool {
    std::env::var("MOCK_MEMPOOL_ENABLED")
        .map(|value| !matches!(value.as_str(), "0" | "false" | "False" | "no" | "NO"))
        .unwrap_or(false)
}

pub async fn spawn_mock_consumer(
    redis_url: String,
    nats_url: String,
    store: WatchlistStore,
    mut shutdown_rx: tokio::sync::watch::Receiver<bool>,
) -> Result<tokio::task::JoinHandle<()>> {
    let js = JetStreamClient::connect(&nats_url).await?;
    js.ensure_stream(TRIGGERS_STREAM, vec![topics::TRIGGERS_FIRED.into()])
        .await?;

    let consumer_id = format!(
        "{}-mock",
        std::env::var("HOSTNAME").unwrap_or_else(|_| "sniper-ingest-1".to_string())
    );
    let mut consumer = ConsumerGroup::connect(
        &redis_url,
        MOCK_MEMPOOL_TOPIC,
        topics::consumer_groups::SNIPER_INGEST,
        &consumer_id,
    )
    .await?;

    info!(
        redis_url = %redis_url,
        nats_url = %nats_url,
        topic = MOCK_MEMPOOL_TOPIC,
        "mock mempool consumer connected"
    );

    Ok(tokio::spawn(async move {
        let mut matcher = TriggerMatcher::new(store);

        loop {
            tokio::select! {
                changed = shutdown_rx.changed() => {
                    match changed {
                        Ok(()) if *shutdown_rx.borrow() => {
                            info!("mock mempool consumer shutting down");
                            break;
                        }
                        Ok(()) => {}
                        Err(_) => break,
                    }
                }
                read = consumer.read_next() => {
                    match read {
                        Ok(messages) => {
                            for message in messages {
                                let message_id = message.id.clone();
                                match handle_mock_message(&mut matcher, &js, message).await {
                                    Ok(()) => {
                                        if let Err(error) = consumer.ack(&message_id).await {
                                            warn!(?error, %message_id, "mock mempool ack failed");
                                        }
                                    }
                                    Err(error) => {
                                        warn!(?error, %message_id, "mock mempool handler failed; leaving pending");
                                    }
                                }
                            }
                        }
                        Err(error) => {
                            warn!(?error, "mock mempool consumer read failed; backing off");
                            tokio::time::sleep(Duration::from_millis(500)).await;
                        }
                    }
                }
            }
        }
    }))
}

async fn handle_mock_message(
    matcher: &mut TriggerMatcher,
    js: &JetStreamClient,
    message: dual_loop_common::bus::ConsumerMessage,
) -> Result<()> {
    let mock = message.decode::<MockMempoolTx>()?;
    let market = mock
        .market
        .clone()
        .ok_or_else(|| anyhow!("mock mempool tx missing market"))?;
    let contract_address = Address::from_str(&market.ctf_address)
        .map_err(|error| anyhow!("invalid mock ctf address {}: {error}", market.ctf_address))?;
    let kind = TriggerKind::try_from(mock.kind).unwrap_or(TriggerKind::LargeFill);
    let tx_hash_hex = hex::encode(mock.tx_hash.as_ref());

    let trigger = matcher.try_match_mock(
        contract_address,
        kind,
        mock.observed_at_ns,
        mock.tx_hash.to_vec(),
    );

    if let Some(trigger) = trigger {
        js.publish(topics::TRIGGERS_FIRED, &trigger).await?;
        info!(
            market_id = market.polymarket_id,
            tx_hash = %tx_hash_hex,
            kind = ?kind,
            "mock mempool tx matched and trigger published"
        );
    } else {
        debug!(
            market_id = market.polymarket_id,
            tx_hash = %tx_hash_hex,
            kind = ?kind,
            "mock mempool tx did not match watchlist policy"
        );
    }

    Ok(())
}
