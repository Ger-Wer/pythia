//! Polymarket CLOB REST client for snapshot fetch.
//!
//! Single endpoint: `GET https://clob.polymarket.com/book?token_id=...`.
//! Returns the canonical [`BookSnapshot`] (integer micros, host-assigned
//! sequence). Network failures, non-2xx responses, and JSON-parse
//! failures all collapse into [`RestError::Transport`] / `Json` — the
//! [`Tracker`] reacts by [`on_snapshot_failed`](crate::Tracker::on_snapshot_failed),
//! which transitions to `Degraded { SnapshotFailed }` so the host can
//! back off and retry on its own cadence.
//!
//! No retries are baked into this layer — the host owns the retry
//! policy because backoff interacts with the WS reconnect ladder.

use std::time::Duration;

use crate::book::BookSnapshot;
use crate::polymarket::wire::{rest_book_to_snapshot, RestBookResponse, WireError};
use reqwest::Client;
use thiserror::Error;

/// Default Polymarket CLOB REST host. Pinned to the production
/// endpoint; staging is `clob-staging.polymarket.com`.
pub const DEFAULT_REST_BASE_URL: &str = "https://clob.polymarket.com";

/// Errors returned by [`RestClient::fetch_snapshot`].
#[derive(Debug, Error)]
pub enum RestError {
    #[error("transport error: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("non-2xx response: {status} body={body}")]
    NonSuccess { status: u16, body: String },
    #[error("wire/json error: {0}")]
    Wire(#[from] WireError),
    #[error("invalid url: {0}")]
    Url(String),
}

/// Thin wrapper around `reqwest::Client` so callers can inject a shared
/// connection pool and a custom timeout / user-agent.
#[derive(Debug, Clone)]
pub struct RestClient {
    inner: Client,
    base_url: String,
}

impl RestClient {
    /// Build a client with sensible Polymarket defaults: 10s overall
    /// timeout, 5s connect timeout, no keep-alive (the snapshot fetch
    /// is one-shot per market).
    pub fn new() -> Result<Self, RestError> {
        Self::with_base_url(DEFAULT_REST_BASE_URL)
    }

    /// Build with a custom base URL (e.g. `clob-staging.polymarket.com`
    /// or a local mock for integration tests).
    pub fn with_base_url(base_url: &str) -> Result<Self, RestError> {
        let inner = Client::builder()
            .timeout(Duration::from_secs(10))
            .connect_timeout(Duration::from_secs(5))
            .user_agent(concat!(
                "pythia-sniper-orderbook/",
                env!("CARGO_PKG_VERSION")
            ))
            .build()
            .map_err(RestError::from)?;
        Ok(Self {
            inner,
            base_url: base_url.trim_end_matches('/').to_owned(),
        })
    }

    /// `GET {base_url}/book?token_id=<token_id>` → [`BookSnapshot`].
    ///
    /// `sequence` is the host's local monotonic counter at the moment
    /// the request was issued; see
    /// [`Tracker::on_snapshot_received`](crate::Tracker::on_snapshot_received)
    /// for why the host owns this rather than the wire.
    pub async fn fetch_snapshot(
        &self,
        token_id: &str,
        sequence: u64,
    ) -> Result<BookSnapshot, RestError> {
        if token_id.is_empty() {
            return Err(RestError::Url("token_id is empty".to_owned()));
        }
        let url = format!("{}/book?token_id={}", self.base_url, token_id);
        let resp = self.inner.get(&url).send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(RestError::NonSuccess {
                status: status.as_u16(),
                body: truncate(&body, 512),
            });
        }
        let parsed: RestBookResponse = resp.json().await?;
        let snap = rest_book_to_snapshot(&parsed, sequence)?;
        Ok(snap)
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_owned()
    } else {
        format!("{}…", &s[..max])
    }
}
