//! Prometheus metrics for sniper-ingest.
//!
//! Metrics are registered lazily via the `metrics` crate macros.
//! This module provides helper functions to initialize descriptors
//! for better Prometheus metadata.

use metrics::{describe_counter, describe_gauge, describe_histogram};

/// Register all metric descriptions. Call once at startup.
pub fn register_metrics() {
    describe_counter!(
        "mempool_tx_seen_total",
        "Total pending transactions observed from mempool"
    );
    describe_counter!(
        "mempool_decoded_total",
        "Total successfully decoded events by contract"
    );
    describe_counter!(
        "mempool_drop_total",
        "Transactions dropped due to channel overflow"
    );
    describe_counter!(
        "trigger_fired_total",
        "Triggers emitted to the bus by market and kind"
    );
    describe_histogram!(
        "trigger_matcher_latency_ns",
        "Latency from tx observation to trigger emission (nanoseconds)"
    );
    describe_gauge!(
        "watchlist_size",
        "Current number of entries in the watchlist"
    );
}
