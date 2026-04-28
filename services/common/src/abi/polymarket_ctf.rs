//! Polymarket CTF Exchange decoder.
//!
//! Contract on Polygon: `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`.

use alloy_primitives::LogData;
use alloy_sol_types::{sol, SolEvent};

use super::DecodedEvent;

sol! {
    contract CtfExchange {
        event OrderFilled(
            bytes32 indexed orderHash,
            address indexed maker,
            address indexed taker,
            uint256 makerAssetId,
            uint256 takerAssetId,
            uint256 makerAmountFilled,
            uint256 takerAmountFilled,
            uint256 fee
        );

        event OrderMatched(
            bytes32 indexed takerOrderHash,
            bytes32 indexed makerOrderHash,
            uint256 takerAssetId,
            uint256 makerAssetId,
            uint256 takerAmountFilled,
            uint256 makerAmountFilled
        );

        event TokenRegistered(
            uint256 indexed token0,
            uint256 indexed token1,
            bytes32 indexed conditionId
        );
    }
}

pub fn try_decode_order_filled(log: &LogData) -> Option<DecodedEvent> {
    let decoded = CtfExchange::OrderFilled::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::CtfOrderFilled {
        order_hash: decoded.orderHash,
        maker: decoded.maker,
        taker: decoded.taker,
        maker_asset_id: decoded.makerAssetId,
        taker_asset_id: decoded.takerAssetId,
        maker_amount_filled: decoded.makerAmountFilled,
        taker_amount_filled: decoded.takerAmountFilled,
        fee: decoded.fee,
    })
}

pub fn try_decode_order_matched(log: &LogData) -> Option<DecodedEvent> {
    let decoded = CtfExchange::OrderMatched::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::CtfOrderMatched {
        taker_order_hash: decoded.takerOrderHash,
        maker_order_hash: decoded.makerOrderHash,
        taker_asset_id: decoded.takerAssetId,
        maker_asset_id: decoded.makerAssetId,
        taker_amount_filled: decoded.takerAmountFilled,
        maker_amount_filled: decoded.makerAmountFilled,
    })
}

pub fn try_decode_token_registered(log: &LogData) -> Option<DecodedEvent> {
    let decoded = CtfExchange::TokenRegistered::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::CtfTokenRegistered {
        token0: decoded.token0,
        token1: decoded.token1,
        condition_id: decoded.conditionId,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{address, b256, Address, LogData, U256};

    fn build_order_filled_log() -> (Address, LogData) {
        let addr = address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E");
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
        let encoded = event.encode_log_data();
        (addr, encoded)
    }

    #[test]
    fn round_trip_order_filled() {
        let (addr, log) = build_order_filled_log();
        let decoded = super::super::try_decode_log(addr, &log).unwrap();
        match decoded {
            DecodedEvent::CtfOrderFilled {
                maker_amount_filled,
                taker_amount_filled,
                fee,
                ..
            } => {
                assert_eq!(maker_amount_filled, U256::from(1000u64));
                assert_eq!(taker_amount_filled, U256::from(2000u64));
                assert_eq!(fee, U256::from(10u64));
            }
            other => panic!("unexpected variant: {other:?}"),
        }
    }

    #[test]
    fn round_trip_order_matched() {
        let event = CtfExchange::OrderMatched {
            takerOrderHash: b256!(
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            ),
            makerOrderHash: b256!(
                "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
            ),
            takerAssetId: U256::from(1u64),
            makerAssetId: U256::from(2u64),
            takerAmountFilled: U256::from(500u64),
            makerAmountFilled: U256::from(600u64),
        };
        let log = event.encode_log_data();
        let addr = address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E");
        let decoded = super::super::try_decode_log(addr, &log).unwrap();
        assert!(matches!(decoded, DecodedEvent::CtfOrderMatched { .. }));
    }

    #[test]
    fn wrong_address_returns_none() {
        let (_, log) = build_order_filled_log();
        assert!(super::super::try_decode_log(Address::ZERO, &log).is_none());
    }
}
