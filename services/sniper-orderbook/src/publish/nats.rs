//! Concrete NATS / JetStream publisher for `OrderBookSnapshotView`.
//!
//! Wraps [`dual_loop_common::bus::nats::JetStreamClient`] (which already
//! handles prost encoding under the hood) and maps the in-crate
//! [`OrderBookSnapshotView`] to the wire-level
//! `dual_loop.v1.OrderBookUpdate` proto on every publish.
//!
//! Host responsibilities (NOT done by this publisher):
//! * `ensure_stream` for the subject before the first publish.
//! * Rate-limiting / debouncing — the publisher writes whatever the
//!   caller passes. The [`crate::Tracker::publish_if_bbo_changed`]
//!   helper provides BBO-diff filtering for the hot path.
//! * Reconnect / retry — `JetStreamClient` does not retry; the host
//!   must restart it on hard failures.

use dual_loop_common::bus::nats::JetStreamClient;
use dual_loop_common::proto::common::MarketId;
use dual_loop_common::proto::new_uuid_v7;
use dual_loop_common::proto::orderbook::OrderBookUpdate;

use crate::publish::{OrderBookPublisher, OrderBookSnapshotView, PublishError, TOPIC};

/// Production NATS / JetStream publisher.
///
/// The caller wires in a connected [`JetStreamClient`] and optionally
/// the static `polymarket_id` / `ctf_address` portions of the canonical
/// [`MarketId`]. The dynamic `market_specific_key` is taken from the
/// view on every publish.
pub struct NatsPublisher {
    client: JetStreamClient,
    polymarket_id: String,
    ctf_address: String,
}

impl NatsPublisher {
    /// Construct a publisher wrapping an already-connected client.
    /// `polymarket_id` and `ctf_address` default to empty strings;
    /// override via [`Self::with_market_identity`] when known.
    pub fn new(client: JetStreamClient) -> Self {
        Self {
            client,
            polymarket_id: String::new(),
            ctf_address: String::new(),
        }
    }

    /// Set the static `(polymarket_id, ctf_address)` pair stamped onto
    /// every published `MarketId`. The dynamic `market_specific_key`
    /// still comes from each view, so a single publisher can serve
    /// multiple assets within one logical market.
    #[must_use]
    pub fn with_market_identity(
        mut self,
        polymarket_id: impl Into<String>,
        ctf_address: impl Into<String>,
    ) -> Self {
        self.polymarket_id = polymarket_id.into();
        self.ctf_address = ctf_address.into();
        self
    }

    fn view_to_proto(&self, view: &OrderBookSnapshotView) -> OrderBookUpdate {
        OrderBookUpdate {
            message_id: Some(new_uuid_v7()),
            market: Some(MarketId {
                polymarket_id: self.polymarket_id.clone(),
                ctf_address: self.ctf_address.clone(),
                market_specific_key: view.market_specific_key.clone(),
            }),
            asset_id: view.asset_id.clone(),
            best_bid_micros: view.best_bid_micros,
            best_ask_micros: view.best_ask_micros,
            mid_micros: view.mid_micros,
            spread_bps: view.spread_bps,
            liquidity_bid_micros: view.liquidity_bid_micros,
            liquidity_ask_micros: view.liquidity_ask_micros,
            liquidity_depth: view.liquidity_depth,
            crossed: view.crossed,
            timestamp_ms: view.timestamp_ms,
            last_sequence: view.last_sequence,
        }
    }
}

impl OrderBookPublisher for NatsPublisher {
    async fn publish(&self, view: &OrderBookSnapshotView) -> Result<(), PublishError> {
        let msg = self.view_to_proto(view);
        self.client
            .publish(TOPIC, &msg)
            .await
            .map_err(|e| PublishError::Transport(e.to_string()))
    }
}
