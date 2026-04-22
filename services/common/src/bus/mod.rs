//! Bus clients — async wrappers for Redis Streams (control-plane) and
//! NATS JetStream (telemetry).
//!
//! These helpers target Dual-Loop Phase 1. They intentionally do NOT
//! implement partitioning, sharding, or backpressure control — those land
//! in Phase 2/3 once real load profiles are known.
//!
//! Serialization: all control-plane payloads are protobuf-encoded
//! (`prost::Message`). See [`crate::proto`] for the generated types.

pub mod nats;
pub mod redis;

pub use nats::{JetStreamClient, JetStreamError};
pub use redis::{ConsumerGroup, ConsumerMessage, RedisBusError, RedisBusResult, StreamPublisher};

/// Default payload field name inside a Redis Stream entry.
pub const PAYLOAD_FIELD: &str = "payload";

/// Default header field (optional) — carries a trace id / wire version.
pub const TRACE_FIELD: &str = "trace_id";
