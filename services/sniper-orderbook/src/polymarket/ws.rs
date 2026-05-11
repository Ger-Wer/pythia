//! Polymarket CLOB WebSocket subscriber.
//!
//! Endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
//! Subscribe immediately after connect with
//! `{"assets_ids":["<token_id>"], "type":"market"}`. Polymarket replies
//! with one `book` event (full snapshot) followed by a continuous
//! stream of `price_change` events (incremental updates).
//!
//! This module exposes a typed stream of [`WsEvent`] values. The
//! caller owns reconnection — a connection drop simply ends the
//! stream with `Ok(None)` (graceful close) or `Err(...)` (transport
//! failure); the [`Tracker`] reacts via
//! [`on_ws_disconnected`](crate::Tracker::on_ws_disconnected) and
//! the host decides when to reconnect.

use std::time::Duration;

use crate::polymarket::wire::{WsBookEvent, WsPriceChangeEvent};
use futures::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::net::TcpStream;
use tokio_tungstenite::{connect_async, tungstenite, MaybeTlsStream, WebSocketStream};

/// Default production WS endpoint.
pub const DEFAULT_WS_URL: &str = "wss://ws-subscriptions-clob.polymarket.com/ws/market";

/// Errors returned by [`WsSubscriber`].
///
/// `tungstenite::Error` is large (~136 B), so we box it to keep the
/// `Result<…, WsError>` size reasonable on the hot path (clippy:
/// `result_large_err`).
#[derive(Debug, Error)]
pub enum WsError {
    #[error("transport: {0}")]
    Transport(#[from] Box<tungstenite::Error>),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
}

impl From<tungstenite::Error> for WsError {
    fn from(e: tungstenite::Error) -> Self {
        WsError::Transport(Box::new(e))
    }
}

/// Parsed WS event, post-JSON. The caller decides what to do with each
/// variant — typically [`WsEvent::Book`] is mapped to a
/// [`BookSnapshot`] via [`wire::ws_book_to_snapshot`](crate::polymarket::wire::ws_book_to_snapshot)
/// (or just used to verify the host's REST snapshot is fresh), and
/// each [`WsEvent::PriceChange`] is turned into one or more
/// [`BookUpdate`]s.
#[derive(Debug, Clone)]
pub enum WsEvent {
    /// Full-book snapshot, emitted on subscribe + on book-affecting
    /// trades. Always carries the canonical bids/asks.
    Book(WsBookEvent),
    /// Incremental update with one or more level changes.
    PriceChange(WsPriceChangeEvent),
    /// Polymarket also emits `last_trade_price`, `tick_size_change`,
    /// `best_bid_ask`, `market_resolved` — we surface them as raw JSON
    /// for now; the M2 tracker only needs book + price_change.
    Other {
        event_type: String,
        raw: serde_json::Value,
    },
}

/// Wraps a connected WS stream and yields typed events.
pub struct WsSubscriber {
    ws: WebSocketStream<MaybeTlsStream<TcpStream>>,
}

#[derive(Debug, Serialize)]
struct Subscription<'a> {
    assets_ids: &'a [&'a str],
    #[serde(rename = "type")]
    kind: &'a str,
}

impl WsSubscriber {
    /// Connect to the default Polymarket WS endpoint and subscribe to
    /// `assets_ids`. Returns once the WS handshake has completed and
    /// the subscription frame has been written.
    pub async fn connect(assets_ids: &[&str]) -> Result<Self, WsError> {
        Self::connect_to(DEFAULT_WS_URL, assets_ids).await
    }

    /// Connect to a custom WS URL (e.g. staging or a local mock).
    pub async fn connect_to(url: &str, assets_ids: &[&str]) -> Result<Self, WsError> {
        let (ws, _resp) = tokio::time::timeout(Duration::from_secs(10), connect_async(url))
            .await
            .map_err(|_| {
                WsError::Io(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "ws connect timed out after 10s",
                ))
            })??;
        let mut sub = Self { ws };
        sub.send_subscription(assets_ids).await?;
        Ok(sub)
    }

    async fn send_subscription(&mut self, assets_ids: &[&str]) -> Result<(), WsError> {
        let body = serde_json::to_string(&Subscription {
            assets_ids,
            kind: "market",
        })?;
        self.ws.send(tungstenite::Message::Text(body)).await?;
        Ok(())
    }

    /// Read the next event. Returns `Ok(None)` if the stream ended
    /// gracefully (server closed the connection), `Err(_)` on any
    /// transport / parse failure.
    pub async fn next_event(&mut self) -> Result<Option<WsEvent>, WsError> {
        while let Some(frame) = self.ws.next().await {
            let msg = frame?;
            match msg {
                tungstenite::Message::Text(text) => {
                    return Ok(Some(parse_event(text.as_ref())?));
                }
                tungstenite::Message::Binary(b) => {
                    // Polymarket usually sends text, but be tolerant.
                    let text = std::str::from_utf8(&b).map_err(|e| {
                        WsError::Io(std::io::Error::new(std::io::ErrorKind::InvalidData, e))
                    })?;
                    return Ok(Some(parse_event(text)?));
                }
                tungstenite::Message::Ping(payload) => {
                    self.ws.send(tungstenite::Message::Pong(payload)).await?;
                }
                tungstenite::Message::Pong(_) => {}
                tungstenite::Message::Close(_) => return Ok(None),
                tungstenite::Message::Frame(_) => {}
            }
        }
        Ok(None)
    }

    /// Best-effort close. Errors are swallowed (host is about to drop us).
    pub async fn close(mut self) {
        let _ = self.ws.close(None).await;
    }
}

/// Parse a single WS text frame into a [`WsEvent`].
pub fn parse_event(text: &str) -> Result<WsEvent, WsError> {
    let value: serde_json::Value = serde_json::from_str(text)?;
    // Polymarket sometimes batches multiple events into a JSON array.
    // We surface the first one and rely on the underlying stream to
    // deliver the rest in subsequent frames; in practice the framing
    // is one event per text frame.
    let single = if let Some(arr) = value.as_array() {
        arr.first().cloned().unwrap_or(serde_json::Value::Null)
    } else {
        value
    };
    let event_type = single
        .get("event_type")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_owned();
    match event_type.as_str() {
        "book" => {
            let evt: WsBookEvent = serde_json::from_value(single)?;
            Ok(WsEvent::Book(evt))
        }
        "price_change" => {
            let evt: WsPriceChangeEvent = serde_json::from_value(single)?;
            Ok(WsEvent::PriceChange(evt))
        }
        _ => Ok(WsEvent::Other {
            event_type,
            raw: single,
        }),
    }
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
struct EventEnvelope {
    event_type: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_book_event() {
        let raw = r#"{
            "event_type": "book",
            "asset_id": "tok",
            "market": "0xabc",
            "timestamp": "1740000000000",
            "hash": "0xh",
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.51", "size": "20"}]
        }"#;
        match parse_event(raw).unwrap() {
            WsEvent::Book(b) => {
                assert_eq!(b.asset_id, "tok");
                assert_eq!(b.bids.len(), 1);
            }
            other => panic!("expected book, got {other:?}"),
        }
    }

    #[test]
    fn parse_price_change_event() {
        let raw = r#"{
            "event_type": "price_change",
            "market": "0xabc",
            "timestamp": "1740000001000",
            "price_changes": [
                {"asset_id": "tok", "price": "0.5", "size": "200", "side": "BUY", "hash": "h1"}
            ]
        }"#;
        match parse_event(raw).unwrap() {
            WsEvent::PriceChange(p) => {
                assert_eq!(p.price_changes.len(), 1);
                assert_eq!(p.price_changes[0].side, "BUY");
            }
            other => panic!("expected price_change, got {other:?}"),
        }
    }

    #[test]
    fn parse_unknown_event_falls_through() {
        let raw = r#"{"event_type": "tick_size_change", "asset_id": "tok"}"#;
        match parse_event(raw).unwrap() {
            WsEvent::Other { event_type, .. } => assert_eq!(event_type, "tick_size_change"),
            other => panic!("expected other, got {other:?}"),
        }
    }

    #[test]
    fn parse_batched_array_takes_first() {
        let raw = r#"[
            {"event_type":"book","asset_id":"tok","bids":[],"asks":[],"timestamp":"1"},
            {"event_type":"price_change","price_changes":[]}
        ]"#;
        match parse_event(raw).unwrap() {
            WsEvent::Book(b) => assert_eq!(b.asset_id, "tok"),
            other => panic!("expected book, got {other:?}"),
        }
    }
}
