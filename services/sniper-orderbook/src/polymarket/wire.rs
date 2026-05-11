//! Wire-format types and converters for the Polymarket CLOB API.
//!
//! Polymarket sends prices and sizes as **decimal strings** (e.g. `"0.48"`,
//! `"30"`). We convert them to integer fixed-point micros (the convention
//! used everywhere in `dual_loop.v1.common.proto`) before they touch the
//! [`OrderBook`] state machine. Anything that can't be parsed into a clean
//! integer (NaN, infinity, more than 6 fractional digits, negative
//! numbers) is rejected as [`WireError::BadPrice`] / [`WireError::BadSize`].
//!
//! ## Polymarket sequence semantics
//!
//! Unlike Binance / Coinbase / Kraken, the Polymarket WS contract does
//! **not** guarantee a strictly-monotonic `+1` sequence per asset. Each
//! message carries only a wall-clock `timestamp` (unix-ms, as a string)
//! and an opaque `hash`. To keep [`OrderBook::apply_update`] happy
//! (it still wants `seq == last + 1`) the host assigns its own monotonic
//! sequence on WS-message receive — see [`SequenceCursor`]. The
//! exchange `timestamp` is preserved on each delta so the
//! snapshot/buffer reconcile can drop stale entries by *exchange time*
//! rather than by host sequence (which is meaningless across a snapshot
//! refetch).
//!
//! ## Endpoints (frozen here for the M2 PR)
//!
//! * REST snapshot: `GET https://clob.polymarket.com/book?token_id=...`
//! * WS feed:        `wss://ws-subscriptions-clob.polymarket.com/ws/market`
//!   subscribed with `{"assets_ids":["<token_id>"],"type":"market"}`.

use crate::book::{BookSide, BookSnapshot, BookUpdate, PriceLevel};
use serde::Deserialize;

/// Errors thrown by the JSON-to-typed conversion layer.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum WireError {
    /// Polymarket sent a price that couldn't be parsed into micros.
    #[error("bad price string {value:?}: {reason}")]
    BadPrice { value: String, reason: &'static str },
    /// Polymarket sent a size that couldn't be parsed into micros.
    #[error("bad size string {value:?}: {reason}")]
    BadSize { value: String, reason: &'static str },
    /// Polymarket sent a timestamp that couldn't be parsed into i64 ms.
    #[error("bad timestamp string {value:?}")]
    BadTimestamp { value: String },
    /// Polymarket sent a side string we don't recognise. The contract
    /// uses `"BUY"` / `"SELL"` (case-insensitive) for `price_change`.
    #[error("unknown side {value:?} (expected BUY or SELL)")]
    UnknownSide { value: String },
}

/// REST `/book` response payload.
#[derive(Debug, Clone, Deserialize)]
pub struct RestBookResponse {
    /// Polymarket market hash (0x...).
    #[serde(default)]
    pub market: Option<String>,
    /// Token / asset ID this book belongs to.
    pub asset_id: String,
    /// Exchange-side timestamp. Polymarket sends a stringified int —
    /// docs are inconsistent about whether it's seconds or ms. The
    /// converter applies a heuristic (see [`parse_polymarket_timestamp`]).
    #[serde(default)]
    pub timestamp: Option<String>,
    /// Opaque book hash, ignored by the converter but kept for logs.
    #[serde(default)]
    pub hash: Option<String>,
    /// Bid levels.
    #[serde(default)]
    pub bids: Vec<RestPriceLevel>,
    /// Ask levels.
    #[serde(default)]
    pub asks: Vec<RestPriceLevel>,
}

/// A single REST book level — both fields are stringified decimals.
#[derive(Debug, Clone, Deserialize)]
pub struct RestPriceLevel {
    pub price: String,
    pub size: String,
}

/// WS `book` event — full book refresh emitted on subscribe and on
/// trades that fully refresh the book.
#[derive(Debug, Clone, Deserialize)]
pub struct WsBookEvent {
    pub asset_id: String,
    #[serde(default)]
    pub market: Option<String>,
    #[serde(default)]
    pub timestamp: Option<String>,
    #[serde(default)]
    pub hash: Option<String>,
    #[serde(default)]
    pub bids: Vec<RestPriceLevel>,
    #[serde(default)]
    pub asks: Vec<RestPriceLevel>,
}

/// WS `price_change` event — one or more level updates.
#[derive(Debug, Clone, Deserialize)]
pub struct WsPriceChangeEvent {
    #[serde(default)]
    pub market: Option<String>,
    #[serde(default)]
    pub timestamp: Option<String>,
    pub price_changes: Vec<WsPriceChange>,
}

/// One level update inside a `price_change` event.
#[derive(Debug, Clone, Deserialize)]
pub struct WsPriceChange {
    pub asset_id: String,
    pub price: String,
    pub size: String,
    pub side: String,
    #[serde(default)]
    pub hash: Option<String>,
}

/// Convert a Polymarket decimal-string price to integer micros.
///
/// Accepts: `"0.48"`, `"1"`, `"0"`, `"0.485000"`. Rejects: `"-0.1"`,
/// `"NaN"`, `"0.1234567"` (7+ fractional digits — silently lossy).
pub fn parse_price_micros(value: &str) -> Result<i64, WireError> {
    parse_decimal_to_micros(value).map_err(|reason| WireError::BadPrice {
        value: value.to_owned(),
        reason,
    })
}

/// Convert a Polymarket decimal-string size to integer micros.
pub fn parse_size_micros(value: &str) -> Result<i64, WireError> {
    parse_decimal_to_micros(value).map_err(|reason| WireError::BadSize {
        value: value.to_owned(),
        reason,
    })
}

/// Parse Polymarket's `"BUY"` / `"SELL"` into our [`BookSide`].
pub fn parse_side(value: &str) -> Result<BookSide, WireError> {
    match value.to_ascii_uppercase().as_str() {
        "BUY" => Ok(BookSide::Bid),
        "SELL" => Ok(BookSide::Ask),
        _ => Err(WireError::UnknownSide {
            value: value.to_owned(),
        }),
    }
}

/// Convert the REST `/book` payload into the canonical
/// [`BookSnapshot`]. The host is responsible for choosing the
/// `sequence` value — it should be the number of WS deltas the host
/// has received so far, so the post-snapshot reconcile step keeps
/// only the truly-newer deltas (see [`Tracker::on_snapshot_received`]).
pub fn rest_book_to_snapshot(
    resp: &RestBookResponse,
    sequence: u64,
) -> Result<BookSnapshot, WireError> {
    let bids = convert_levels(&resp.bids)?;
    let asks = convert_levels(&resp.asks)?;
    let taken_at_unix_ms = match resp.timestamp.as_deref() {
        Some(s) => parse_polymarket_timestamp(s)?,
        None => 0,
    };
    Ok(BookSnapshot {
        sequence,
        bids,
        asks,
        taken_at_unix_ms,
    })
}

/// Convert one WS price_change row into a [`BookUpdate`]. The caller is
/// expected to assign `sequence` (host-monotonic, since Polymarket has
/// no strict seq), and to pass the `received_at_unix_ms` it observed
/// when the WS frame arrived.
pub fn ws_price_change_to_update(
    change: &WsPriceChange,
    sequence: u64,
    received_at_unix_ms: i64,
) -> Result<BookUpdate, WireError> {
    let price_micros = parse_price_micros(&change.price)?;
    let size_micros = parse_size_micros(&change.size)?;
    let side = parse_side(&change.side)?;
    Ok(BookUpdate {
        sequence,
        side,
        level: PriceLevel::new(price_micros, size_micros),
        received_at_unix_ms,
    })
}

/// Convert the WS `book` event into a [`BookSnapshot`]. Used by paths
/// that prefer the WS-delivered snapshot (it ships within milliseconds
/// of `subscribe`) over the REST `/book` round-trip.
pub fn ws_book_to_snapshot(event: &WsBookEvent, sequence: u64) -> Result<BookSnapshot, WireError> {
    let bids = convert_levels(&event.bids)?;
    let asks = convert_levels(&event.asks)?;
    let taken_at_unix_ms = match event.timestamp.as_deref() {
        Some(s) => parse_polymarket_timestamp(s)?,
        None => 0,
    };
    Ok(BookSnapshot {
        sequence,
        bids,
        asks,
        taken_at_unix_ms,
    })
}

fn convert_levels(rows: &[RestPriceLevel]) -> Result<Vec<PriceLevel>, WireError> {
    let mut out = Vec::with_capacity(rows.len());
    for r in rows {
        out.push(PriceLevel::new(
            parse_price_micros(&r.price)?,
            parse_size_micros(&r.size)?,
        ));
    }
    Ok(out)
}

/// Polymarket sends timestamps as strings of digits. Older parts of
/// their docs show seconds (`1234567890`), newer parts show ms
/// (`123456789000`). We accept both: anything `>= 10_000_000_000`
/// (10 billion, past year 2286 in seconds) is treated as already-ms,
/// anything smaller is multiplied by 1000.
pub fn parse_polymarket_timestamp(value: &str) -> Result<i64, WireError> {
    let n: i64 = value.trim().parse().map_err(|_| WireError::BadTimestamp {
        value: value.to_owned(),
    })?;
    if n < 0 {
        return Err(WireError::BadTimestamp {
            value: value.to_owned(),
        });
    }
    if n >= 10_000_000_000 {
        Ok(n)
    } else {
        Ok(n.saturating_mul(1000))
    }
}

/// Decimal string → micros (6 fractional digits = 1 micro).
fn parse_decimal_to_micros(value: &str) -> Result<i64, &'static str> {
    let v = value.trim();
    if v.is_empty() {
        return Err("empty");
    }
    if v.starts_with('-') {
        return Err("negative not allowed");
    }
    if v.eq_ignore_ascii_case("nan")
        || v.eq_ignore_ascii_case("inf")
        || v.eq_ignore_ascii_case("infinity")
    {
        return Err("non-finite");
    }
    let (int_part, frac_part) = match v.split_once('.') {
        Some((i, f)) => (i, f),
        None => (v, ""),
    };
    // Integer part: digits only, no leading +.
    if int_part.is_empty() || !int_part.chars().all(|c| c.is_ascii_digit()) {
        return Err("non-numeric integer part");
    }
    if !frac_part.chars().all(|c| c.is_ascii_digit()) {
        return Err("non-numeric fractional part");
    }
    if frac_part.len() > 6 {
        return Err("more than 6 fractional digits (lossy)");
    }
    let int_val: i64 = int_part.parse().map_err(|_| "integer overflow")?;
    let mut frac_buf = [b'0'; 6];
    for (i, b) in frac_part.bytes().enumerate() {
        frac_buf[i] = b;
    }
    let frac_str = std::str::from_utf8(&frac_buf).map_err(|_| "non-utf8")?;
    let frac_val: i64 = frac_str.parse().map_err(|_| "fractional overflow")?;
    int_val
        .checked_mul(1_000_000)
        .and_then(|m| m.checked_add(frac_val))
        .ok_or("overflow")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_price_basic() {
        assert_eq!(parse_price_micros("0.48").unwrap(), 480_000);
        assert_eq!(parse_price_micros("0.485").unwrap(), 485_000);
        assert_eq!(parse_price_micros("1").unwrap(), 1_000_000);
        assert_eq!(parse_price_micros("0").unwrap(), 0);
        assert_eq!(parse_price_micros("0.999999").unwrap(), 999_999);
    }

    #[test]
    fn parse_price_rejects_garbage() {
        assert!(parse_price_micros("-0.1").is_err());
        assert!(parse_price_micros("NaN").is_err());
        assert!(parse_price_micros("inf").is_err());
        assert!(parse_price_micros("0.1234567").is_err()); // 7 frac digits
        assert!(parse_price_micros("").is_err());
        assert!(parse_price_micros("foo").is_err());
        assert!(parse_price_micros("0.4.8").is_err());
    }

    #[test]
    fn parse_size_basic() {
        assert_eq!(parse_size_micros("30").unwrap(), 30_000_000);
        assert_eq!(parse_size_micros("0.5").unwrap(), 500_000);
        assert_eq!(parse_size_micros("0").unwrap(), 0);
    }

    #[test]
    fn parse_side_buy_sell() {
        assert_eq!(parse_side("BUY").unwrap(), BookSide::Bid);
        assert_eq!(parse_side("buy").unwrap(), BookSide::Bid);
        assert_eq!(parse_side("SELL").unwrap(), BookSide::Ask);
        assert!(parse_side("HOLD").is_err());
    }

    #[test]
    fn polymarket_timestamp_seconds_or_ms() {
        // ms (13+ digits): pass through.
        assert_eq!(
            parse_polymarket_timestamp("1740000000000").unwrap(),
            1_740_000_000_000
        );
        // seconds (10 digits): multiplied.
        assert_eq!(
            parse_polymarket_timestamp("1740000000").unwrap(),
            1_740_000_000_000
        );
        // boundary: 9_999_999_999 → seconds, 10_000_000_000 → ms.
        assert_eq!(
            parse_polymarket_timestamp("9999999999").unwrap(),
            9_999_999_999_000
        );
        assert_eq!(
            parse_polymarket_timestamp("10000000000").unwrap(),
            10_000_000_000
        );
        // garbage.
        assert!(parse_polymarket_timestamp("abc").is_err());
        assert!(parse_polymarket_timestamp("-1").is_err());
    }

    #[test]
    fn rest_book_round_trip() {
        let raw = r#"{
            "market": "0xabc",
            "asset_id": "12345",
            "timestamp": "1740000000",
            "hash": "0xdef",
            "bids": [
                {"price": "0.48", "size": "30"},
                {"price": "0.49", "size": "20"}
            ],
            "asks": [
                {"price": "0.52", "size": "25"}
            ]
        }"#;
        let resp: RestBookResponse = serde_json::from_str(raw).unwrap();
        let snap = rest_book_to_snapshot(&resp, 42).unwrap();
        assert_eq!(snap.sequence, 42);
        assert_eq!(snap.taken_at_unix_ms, 1_740_000_000_000);
        assert_eq!(snap.bids.len(), 2);
        assert_eq!(snap.bids[0].price_micros, 480_000);
        assert_eq!(snap.bids[0].size_micros, 30_000_000);
        assert_eq!(snap.asks[0].price_micros, 520_000);
    }

    #[test]
    fn ws_price_change_round_trip() {
        let raw = r#"{
            "market": "0xabc",
            "timestamp": "1740000001000",
            "price_changes": [
                {"asset_id": "tok", "price": "0.5", "size": "200", "side": "BUY", "hash": "h1"},
                {"asset_id": "tok", "price": "0.55", "size": "0", "side": "SELL", "hash": "h2"}
            ]
        }"#;
        let evt: WsPriceChangeEvent = serde_json::from_str(raw).unwrap();
        let u0 = ws_price_change_to_update(&evt.price_changes[0], 7, 1_740_000_001_500).unwrap();
        assert_eq!(u0.sequence, 7);
        assert_eq!(u0.side, BookSide::Bid);
        assert_eq!(u0.level.price_micros, 500_000);
        assert_eq!(u0.level.size_micros, 200_000_000);
        let u1 = ws_price_change_to_update(&evt.price_changes[1], 8, 1_740_000_001_500).unwrap();
        assert_eq!(u1.side, BookSide::Ask);
        assert!(u1.level.is_zero());
    }

    #[test]
    fn ws_book_round_trip() {
        let raw = r#"{
            "event_type": "book",
            "asset_id": "tok",
            "market": "0xabc",
            "timestamp": "1740000000000",
            "hash": "0xh",
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.51", "size": "20"}]
        }"#;
        let evt: WsBookEvent = serde_json::from_str(raw).unwrap();
        let snap = ws_book_to_snapshot(&evt, 0).unwrap();
        assert_eq!(snap.taken_at_unix_ms, 1_740_000_000_000);
        assert_eq!(snap.bids[0].price_micros, 500_000);
        assert_eq!(snap.asks[0].size_micros, 20_000_000);
    }
}
