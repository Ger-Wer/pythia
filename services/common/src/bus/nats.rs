//! Thin async-nats JetStream wrapper used by Fast Loop services.
//!
//! * `JetStreamClient::publish` — encode a `prost::Message` and push to a
//!   subject. Used for `exec.status`, trigger fan-out, etc.
//! * `JetStreamClient::pull_subscribe` — durable pull-consumer that yields
//!   `JetStreamMessage`s carrying the raw `Bytes` payload. Decoding with
//!   `prost::Message::decode(Bytes)` is zero-copy: the `Bytes` buffer is
//!   shared (refcounted) with the generated struct's owned fields, and
//!   `String` / `Vec<u8>` fields alias slices of that buffer.
//!
//! Keep this module small — anything protocol-specific (topic consts,
//! message type dispatch) belongs in callers or in `topics.rs`.

use std::time::Duration;

use async_nats::jetstream::{self, consumer::pull, context::GetStreamErrorKind};
use bytes::Bytes;
use futures::StreamExt;
use prost::Message;
use thiserror::Error;

/// Errors from the NATS bus layer.
#[derive(Debug, Error)]
pub enum JetStreamError {
    #[error("connect error: {0}")]
    Connect(#[from] async_nats::ConnectError),
    #[error("publish error: {0}")]
    Publish(#[from] async_nats::jetstream::context::PublishError),
    #[error("context error: {0}")]
    Context(#[from] async_nats::jetstream::context::RequestError),
    #[error("stream create error: {0}")]
    CreateStream(#[from] async_nats::jetstream::context::CreateStreamError),
    #[error("consumer create error: {0}")]
    CreateConsumer(#[from] async_nats::jetstream::stream::ConsumerError),
    #[error("consumer stream error: {0}")]
    ConsumerStream(#[from] async_nats::jetstream::consumer::StreamError),
    #[error("messages error: {0}")]
    Messages(#[from] async_nats::jetstream::consumer::pull::MessagesError),
    #[error("get stream error: {0}")]
    GetStream(#[from] async_nats::error::Error<GetStreamErrorKind>),
    #[error("protobuf encode error: {0}")]
    Encode(#[from] prost::EncodeError),
    #[error("protobuf decode error: {0}")]
    Decode(#[from] prost::DecodeError),
}

/// JetStream client. Cheap to clone.
#[derive(Clone)]
pub struct JetStreamClient {
    ctx: jetstream::Context,
}

impl JetStreamClient {
    /// Connect and open a JetStream context.
    pub async fn connect(url: &str) -> Result<Self, JetStreamError> {
        let client = async_nats::connect(url).await?;
        let ctx = jetstream::new(client);
        Ok(Self { ctx })
    }

    /// Protobuf-encode and publish.
    pub async fn publish<M: Message>(
        &self,
        subject: &str,
        message: &M,
    ) -> Result<(), JetStreamError> {
        let mut buf = Vec::with_capacity(message.encoded_len());
        message.encode(&mut buf)?;
        let ack = self.ctx.publish(subject.to_string(), buf.into()).await?;
        // Fire-and-forget: JetStream keeps the message durable regardless of
        // whether the caller awaits the ack future.
        drop(ack);
        Ok(())
    }

    /// Ensure a JetStream stream exists covering `subjects`. Idempotent.
    pub async fn ensure_stream(
        &self,
        name: &str,
        subjects: Vec<String>,
    ) -> Result<(), JetStreamError> {
        self.ctx
            .get_or_create_stream(jetstream::stream::Config {
                name: name.to_string(),
                subjects,
                ..Default::default()
            })
            .await?;
        Ok(())
    }

    /// Open a durable pull-consumer on `stream_name` filtered by `subject`.
    /// Subsequent `JetStreamConsumer::next().await` calls yield messages
    /// with zero-copy `Bytes` payloads.
    pub async fn pull_subscribe(
        &self,
        stream_name: &str,
        subject: &str,
        durable: &str,
    ) -> Result<JetStreamConsumer, JetStreamError> {
        let stream = self.ctx.get_stream(stream_name).await?;
        let consumer = stream
            .get_or_create_consumer(
                durable,
                pull::Config {
                    durable_name: Some(durable.to_string()),
                    filter_subject: subject.to_string(),
                    ack_wait: Duration::from_secs(30),
                    ..Default::default()
                },
            )
            .await?;
        Ok(JetStreamConsumer { consumer })
    }
}

/// Durable pull-consumer over a single subject.
pub struct JetStreamConsumer {
    consumer: async_nats::jetstream::consumer::Consumer<pull::Config>,
}

impl JetStreamConsumer {
    /// Fetch the next message (blocks indefinitely until one arrives or the
    /// connection is closed). Returns `None` when the stream ends.
    pub async fn next(&mut self) -> Result<Option<JetStreamMessage>, JetStreamError> {
        let mut messages = self.consumer.messages().await?;
        match messages.next().await {
            Some(Ok(msg)) => Ok(Some(JetStreamMessage { inner: msg })),
            Some(Err(e)) => Err(JetStreamError::Messages(e)),
            None => Ok(None),
        }
    }
}

/// A JetStream message with zero-copy access to its payload.
pub struct JetStreamMessage {
    inner: async_nats::jetstream::Message,
}

impl JetStreamMessage {
    /// Raw payload (refcounted — cheap to clone).
    #[inline]
    pub fn payload(&self) -> Bytes {
        self.inner.payload.clone()
    }

    /// Zero-copy protobuf decode. The returned message's `String` / `Vec<u8>`
    /// fields alias slices of the original payload's `Bytes`.
    #[inline]
    pub fn decode<M: Message + Default>(&self) -> Result<M, prost::DecodeError> {
        M::decode(self.payload())
    }

    /// Acknowledge the message so JetStream stops redelivering it.
    pub async fn ack(self) -> Result<(), async_nats::Error> {
        self.inner.ack().await
    }

    /// Negative-ack (unblocks redelivery without waiting for ack_wait).
    pub async fn nak(self) -> Result<(), async_nats::Error> {
        self.inner
            .ack_with(async_nats::jetstream::AckKind::Nak(None))
            .await
    }
}
