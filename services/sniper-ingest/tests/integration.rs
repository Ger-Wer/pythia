//! Integration tests for sniper-ingest.
//!
//! These tests require Anvil (from Foundry) to be available in PATH or
//! use `alloy_node_bindings::Anvil` to spawn a local instance.

use alloy::providers::{Provider, ProviderBuilder, WsConnect};
use alloy_node_bindings::Anvil;
use alloy_primitives::{address, b256, Address, U256};
use alloy_sol_types::SolEvent;
use dual_loop_common::abi::polymarket_ctf::CtfExchange;
use dual_loop_common::abi::{self, DecodedEvent};
use sniper_ingest::matcher::TriggerMatcher;
use sniper_ingest::mempool::monotonic_ns;
use sniper_ingest::watchlist::WatchlistStore;

use dual_loop_common::proto::common::{MarketId, Money};
use dual_loop_common::proto::watchlist::{TriggerKind, TriggerPolicy, WatchlistUpsert};

fn setup_watchlist(store: &WatchlistStore) {
    let ctf_str = format!(
        "{:#x}",
        address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
    );
    let upsert = WatchlistUpsert {
        message_id: None,
        market: Some(MarketId {
            polymarket_id: "test-market-1".to_string(),
            ctf_address: ctf_str,
        }),
        version: 1,
        edge_ppm: 150_000,
        p_fundamental_ppm: 720_000,
        confidence_ppm: 900_000,
        side_hint: 1,
        max_position_usd: Some(Money {
            amount_micros: 10_000_000,
        }),
        max_slippage_bps: 50,
        ttl_ms: 3_600_000,
        issued_at_unix_ms: dual_loop_common::time::StampedTime::now().wall_clock_unix_ms,
        trigger_policy: Some(TriggerPolicy {
            min_independent_sources: 1,
            allowed_kinds: vec![TriggerKind::LargeFill as i32],
            max_price_impact_bps: 100,
        }),
    };
    store.apply_upsert(&upsert);
}

#[test]
fn matcher_fires_on_ctf_order_filled() {
    let store = WatchlistStore::new();
    setup_watchlist(&store);

    let event = DecodedEvent::CtfOrderFilled {
        order_hash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        maker: address!("1111111111111111111111111111111111111111"),
        taker: address!("2222222222222222222222222222222222222222"),
        maker_asset_id: U256::from(1u64),
        taker_asset_id: U256::from(2u64),
        maker_amount_filled: U256::from(1000u64),
        taker_amount_filled: U256::from(2000u64),
        fee: U256::from(5u64),
    };

    let mut matcher = TriggerMatcher::new(store);
    let ctf_addr = address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E");

    let start = monotonic_ns();
    let trigger = matcher.try_match(&event, ctf_addr, start);
    let latency = monotonic_ns() - start;

    assert!(trigger.is_some(), "trigger should fire");
    let trigger = trigger.unwrap();
    assert_eq!(trigger.kind, TriggerKind::LargeFill as i32);
    assert_eq!(
        trigger.expected_price.as_ref().map(|money| money.amount_micros),
        Some(720_000)
    );
    // Matcher latency should be well under 10ms.
    assert!(latency < 10_000_000, "latency_ns={latency} > 10ms");
}

#[test]
fn matcher_ignores_unknown_market() {
    let store = WatchlistStore::new();
    // Don't add any watchlist entries.
    let mut matcher = TriggerMatcher::new(store);

    let event = DecodedEvent::CtfOrderFilled {
        order_hash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        maker: address!("1111111111111111111111111111111111111111"),
        taker: address!("2222222222222222222222222222222222222222"),
        maker_asset_id: U256::from(1u64),
        taker_asset_id: U256::from(2u64),
        maker_amount_filled: U256::from(1000u64),
        taker_amount_filled: U256::from(2000u64),
        fee: U256::from(5u64),
    };

    let trigger = matcher.try_match(
        &event,
        address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
        monotonic_ns(),
    );
    assert!(
        trigger.is_none(),
        "trigger should NOT fire for unknown market"
    );
}

#[test]
fn abi_decoder_round_trip_all_events() {
    // CTF OrderFilled
    let ctf_event = CtfExchange::OrderFilled {
        orderHash: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        maker: address!("1111111111111111111111111111111111111111"),
        taker: address!("2222222222222222222222222222222222222222"),
        makerAssetId: U256::from(100u64),
        takerAssetId: U256::from(200u64),
        makerAmountFilled: U256::from(1000u64),
        takerAmountFilled: U256::from(2000u64),
        fee: U256::from(10u64),
    };
    let log = ctf_event.encode_log_data();
    let addr = address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E");
    let decoded = abi::try_decode_log(addr, &log);
    assert!(matches!(decoded, Some(DecodedEvent::CtfOrderFilled { .. })));

    // Wrong address should fail.
    assert!(abi::try_decode_log(Address::ZERO, &log).is_none());
}

#[tokio::test]
async fn anvil_ws_connection_works() {
    let Ok(anvil) = Anvil::new()
        .arg("--host")
        .arg("0.0.0.0")
        .arg("--chain-id")
        .arg("137")
        .try_spawn()
    else {
        eprintln!("Anvil not found; skipping integration test");
        return;
    };

    let ws_url = format!("ws://127.0.0.1:{}", anvil.port());
    let ws = WsConnect::new(&ws_url);
    let provider = ProviderBuilder::new().on_ws(ws).await;
    assert!(provider.is_ok(), "WS connection to Anvil should succeed");

    let chain_id = provider.unwrap().get_chain_id().await.unwrap();
    assert_eq!(chain_id, 137);
}
