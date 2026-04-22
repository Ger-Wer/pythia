//! Thin async-nats JetStream wrapper (publish-side only for MVP).
//!
//! Telemetry topics (`telem.*`) will layer on top of this once Fast Loop
//! actually taps mempool / order books. Phase 1 keeps it minimal: the
//! bus layer is ready, but no service publishes to NATS yet.

use async_nats::jetstream;
use prost::Message;
use thiserror::Error;

/// Errors from the NATS bus layer.
#[derive(Debug, Error)]
pub enum JetStreamError {
    #[error("connect error: {0}")]
    Connect(#[from] async_nats::ConnectError),
    #[error("publish error: {0}")]
    Publish(#[from] async_nats::jetstream::context::PublishError),
    #[error("protobuf encode error: {0}")]
    Encode(#[from] prost::EncodeError),
}

/// Publish-only JetStream client. Cheap to clone.
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
        // We don't await the ack here — callers can if they need it.
        // Drop the future; JetStream keeps the message durable regardless.
        drop(ack);
        Ok(())
    }
}
