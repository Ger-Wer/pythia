use std::collections::BTreeMap;

use anyhow::{anyhow, Result};
use dual_loop_common::proto::common::MarketId;
use tokio::sync::{mpsc, oneshot};

use crate::paper::PlannedExecution;

#[derive(Debug, Clone)]
pub enum DueExecution {
    Fill {
        nonce: u64,
        execution: PlannedExecution,
    },
    Revert {
        nonce: u64,
        execution: PlannedExecution,
    },
}

#[derive(Debug, Clone, Default)]
pub struct NonceSnapshot {
    pub next_nonce: u64,
    pub in_flight: usize,
    pub current_market: Option<MarketId>,
    pub oldest_in_flight_age_ms: Option<i64>,
}

#[derive(Clone)]
pub struct NonceManagerHandle {
    tx: mpsc::Sender<Command>,
}

impl NonceManagerHandle {
    pub fn spawn(base_nonce: u64) -> Self {
        let (tx, mut rx) = mpsc::channel(128);
        tokio::spawn(async move {
            let mut next_nonce = base_nonce;
            let mut in_flight: BTreeMap<u64, PlannedExecution> = BTreeMap::new();

            while let Some(command) = rx.recv().await {
                match command {
                    Command::Allocate { reply } => {
                        let nonce = next_nonce;
                        next_nonce = next_nonce.saturating_add(1);
                        let _ = reply.send(nonce);
                    }
                    Command::Track { execution } => {
                        let execution = *execution;
                        in_flight.insert(execution.nonce, execution);
                    }
                    Command::PollDue { now_ms, reply } => {
                        let mut due = Vec::new();
                        let nonces: Vec<u64> = in_flight.keys().copied().collect();

                        for nonce in nonces {
                            let Some(execution) = in_flight.get(&nonce).cloned() else {
                                continue;
                            };

                            let fill_due = execution.fill_due_at_unix_ms;
                            let timeout_due = execution.timeout_at_unix_ms <= now_ms;
                            let fill_ready = fill_due.is_some_and(|due_at| due_at <= now_ms);
                            let fill_wins = fill_due
                                .map(|due_at| due_at <= execution.timeout_at_unix_ms)
                                .unwrap_or(false);

                            if fill_ready && fill_wins {
                                in_flight.remove(&nonce);
                                due.push(DueExecution::Fill { nonce, execution });
                                continue;
                            }
                            if timeout_due {
                                in_flight.remove(&nonce);
                                due.push(DueExecution::Revert { nonce, execution });
                                continue;
                            }
                            if fill_ready {
                                in_flight.remove(&nonce);
                                due.push(DueExecution::Fill { nonce, execution });
                            }
                        }

                        let _ = reply.send(due);
                    }
                    Command::Snapshot { now_ms, reply } => {
                        let oldest = in_flight
                            .values()
                            .map(|execution| execution.submitted_at_unix_ms)
                            .min();
                        let current_market = in_flight
                            .values()
                            .min_by_key(|execution| execution.submitted_at_unix_ms)
                            .and_then(|execution| execution.market());
                        let snapshot = NonceSnapshot {
                            next_nonce,
                            in_flight: in_flight.len(),
                            current_market,
                            oldest_in_flight_age_ms: oldest.map(|ts| now_ms.saturating_sub(ts)),
                        };
                        let _ = reply.send(snapshot);
                    }
                }
            }
        });

        Self { tx }
    }

    pub async fn allocate(&self) -> Result<u64> {
        let (reply_tx, reply_rx) = oneshot::channel();
        self.tx
            .send(Command::Allocate { reply: reply_tx })
            .await
            .map_err(|_| anyhow!("nonce manager stopped"))?;
        reply_rx
            .await
            .map_err(|_| anyhow!("nonce manager dropped allocate reply"))
    }

    pub async fn track(&self, execution: PlannedExecution) -> Result<()> {
        self.tx
            .send(Command::Track {
                execution: Box::new(execution),
            })
            .await
            .map_err(|_| anyhow!("nonce manager stopped"))?;
        Ok(())
    }

    pub async fn poll_due(&self, now_ms: i64) -> Result<Vec<DueExecution>> {
        let (reply_tx, reply_rx) = oneshot::channel();
        self.tx
            .send(Command::PollDue {
                now_ms,
                reply: reply_tx,
            })
            .await
            .map_err(|_| anyhow!("nonce manager stopped"))?;
        reply_rx
            .await
            .map_err(|_| anyhow!("nonce manager dropped poll_due reply"))
    }

    pub async fn snapshot(&self, now_ms: i64) -> Result<NonceSnapshot> {
        let (reply_tx, reply_rx) = oneshot::channel();
        self.tx
            .send(Command::Snapshot {
                now_ms,
                reply: reply_tx,
            })
            .await
            .map_err(|_| anyhow!("nonce manager stopped"))?;
        reply_rx
            .await
            .map_err(|_| anyhow!("nonce manager dropped snapshot reply"))
    }
}

enum Command {
    Allocate {
        reply: oneshot::Sender<u64>,
    },
    Track {
        execution: Box<PlannedExecution>,
    },
    PollDue {
        now_ms: i64,
        reply: oneshot::Sender<Vec<DueExecution>>,
    },
    Snapshot {
        now_ms: i64,
        reply: oneshot::Sender<NonceSnapshot>,
    },
}

#[cfg(test)]
mod tests {
    use bytes::Bytes;
    use dual_loop_common::proto::common::{MarketId, Money, Uuid};
    use dual_loop_common::proto::trigger::Trigger;

    use super::*;
    use crate::paper::{plan_paper_execution, PaperExecutorConfig};

    fn sample_trigger() -> Trigger {
        Trigger {
            message_id: Some(Uuid {
                value: Bytes::from_static(&[0xCD; 16]),
            }),
            market: Some(MarketId {
                polymarket_id: "market-actor".to_string(),
                ctf_address: "0xactor".to_string(),
                market_specific_key: String::new(),
            }),
            kind: 1,
            evidence_hash: Bytes::from_static(&[0x44; 32]),
            eta_block: 1,
            observed_at_ns: 100,
            expected_price: Some(Money {
                amount_micros: 650_000,
            }),
            slippage_bps: 15,
            source_count: 1,
        }
    }

    #[tokio::test]
    async fn allocates_monotonic_nonces() {
        let actor = NonceManagerHandle::spawn(41);

        let n1 = actor.allocate().await.expect("n1");
        let n2 = actor.allocate().await.expect("n2");

        assert_eq!(n1, 41);
        assert_eq!(n2, 42);
    }

    #[tokio::test]
    async fn returns_fill_when_due_before_timeout() {
        let actor = NonceManagerHandle::spawn(0);
        let trigger = sample_trigger();
        let cfg = PaperExecutorConfig {
            fill_delay_ms: 100,
            stuck_timeout_ms: 1_000,
            ..PaperExecutorConfig::default()
        };
        let nonce = actor.allocate().await.expect("nonce");
        actor
            .track(plan_paper_execution(&trigger, nonce, 1_000, &cfg))
            .await
            .expect("track");

        let due = actor.poll_due(1_150).await.expect("poll_due");

        assert!(matches!(
            due.as_slice(),
            [DueExecution::Fill { nonce: 0, .. }]
        ));
    }

    #[tokio::test]
    async fn returns_revert_when_stuck_timeout_expires_without_fill() {
        let actor = NonceManagerHandle::spawn(0);
        let trigger = sample_trigger();
        let cfg = PaperExecutorConfig {
            auto_fill: false,
            stuck_timeout_ms: 500,
            ..PaperExecutorConfig::default()
        };
        let nonce = actor.allocate().await.expect("nonce");
        actor
            .track(plan_paper_execution(&trigger, nonce, 2_000, &cfg))
            .await
            .expect("track");

        let due = actor.poll_due(2_600).await.expect("poll_due");

        assert!(matches!(
            due.as_slice(),
            [DueExecution::Revert { nonce: 0, .. }]
        ));
    }
}
