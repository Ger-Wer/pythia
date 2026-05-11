//! Topic names for the Dual-Loop bus.
//!
//! **Single source of truth** for Rust. The TypeScript side mirrors this
//! file at `src/services/bus/topics.ts`; a contract test enforces that
//! the two stay in sync.
//!
//! Naming convention: `dl.<wire_version>.<domain>.<event>`.
//! Wire version is bumped only on breaking protobuf changes.

/// Semver-ish version prefix that matches `dual_loop.v1` protobuf package.
pub const WIRE_VERSION: &str = crate::WIRE_VERSION;

// ---------- Watchlist (pythia → sniper-ingest) ----------
pub const WATCHLIST_UPSERT: &str = "dl.v1.watchlist.upsert";
pub const WATCHLIST_REMOVE: &str = "dl.v1.watchlist.remove";

// ---------- Signals (pythia → gateway) ----------
pub const SIGNALS_EMITTED: &str = "dl.v1.signals.emitted";

// ---------- Triggers (sniper-ingest → sniper-executor) ----------
pub const TRIGGERS_FIRED: &str = "dl.v1.triggers.fired";

// ---------- Execution (sniper-executor → all) ----------
pub const EXEC_SUBMITTED: &str = "dl.v1.exec.submitted";
pub const EXEC_FILLED: &str = "dl.v1.exec.filled";
pub const EXEC_REVERTED: &str = "dl.v1.exec.reverted";

// ---------- Control plane (gateway → sniper-executor) ----------
pub const CONTROL_KILL: &str = "dl.v1.control.kill";
pub const CONTROL_KILL_ACK: &str = "dl.v1.control.kill_ack";
pub const CONTROL_HEARTBEAT: &str = "dl.v1.control.heartbeat";

// ---------- Telemetry (sniper-executor → gateway-api, NATS JetStream) ----------
/// Periodic heartbeat from `sniper-executor`; proxied over WSS to Command Center.
pub const EXEC_STATUS: &str = "dl.v1.exec.status";

// ---------- Order book (sniper-orderbook → sniper-risk + downstream) ----------
/// Periodic top-of-book snapshot per asset. Carries best bid/ask, mid,
/// spread, top-N liquidity sums, and a host-monotonic sequence. See
/// `schemas/dual_loop/v1/orderbook.proto::OrderBookUpdate`.
pub const ORDERBOOK_UPDATED: &str = "dl.v1.orderbook.updated";

/// All topics in a canonical order. The contract test on the TS side
/// compares this list byte-for-byte with `topics.ts::ALL_TOPICS`.
pub const ALL_TOPICS: &[&str] = &[
    WATCHLIST_UPSERT,
    WATCHLIST_REMOVE,
    SIGNALS_EMITTED,
    TRIGGERS_FIRED,
    EXEC_SUBMITTED,
    EXEC_FILLED,
    EXEC_REVERTED,
    CONTROL_KILL,
    CONTROL_KILL_ACK,
    CONTROL_HEARTBEAT,
    EXEC_STATUS,
    ORDERBOOK_UPDATED,
];

/// Default consumer group names.
pub mod consumer_groups {
    pub const SNIPER_INGEST: &str = "sniper-ingest";
    pub const SNIPER_EXECUTOR: &str = "sniper-executor";
    pub const SNIPER_RISK: &str = "sniper-risk";
    pub const GATEWAY: &str = "gateway-api";
    pub const PYTHIA_CORE: &str = "pythia-core";
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn topics_have_expected_prefix() {
        for t in ALL_TOPICS {
            assert!(t.starts_with("dl.v1."), "topic {t} missing dl.v1 prefix");
        }
    }

    #[test]
    fn topics_are_unique() {
        let mut seen = std::collections::HashSet::new();
        for t in ALL_TOPICS {
            assert!(seen.insert(*t), "duplicate topic {t}");
        }
    }

    #[test]
    fn wire_version_matches() {
        assert_eq!(WIRE_VERSION, "v1");
    }
}
