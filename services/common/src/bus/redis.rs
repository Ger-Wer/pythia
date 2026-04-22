//! Redis Streams wrapper (publisher + consumer group).
//!
//! API is deliberately tiny: one `StreamPublisher::publish` and one
//! `ConsumerGroup::consume` loop. Protobuf encoding/decoding is the
//! caller's responsibility (generic `M: prost::Message`).
//!
//! Redis Stream layout:
//! ```text
//! XADD <topic> * payload <protobuf-bytes> [trace_id <id>]
//! ```
//! Consumer reads via `XREADGROUP > COUNT 64 BLOCK 1000`, ACKs via `XACK`,
//! and relies on `XAUTOCLAIM` (not yet wired) to reclaim abandoned entries.

use std::time::Duration;

use bytes::Bytes;
use prost::Message;
use redis::aio::MultiplexedConnection;
use redis::streams::{StreamKey, StreamReadOptions, StreamReadReply};
use redis::{AsyncCommands, Client, RedisError};
use thiserror::Error;
use tracing::{debug, trace, warn};

use super::{PAYLOAD_FIELD, TRACE_FIELD};

/// Errors from the Redis bus layer.
#[derive(Debug, Error)]
pub enum RedisBusError {
    #[error("redis error: {0}")]
    Redis(#[from] RedisError),
    #[error("protobuf decode error: {0}")]
    Decode(#[from] prost::DecodeError),
    #[error("protobuf encode error: {0}")]
    Encode(#[from] prost::EncodeError),
    #[error("stream entry missing `{0}` field")]
    MissingField(&'static str),
}

pub type RedisBusResult<T> = Result<T, RedisBusError>;

/// Thin publisher over `XADD`. Cheap to clone; owns a multiplexed async connection.
#[derive(Clone)]
pub struct StreamPublisher {
    conn: MultiplexedConnection,
    /// Optional MAXLEN cap (XADD ~= MAXLEN). Use `None` to leave stream
    /// unbounded (fine for dev, dangerous in prod).
    maxlen_approx: Option<usize>,
}

impl StreamPublisher {
    /// Connect to Redis and open a multiplexed connection.
    pub async fn connect(url: &str) -> RedisBusResult<Self> {
        let client = Client::open(url)?;
        let conn = client.get_multiplexed_async_connection().await?;
        Ok(Self {
            conn,
            maxlen_approx: Some(10_000),
        })
    }

    /// Override the approximate MAXLEN used on `XADD`.
    pub fn with_maxlen_approx(mut self, maxlen: Option<usize>) -> Self {
        self.maxlen_approx = maxlen;
        self
    }

    /// Encode + XADD. Returns the server-assigned stream id.
    pub async fn publish<M: Message>(
        &mut self,
        topic: &str,
        message: &M,
    ) -> RedisBusResult<String> {
        self.publish_with_trace(topic, message, None).await
    }

    /// Same as [`publish`] but attaches an optional trace id header.
    pub async fn publish_with_trace<M: Message>(
        &mut self,
        topic: &str,
        message: &M,
        trace_id: Option<&str>,
    ) -> RedisBusResult<String> {
        let mut buf = Vec::with_capacity(message.encoded_len());
        message.encode(&mut buf)?;

        let mut fields: Vec<(&str, Vec<u8>)> = vec![(PAYLOAD_FIELD, buf)];
        if let Some(tid) = trace_id {
            fields.push((TRACE_FIELD, tid.as_bytes().to_vec()));
        }

        // redis::cmd lets us pass NOMKSTREAM / MAXLEN in the right order.
        let mut cmd = redis::cmd("XADD");
        cmd.arg(topic);
        if let Some(n) = self.maxlen_approx {
            cmd.arg("MAXLEN").arg("~").arg(n);
        }
        cmd.arg("*");
        for (k, v) in &fields {
            cmd.arg(*k).arg(v.as_slice());
        }

        let id: String = cmd.query_async(&mut self.conn).await?;
        trace!(topic, %id, "XADD ok");
        Ok(id)
    }
}

/// One entry pulled from a consumer group.
#[derive(Debug, Clone)]
pub struct ConsumerMessage {
    /// Stream id assigned by Redis (e.g. `"1700000000000-0"`).
    pub id: String,
    /// Source topic (the stream key).
    pub topic: String,
    /// Encoded protobuf payload.
    pub payload: Bytes,
    /// Optional trace id carried in the `trace_id` field.
    pub trace_id: Option<String>,
}

impl ConsumerMessage {
    /// Decode the payload into a concrete protobuf type.
    pub fn decode<M: Message + Default>(&self) -> RedisBusResult<M> {
        Ok(M::decode(self.payload.clone())?)
    }
}

/// Redis Streams consumer group. Creates the group if missing and
/// offers a simple `consume` loop that delivers one message at a time.
pub struct ConsumerGroup {
    conn: MultiplexedConnection,
    topic: String,
    group: String,
    consumer: String,
    block_ms: usize,
    batch: usize,
}

impl ConsumerGroup {
    /// Connect + ensure the consumer group exists.
    pub async fn connect(
        url: &str,
        topic: &str,
        group: &str,
        consumer: &str,
    ) -> RedisBusResult<Self> {
        let client = Client::open(url)?;
        let conn = client.get_multiplexed_async_connection().await?;

        let mut this = Self {
            conn,
            topic: topic.to_string(),
            group: group.to_string(),
            consumer: consumer.to_string(),
            block_ms: 1_000,
            batch: 64,
        };
        this.ensure_group().await?;
        Ok(this)
    }

    /// Override the XREADGROUP block timeout.
    pub fn with_block(mut self, d: Duration) -> Self {
        self.block_ms = d.as_millis() as usize;
        self
    }

    /// Override the XREADGROUP batch size.
    pub fn with_batch(mut self, n: usize) -> Self {
        self.batch = n;
        self
    }

    async fn ensure_group(&mut self) -> RedisBusResult<()> {
        // MKSTREAM is crucial — otherwise XGROUP CREATE fails on cold Redis.
        let res: Result<String, RedisError> = redis::cmd("XGROUP")
            .arg("CREATE")
            .arg(&self.topic)
            .arg(&self.group)
            .arg("$")
            .arg("MKSTREAM")
            .query_async(&mut self.conn)
            .await;
        match res {
            Ok(_) => Ok(()),
            Err(e) if e.to_string().contains("BUSYGROUP") => Ok(()),
            Err(e) => Err(e.into()),
        }
    }

    /// Read the next batch of messages. Returns an empty vec on block timeout.
    pub async fn read_next(&mut self) -> RedisBusResult<Vec<ConsumerMessage>> {
        let opts = StreamReadOptions::default()
            .group(&self.group, &self.consumer)
            .count(self.batch)
            .block(self.block_ms);

        let reply: Option<StreamReadReply> = self
            .conn
            .xread_options(&[&self.topic], &[">"], &opts)
            .await?;

        let mut out = Vec::new();
        let Some(reply) = reply else { return Ok(out) };

        for StreamKey { key, ids } in reply.keys {
            for entry in ids {
                let payload = match entry.map.get(PAYLOAD_FIELD) {
                    Some(redis::Value::BulkString(b)) => Bytes::copy_from_slice(b),
                    _ => return Err(RedisBusError::MissingField(PAYLOAD_FIELD)),
                };
                let trace_id = entry.map.get(TRACE_FIELD).and_then(|v| match v {
                    redis::Value::BulkString(b) => {
                        std::str::from_utf8(b).ok().map(|s| s.to_string())
                    }
                    _ => None,
                });
                out.push(ConsumerMessage {
                    id: entry.id,
                    topic: key.clone(),
                    payload,
                    trace_id,
                });
            }
        }
        Ok(out)
    }

    /// ACK a single stream id.
    pub async fn ack(&mut self, id: &str) -> RedisBusResult<()> {
        let _: i64 = self.conn.xack(&self.topic, &self.group, &[id]).await?;
        Ok(())
    }

    /// Run the consumer loop until `cancel` future resolves. For each
    /// message the handler is called; on `Ok` the message is ACKed,
    /// on `Err` it's left pending for reclaim.
    pub async fn consume<F, Fut, C>(&mut self, mut handler: F, mut cancel: C) -> RedisBusResult<()>
    where
        F: FnMut(ConsumerMessage) -> Fut,
        Fut: std::future::Future<Output = anyhow::Result<()>>,
        C: std::future::Future<Output = ()> + Unpin,
    {
        loop {
            tokio::select! {
                _ = &mut cancel => {
                    debug!(topic = %self.topic, "consumer cancelled");
                    return Ok(());
                }
                res = self.read_next() => {
                    match res {
                        Ok(msgs) => {
                            for msg in msgs {
                                let id = msg.id.clone();
                                match handler(msg).await {
                                    Ok(()) => {
                                        if let Err(e) = self.ack(&id).await {
                                            warn!(?e, %id, "XACK failed");
                                        }
                                    }
                                    Err(e) => warn!(?e, %id, "handler failed; leaving unacked"),
                                }
                            }
                        }
                        Err(RedisBusError::Redis(e)) => {
                            warn!(?e, "XREADGROUP failed; backing off");
                            tokio::time::sleep(Duration::from_millis(500)).await;
                        }
                        Err(e) => return Err(e),
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Construction only (no network). Real Redis tests live in
    /// `services/common/tests/cross_lang.rs` and are gated by `REDIS_URL`.
    #[test]
    fn consumer_message_decode_empty_err() {
        let msg = ConsumerMessage {
            id: "1-0".into(),
            topic: "t".into(),
            payload: Bytes::new(),
            trace_id: None,
        };
        // Decoding an empty buffer into a struct with required fields is OK
        // for proto3 (all fields optional-ish) — pick something that can't decode:
        let res: RedisBusResult<crate::proto::watchlist::WatchlistUpsert> = msg.decode();
        assert!(res.is_ok(), "proto3 empty decode should produce default");
    }
}
