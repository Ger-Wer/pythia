//! Generated protobuf types for `dual_loop.v1`.
//!
//! `build.rs` invokes `prost-build` against `schemas/dual_loop/v1/*.proto`
//! and emits output to `$OUT_DIR`. All six .proto files share the
//! `dual_loop.v1` package so prost collapses them into one `dual_loop.v1.rs`.
//! Logical sub-modules below let callers import by source-file grouping,
//! e.g. `dual_loop_common::proto::watchlist::WatchlistUpsert`.
//!
//! Wire convention (enforced by schema — see `common.proto`):
//!
//!   * Wall-clock времена — `int64 *_unix_ms` (no more `Timestamp` message).
//!   * Monotonic per-host — `uint64 *_ns`, только для hot-path latency.
//!   * Вероятности — `int64 *_ppm` (1_000_000 = 100%).
//!   * Знаковые probability/EV — `sint64` (ZigZag).
//!   * Деньги и цены — `Money.amount_micros` (1_000_000 = $1.00).

/// All generated types, flat under `dual_loop.v1`. The file is emitted by
/// `build.rs` into `$OUT_DIR` (not the source tree) so `cargo fmt` can
/// succeed before the build has been run once.
#[allow(clippy::all, dead_code, missing_docs)]
pub mod v1 {
    include!(concat!(env!("OUT_DIR"), "/dual_loop.v1.rs"));
}

/// Message types originally declared in `common.proto`.
pub mod common {
    pub use super::v1::{FixedPointScales, MarketId, Money, Reason, Side, Uuid};
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
    pub use super::v1::{Trigger, TriggerBlocked};
}

/// Message types originally declared in `execution.proto`.
pub mod execution {
    pub use super::v1::{ExecFilled, ExecReverted, ExecStatus, ExecSubmitted, ExecutorState};
}

/// Message types originally declared in `control.proto`.
pub mod control {
    pub use super::v1::{KillAck, KillAckStatus, KillCommand, KillScope};
}

// ---------- Fixed-point conversion helpers ----------

/// 1.0 probability = 1_000_000 ppm. See [`common::FixedPointScales`].
pub const PROBABILITY_PPM_PER_UNIT: i64 = 1_000_000;
/// 1.0 = 10_000 bps (1 bps = 0.01%).
pub const BPS_PER_UNIT: i64 = 10_000;
/// 1.0 USD = 1_000_000 micros.
pub const USD_MICROS_PER_UNIT: i64 = 1_000_000;

/// Convert a unit-interval probability (0..1) to signed `_ppm` with clamping
/// to `±PROBABILITY_PPM_PER_UNIT`. Used to cross the float→int boundary at
/// the application edge (NOT on the wire — the wire is pure integer).
#[inline]
pub fn prob_to_ppm(p: f64) -> i64 {
    let scaled = (p * PROBABILITY_PPM_PER_UNIT as f64).round() as i64;
    scaled.clamp(-PROBABILITY_PPM_PER_UNIT, PROBABILITY_PPM_PER_UNIT)
}

/// Inverse of [`prob_to_ppm`]; always safe (wire already bounded).
#[inline]
pub fn ppm_to_prob(ppm: i64) -> f64 {
    ppm as f64 / PROBABILITY_PPM_PER_UNIT as f64
}

/// Convert a fractional return (e.g. 0.004 = 40 bps) to signed `_bps`.
#[inline]
pub fn ret_to_bps(ret: f64) -> i64 {
    (ret * BPS_PER_UNIT as f64).round() as i64
}

/// Inverse of [`ret_to_bps`].
#[inline]
pub fn bps_to_ret(bps: i64) -> f64 {
    bps as f64 / BPS_PER_UNIT as f64
}

/// Convert USD (float) to `Money.amount_micros`.
#[inline]
pub fn usd_to_micros(usd: f64) -> i64 {
    (usd * USD_MICROS_PER_UNIT as f64).round() as i64
}

/// Helper: UUIDv7 wrapped in the generated `Uuid` proto.
pub fn new_uuid_v7() -> common::Uuid {
    let raw = uuid::Uuid::now_v7();
    common::Uuid {
        value: prost::bytes::Bytes::copy_from_slice(raw.as_bytes()),
    }
}

/// Current wall-clock as Unix epoch ms. Replaces the old `now_timestamp()`.
#[inline]
pub fn now_unix_ms() -> i64 {
    crate::time::StampedTime::now().wall_clock_unix_ms
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prob_roundtrip_within_tolerance() {
        for p in [0.0_f64, 0.1, 0.5, 0.95, 1.0] {
            let ppm = prob_to_ppm(p);
            let back = ppm_to_prob(ppm);
            assert!((back - p).abs() < 1e-6, "p={p}, ppm={ppm}, back={back}");
        }
    }

    #[test]
    fn prob_clamps_out_of_range() {
        assert_eq!(prob_to_ppm(2.0), PROBABILITY_PPM_PER_UNIT);
        assert_eq!(prob_to_ppm(-2.0), -PROBABILITY_PPM_PER_UNIT);
    }

    #[test]
    fn ret_bps_roundtrip() {
        let bps = ret_to_bps(0.0123);
        assert_eq!(bps, 123);
        assert!((bps_to_ret(bps) - 0.0123).abs() < 1e-9);
    }

    #[test]
    fn usd_micros_conversion() {
        assert_eq!(usd_to_micros(1.0), 1_000_000);
        assert_eq!(usd_to_micros(0.005), 5_000);
    }
}
