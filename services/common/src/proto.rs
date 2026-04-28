//! Generated protobuf types for `dual_loop.v1`.
//!
//! The `build.rs` script invokes `prost-build` against
//! `schemas/dual_loop/v1/*.proto` and drops output into `src/generated/`.
//! Because all six files share package `dual_loop.v1`, prost emits a single
//! file `dual_loop.v1.rs`. We expose logical sub-modules so callers can
//! import e.g. `dual_loop_common::proto::watchlist::WatchlistUpsert`
//! without caring about the underlying file layout.

/// All generated types, flat under `dual_loop.v1`. The file is emitted by
/// `build.rs` into `$OUT_DIR` (not the source tree) so `cargo fmt` can
/// succeed before the build has been run once.
#[allow(clippy::all, dead_code, missing_docs)]
pub mod v1 {
    include!(concat!(env!("OUT_DIR"), "/dual_loop.v1.rs"));
}

/// Message types originally declared in `common.proto`.
pub mod common {
    pub use super::v1::{MarketId, Money, Reason, Side, Timestamp, Uuid};
}

/// Message types originally declared in `watchlist.proto`.
pub mod watchlist {
    pub use super::v1::{TriggerKind, TriggerPolicy, WatchlistRemove, WatchlistUpsert};
}

/// Message types originally declared in `signal.proto`.
pub mod signal {
    pub use super::v1::{DecisionAction, Signal, SignalDecision, SignalPriority};
}

/// Message types originally declared in `trigger.proto`.
pub mod trigger {
    pub use super::v1::Trigger;
}

/// Message types originally declared in `execution.proto`.
pub mod execution {
    pub use super::v1::{ExecFilled, ExecReverted, ExecSubmitted};
}

/// Message types originally declared in `control.proto`.
pub mod control {
    pub use super::v1::{Heartbeat, KillAck, KillAckStatus, KillCommand, KillScope};
}

/// Helper: UUIDv7 wrapped in the generated `Uuid` proto.
pub fn new_uuid_v7() -> common::Uuid {
    let raw = uuid::Uuid::now_v7();
    common::Uuid {
        value: prost::bytes::Bytes::copy_from_slice(raw.as_bytes()),
    }
}

/// Helper: stamp a `Timestamp` from wall-clock + monotonic clocks.
pub fn now_timestamp() -> common::Timestamp {
    let t = crate::time::StampedTime::now();
    common::Timestamp {
        wall_clock_unix_ms: t.wall_clock_unix_ms,
        monotonic_ns: t.monotonic_ns,
    }
}
