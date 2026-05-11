//! `orderbook_probe` — drive a live Polymarket order-book Tracker for one
//! market and print top-3 levels + spread on every applied delta.
//!
//! Usage:
//!
//! ```text
//! cargo run --release -p orderbook_probe -- \
//!     --token-id <TOKEN_ID> \
//!     --duration-secs 30 \
//!     --print-every 10
//! ```
//!
//! The probe enforces the M2 sync protocol exactly as documented in
//! `services/sniper-orderbook/src/lib.rs`:
//!
//! 1. Open the WS subscription **first**, start buffering deltas.
//! 2. Then issue the REST `/book` snapshot request.
//! 3. Feed the snapshot into the Tracker; it drops stale buffered
//!    deltas and replays the rest.
//! 4. Loop: pull WS events → feed Tracker → print top-of-book.
//!
//! Crossed books are flagged as `WARN` by the Tracker itself (via
//! `metrics::counter!("sniper_orderbook_crossed_observed_total")` and
//! a `tracing::warn!`). The probe additionally counts them locally so
//! the end-of-run summary lines up with the metric.

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use clap::Parser;
use sniper_orderbook::{
    polymarket::{
        rest::RestClient, wire::ws_price_change_to_update, ws::WsEvent, ws::WsSubscriber,
        SequenceCursor,
    },
    OrderBook, PriceLevel, Tracker, TrackerConfig, TrackerState,
};

#[derive(Parser, Debug)]
#[command(
    name = "orderbook_probe",
    about = "Drive a live Polymarket order-book Tracker and print top-3 + spread."
)]
struct Cli {
    /// Polymarket token (asset) ID. Discover via the Gamma API.
    #[arg(long)]
    token_id: String,

    /// Total run duration in seconds before printing the summary.
    #[arg(long, default_value_t = 30)]
    duration_secs: u64,

    /// Print top-3 levels every N applied updates (1 = every update).
    #[arg(long, default_value_t = 10)]
    print_every: u64,

    /// Override the REST base URL. Defaults to Polymarket production.
    #[arg(long)]
    rest_base_url: Option<String>,

    /// Override the WS URL. Defaults to Polymarket production.
    #[arg(long)]
    ws_url: Option<String>,

    /// Max WS-delta buffer length before the Tracker degrades.
    #[arg(long, default_value_t = 10_000)]
    max_buffer_len: usize,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "orderbook_probe=info,sniper_orderbook=info".into()),
        )
        .init();

    let cli = Cli::parse();
    run(cli).await
}

async fn run(cli: Cli) -> Result<()> {
    tracing::info!(token_id = %cli.token_id, "starting probe");

    let mut tracker = Tracker::new(TrackerConfig {
        max_buffer_len: cli.max_buffer_len,
    });
    let mut cursor = SequenceCursor::new();
    // Cache: only apply price_changes whose `asset_id` matches the
    // token we subscribed to. Polymarket batches all price_changes
    // for a `market` (i.e. both YES and NO sides of a binary market)
    // into the same WS event, so filtering happens host-side.
    let token_id = cli.token_id.clone();

    // 1. WS first — open the subscription before requesting the snapshot.
    let ws_url = cli.ws_url.as_deref();
    let assets = [token_id.as_str()];
    let mut ws = match ws_url {
        Some(u) => WsSubscriber::connect_to(u, &assets).await,
        None => WsSubscriber::connect(&assets).await,
    }
    .with_context(|| "ws connect")?;

    let r = tracker.on_ws_connected();
    tracing::info!(state = ?r.new_state, "ws connected, tracker armed for buffering");

    // 2. Fire the snapshot request. The Tracker transitions to
    //    `FetchingSnapshot`; in parallel we drain WS deltas into the
    //    buffer.
    let r = tracker.request_snapshot();
    tracing::info!(state = ?r.new_state, "snapshot requested; fetching in background");

    let rest = match cli.rest_base_url.as_deref() {
        Some(b) => RestClient::with_base_url(b),
        None => RestClient::new(),
    }
    .context("rest client")?;
    let snapshot_seq = cursor.peek();
    let snapshot_token = token_id.clone();
    let snapshot_fetch =
        tokio::spawn(async move { rest.fetch_snapshot(&snapshot_token, snapshot_seq).await });

    // Concurrent: drain WS into Tracker buffer until snapshot lands.
    let mut snapshot_handle = Some(snapshot_fetch);
    let mut summary = Summary::default();

    while let Some(handle) = snapshot_handle.as_mut() {
        // Race WS-next vs snapshot-ready.
        {
            tokio::select! {
                biased;
                snap_res = handle => {
                    snapshot_handle = None;
                    match snap_res {
                        Ok(Ok(snap)) => {
                            tracing::info!(
                                seq = snap.sequence,
                                bids = snap.bids.len(),
                                asks = snap.asks.len(),
                                taken_at_unix_ms = snap.taken_at_unix_ms,
                                "snapshot received; reconciling buffer",
                            );
                            // Override snapshot.sequence with current cursor
                            // so post-snapshot deltas (assigned seq from the
                            // same cursor) correctly satisfy `seq == last+1`.
                            // (Buffered pre-snapshot deltas have lower seq
                            // and are dropped by the Tracker's reconcile.)
                            let mut snap = snap;
                            snap.sequence = cursor.peek().saturating_sub(1);
                            let r = tracker.on_snapshot_received(snap);
                            tracing::info!(state = ?r.new_state, "tracker post-snapshot");
                            if matches!(r.new_state, TrackerState::Tracking) {
                                print_top_levels(tracker.book());
                            }
                        }
                        Ok(Err(e)) => {
                            tracing::error!(error = %e, "snapshot fetch failed");
                            let r = tracker.on_snapshot_failed();
                            tracing::warn!(state = ?r.new_state, "tracker degraded; aborting probe");
                            break;
                        }
                        Err(e) => {
                            tracing::error!(error = %e, "snapshot task panicked");
                            break;
                        }
                    }
                }
                evt = ws.next_event() => {
                    if !handle_event(evt, &token_id, &mut tracker, &mut cursor, &mut summary, cli.print_every) {
                        break;
                    }
                }
            }
        }
    }

    if tracker.state() == &TrackerState::Stopped
        || matches!(tracker.state(), TrackerState::Degraded { .. })
    {
        tracing::warn!(state = ?tracker.state(), "exiting before sample window started");
        emit_summary(&summary, &tracker);
        return Ok(());
    }

    // 3. Sample window.
    let deadline = Instant::now() + Duration::from_secs(cli.duration_secs);
    tracing::info!(
        duration_secs = cli.duration_secs,
        "entering Tracking sample window",
    );
    while Instant::now() < deadline {
        let timeout = deadline.saturating_duration_since(Instant::now());
        match tokio::time::timeout(timeout, ws.next_event()).await {
            Ok(evt) => {
                if !handle_event(
                    evt,
                    &token_id,
                    &mut tracker,
                    &mut cursor,
                    &mut summary,
                    cli.print_every,
                ) {
                    break;
                }
            }
            Err(_) => break,
        }
    }

    emit_summary(&summary, &tracker);
    Ok(())
}

#[derive(Default, Debug)]
struct Summary {
    ws_events: u64,
    price_changes_received: u64,
    price_changes_other_asset: u64,
    book_events: u64,
    other_events: u64,
    updates_applied: u64,
    crossed_observed_local: u64,
    apply_errors: u64,
    sequence_gap_degrades: u64,
}

fn handle_event(
    res: Result<Option<WsEvent>, sniper_orderbook::polymarket::ws::WsError>,
    token_id: &str,
    tracker: &mut Tracker,
    cursor: &mut SequenceCursor,
    summary: &mut Summary,
    print_every: u64,
) -> bool {
    summary.ws_events += 1;
    let evt = match res {
        Ok(Some(e)) => e,
        Ok(None) => {
            tracing::warn!("ws stream ended (server closed)");
            return false;
        }
        Err(e) => {
            tracing::error!(error = %e, "ws read failed");
            return false;
        }
    };
    match evt {
        WsEvent::Book(b) => {
            summary.book_events += 1;
            // We rely on the REST snapshot to seed Tracking. WS-book
            // events that arrive later mean the book had a major refresh;
            // for M2 we just log + count.
            tracing::debug!(asset_id = %b.asset_id, "ws book event observed");
        }
        WsEvent::PriceChange(pc) => {
            let received_at = unix_now_ms();
            for change in pc.price_changes.iter() {
                summary.price_changes_received += 1;
                // Polymarket bundles all updates for a market (i.e. both
                // YES and NO sides of a binary market) into one
                // price_change event; filter to our token here.
                if change.asset_id != token_id {
                    summary.price_changes_other_asset += 1;
                    continue;
                }
                let seq = cursor.take_next();
                let update = match ws_price_change_to_update(change, seq, received_at) {
                    Ok(u) => u,
                    Err(e) => {
                        summary.apply_errors += 1;
                        tracing::warn!(error = %e, "skip bad WS price change");
                        continue;
                    }
                };
                let r = tracker.on_ws_message(update);
                if matches!(r.new_state, TrackerState::Tracking) {
                    summary.updates_applied += 1;
                    if tracker.book().is_crossed() {
                        summary.crossed_observed_local += 1;
                    }
                    if print_every > 0 && summary.updates_applied % print_every == 0 {
                        print_top_levels(tracker.book());
                    }
                }
                if matches!(r.new_state, TrackerState::Degraded { .. }) {
                    summary.sequence_gap_degrades += 1;
                    tracing::warn!(state = ?r.new_state, "tracker degraded mid-stream");
                    return false;
                }
            }
        }
        WsEvent::Other { event_type, .. } => {
            summary.other_events += 1;
            tracing::debug!(event_type, "other ws event");
        }
    }
    true
}

fn print_top_levels(book: &OrderBook) {
    let bids = book.top_bids(3);
    let asks = book.top_asks(3);
    let spread = book.spread_micros();
    let mid = book.mid_micros();
    let crossed = book.is_crossed();
    tracing::info!(
        crossed,
        bid_depth = book.bid_depth(),
        ask_depth = book.ask_depth(),
        last_seq = ?book.last_sequence(),
        "top-of-book"
    );
    println!(
        "  ── Top 3 Bids ──        ── Top 3 Asks ──\n{}",
        format_levels_side_by_side(&bids, &asks),
    );
    match (book.best_bid(), book.best_ask(), spread, mid) {
        (Some(b), Some(a), Some(s), Some(m)) => {
            let bps = if a.price_micros > 0 {
                (s as f64) / (m as f64) * 10_000.0
            } else {
                0.0
            };
            println!(
                "  best_bid={} best_ask={} spread={} ({:.2} bps from mid={}){}",
                fmt_price(b.price_micros),
                fmt_price(a.price_micros),
                fmt_price(s),
                bps,
                fmt_price(m),
                if crossed { "  [CROSSED]" } else { "" },
            );
        }
        _ => {
            println!("  best_bid=- best_ask=- spread=- (insufficient liquidity)");
        }
    }
}

fn format_levels_side_by_side(bids: &[PriceLevel], asks: &[PriceLevel]) -> String {
    let rows = bids.len().max(asks.len()).max(3);
    let mut out = String::new();
    for i in 0..rows {
        let bid = bids.get(i).copied();
        let ask = asks.get(i).copied();
        let bid_str = bid
            .map(|l| {
                format!(
                    "{}  size={}",
                    fmt_price(l.price_micros),
                    fmt_size(l.size_micros)
                )
            })
            .unwrap_or_else(|| "—".to_owned());
        let ask_str = ask
            .map(|l| {
                format!(
                    "{}  size={}",
                    fmt_price(l.price_micros),
                    fmt_size(l.size_micros)
                )
            })
            .unwrap_or_else(|| "—".to_owned());
        out.push_str(&format!("    {}  |  {}\n", bid_str, ask_str));
    }
    out
}

fn fmt_price(p: i64) -> String {
    // micros → decimal dollar string, e.g. 480_000 → "0.480000"
    let int = p / 1_000_000;
    let frac = (p.unsigned_abs() % 1_000_000) as u32;
    if p < 0 {
        format!("-{}.{:06}", int.unsigned_abs(), frac)
    } else {
        format!("{}.{:06}", int, frac)
    }
}

fn fmt_size(s: i64) -> String {
    let int = s / 1_000_000;
    let frac = (s.unsigned_abs() % 1_000_000) as u32;
    if frac == 0 {
        int.to_string()
    } else {
        format!("{}.{:06}", int, frac)
    }
}

fn unix_now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn emit_summary(s: &Summary, tracker: &Tracker) {
    tracing::info!(
        ws_events = s.ws_events,
        book_events = s.book_events,
        price_changes_received = s.price_changes_received,
        price_changes_other_asset = s.price_changes_other_asset,
        other_events = s.other_events,
        updates_applied = s.updates_applied,
        crossed_observed_local = s.crossed_observed_local,
        apply_errors = s.apply_errors,
        sequence_gap_degrades = s.sequence_gap_degrades,
        final_state = ?tracker.state(),
        bid_depth = tracker.book().bid_depth(),
        ask_depth = tracker.book().ask_depth(),
        "probe summary",
    );
    println!();
    println!("=== Probe Summary ===");
    println!("WS events:                  {}", s.ws_events);
    println!("  book:                     {}", s.book_events);
    println!("  price_change (any asset): {}", s.price_changes_received);
    println!(
        "    other asset (filtered): {}",
        s.price_changes_other_asset
    );
    println!("  other:                    {}", s.other_events);
    println!("Applied deltas (our asset): {}", s.updates_applied);
    println!("Crossed observed (local):   {}", s.crossed_observed_local);
    println!("Apply errors:               {}", s.apply_errors);
    println!("Seq gap degrades:           {}", s.sequence_gap_degrades);
    println!("Final state:                {:?}", tracker.state());
    println!(
        "Final book depth:           bids={}  asks={}",
        tracker.book().bid_depth(),
        tracker.book().ask_depth(),
    );
}
