//! Performance benchmarks for sniper-ingest hot path.
//!
//! Run: `cargo bench --package sniper-ingest`

use criterion::{black_box, criterion_group, criterion_main, Criterion};

use alloy_primitives::{address, b256, Address, U256};
use alloy_sol_types::SolEvent;
use dual_loop_common::abi::polymarket_ctf::CtfExchange;
use dual_loop_common::abi::{self, addresses, DecodedEvent};
use dual_loop_common::proto::common::{MarketId, Money, Timestamp};
use dual_loop_common::proto::watchlist::{TriggerKind, TriggerPolicy, WatchlistUpsert};
use sniper_ingest::matcher::TriggerMatcher;
use sniper_ingest::watchlist::WatchlistStore;

fn make_store() -> (WatchlistStore, Address) {
    let store = WatchlistStore::new();
    let ctf_addr = addresses::POLYMARKET_CTF_EXCHANGE;
    let ctf_str = format!("{ctf_addr:#x}");

    let upsert = WatchlistUpsert {
        message_id: None,
        market: Some(MarketId {
            polymarket_id: "bench-market".to_string(),
            ctf_address: ctf_str,
        }),
        version: 1,
        edge: 0.15,
        p_fundamental: 0.72,
        confidence: 0.9,
        side_hint: 1,
        max_position_usd: Some(Money {
            amount_micros: 10_000_000,
        }),
        max_slippage_bps: 50,
        ttl_ms: 3_600_000,
        issued_at: Some(Timestamp {
            wall_clock_unix_ms: dual_loop_common::time::StampedTime::now().wall_clock_unix_ms,
            monotonic_ns: 0,
        }),
        trigger_policy: Some(TriggerPolicy {
            min_independent_sources: 1,
            allowed_kinds: vec![
                TriggerKind::LargeFill as i32,
                TriggerKind::OracleResolution as i32,
            ],
            max_price_impact_bps: 100,
        }),
    };
    store.apply_upsert(&upsert);
    (store, ctf_addr)
}

fn make_order_filled_event() -> DecodedEvent {
    DecodedEvent::CtfOrderFilled {
        order_hash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        maker: address!("1111111111111111111111111111111111111111"),
        taker: address!("2222222222222222222222222222222222222222"),
        maker_asset_id: U256::from(1u64),
        taker_asset_id: U256::from(2u64),
        maker_amount_filled: U256::from(1000u64),
        taker_amount_filled: U256::from(2000u64),
        fee: U256::from(5u64),
    }
}

fn bench_matcher_throughput(c: &mut Criterion) {
    let (store, ctf_addr) = make_store();
    let event = make_order_filled_event();

    c.bench_function("matcher_throughput_1M", |b| {
        b.iter(|| {
            let mut matcher = TriggerMatcher::new(store.clone());
            for i in 0..1_000_000u64 {
                black_box(matcher.try_match(black_box(&event), ctf_addr, i));
            }
        });
    });
}

fn bench_decode_ctf_event(c: &mut Criterion) {
    let event = CtfExchange::OrderFilled {
        orderHash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        maker: address!("1111111111111111111111111111111111111111"),
        taker: address!("2222222222222222222222222222222222222222"),
        makerAssetId: U256::from(100u64),
        takerAssetId: U256::from(200u64),
        makerAmountFilled: U256::from(1000u64),
        takerAmountFilled: U256::from(2000u64),
        fee: U256::from(10u64),
    };
    let log = event.encode_log_data();
    let addr = addresses::POLYMARKET_CTF_EXCHANGE;

    c.bench_function("decode_ctf_event_10k", |b| {
        b.iter(|| {
            for _ in 0..10_000 {
                black_box(abi::try_decode_log(addr, black_box(&log)));
            }
        });
    });
}

fn bench_end_to_end_latency(c: &mut Criterion) {
    let (store, ctf_addr) = make_store();
    let event = make_order_filled_event();

    c.bench_function("e2e_latency_100_events", |b| {
        b.iter(|| {
            let mut matcher = TriggerMatcher::new(store.clone());
            for i in 0..100u64 {
                let start = sniper_ingest::mempool::monotonic_ns();
                let trigger = matcher.try_match(black_box(&event), ctf_addr, i);
                let _latency = sniper_ingest::mempool::monotonic_ns() - start;
                black_box(trigger);
            }
        });
    });
}

criterion_group!(
    benches,
    bench_matcher_throughput,
    bench_decode_ctf_event,
    bench_end_to_end_latency
);
criterion_main!(benches);
