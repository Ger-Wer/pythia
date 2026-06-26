//! Polymarket client wrappers for TrackerTask.
//!
//! Thin adapters around M2's RestClient and WsSubscriber, presenting
//! a simplified interface suitable for TrackerTask's lifecycle.

use crate::book::{BookSnapshot, BookUpdate};
use crate::polymarket::{
    ws_price_change_to_update, RestClient, SequenceCursor, WsEvent, WsSubscriber,
};

/// Polymarket REST client wrapper for snapshot fetching.
pub struct PolymarketRestClient {
    token_id: String,
    inner: RestClient,
}

impl PolymarketRestClient {
    pub fn new(token_id: String) -> Result<Self, String> {
        let inner = RestClient::new().map_err(|e| format!("RestClient init failed: {}", e))?;
        Ok(Self { token_id, inner })
    }

    /// Fetch snapshot from Polymarket REST API.
    /// `sequence` is the host's monotonic cursor at request time.
    pub async fn fetch_snapshot(&self, sequence: u64) -> Result<BookSnapshot, String> {
        self.inner
            .fetch_snapshot(&self.token_id, sequence)
            .await
            .map_err(|e| format!("Snapshot fetch failed: {}", e))
    }
}

/// Polymarket WebSocket subscriber wrapper.
pub struct PolymarketWsSubscriber {
    inner: WsSubscriber,
    seq_cursor: SequenceCursor,
}

impl PolymarketWsSubscriber {
    /// Connect to Polymarket WS and subscribe to the given token.
    pub async fn connect(token_id: String) -> Result<Self, String> {
        let inner = WsSubscriber::connect(&[&token_id])
            .await
            .map_err(|e| format!("WS connect failed: {}", e))?;
        Ok(Self {
            inner,
            seq_cursor: SequenceCursor::new(),
        })
    }

    /// Receive the next WS message and convert to BookUpdate.
    ///
    /// Returns `Ok(None)` on graceful close, `Err(_)` on transport failure.
    ///
    /// For MVP: Only handles `PriceChange` events (incremental deltas).
    /// `Book` events (WS snapshot) are currently ignored — we rely on
    /// REST snapshot for initial state.
    pub async fn next_update(&mut self) -> Result<Option<BookUpdate>, String> {
        loop {
            match self
                .inner
                .next_event()
                .await
                .map_err(|e| format!("WS read failed: {}", e))?
            {
                None => return Ok(None), // graceful close
                Some(WsEvent::PriceChange(evt)) => {
                    // Convert first price change to BookUpdate.
                    // M2 wire format can have multiple changes per event;
                    // for MVP we only process the first one.
                    if let Some(pc) = evt.price_changes.first() {
                        let seq = self.seq_cursor.take_next();
                        // Use current system time for received_at timestamp
                        let received_at_unix_ms = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .map(|d| d.as_millis() as i64)
                            .unwrap_or(0);
                        let update = ws_price_change_to_update(pc, seq, received_at_unix_ms)
                            .map_err(|e| format!("Wire parse failed: {}", e))?;
                        return Ok(Some(update));
                    }
                    // No price changes in event, skip to next frame
                }
                Some(WsEvent::Book(_)) => {
                    // WS snapshot event — skip for MVP.
                    // TODO: Could validate against REST snapshot or use for recovery.
                }
                Some(WsEvent::Other { .. }) => {
                    // Ignore unknown event types (last_trade_price, tick_size_change, etc.)
                }
            }
        }
    }

    /// Close the WebSocket connection gracefully.
    pub async fn close(self) {
        self.inner.close().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn polymarket_rest_client_new() {
        let client = PolymarketRestClient::new("test-token".to_string());
        assert!(client.is_ok());
    }

    #[test]
    fn sequence_cursor_increments() {
        let mut cursor = SequenceCursor::new();
        assert_eq!(cursor.take_next(), 1);
        assert_eq!(cursor.take_next(), 2);
        assert_eq!(cursor.take_next(), 3);
    }

    // Real Polymarket integration tests marked #[ignore] — require live API.
    #[tokio::test]
    #[ignore = "requires live Polymarket API"]
    async fn fetch_snapshot_integration() {
        let client = PolymarketRestClient::new("test-token".to_string()).unwrap();
        // This will fail against live API without a valid token_id
        let result = client.fetch_snapshot(0).await;
        // Just verify the error path doesn't panic
        assert!(result.is_err());
    }

    #[tokio::test]
    #[ignore = "requires live Polymarket WebSocket"]
    async fn ws_connect_integration() {
        // This will fail against live API without a valid token_id
        let result = PolymarketWsSubscriber::connect("test-token".to_string()).await;
        // Just verify the error path doesn't panic
        assert!(result.is_err());
    }
}
