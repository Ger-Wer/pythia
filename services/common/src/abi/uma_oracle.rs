//! UMA Optimistic Oracle V3 decoder.
//!
//! Contract on Polygon: `0xfb55F43fB9F48F63f9269DB7Dde3BbBe1ebDC0dE`.

use alloy_primitives::LogData;
use alloy_sol_types::{sol, SolEvent};

use super::DecodedEvent;

sol! {
    contract UmaOracleV3 {
        event ProposePrice(
            address indexed requester,
            address indexed proposer,
            bytes32 questionId,
            int256 proposedPrice,
            uint256 expirationTimestamp,
            bytes ancillaryData
        );

        event DisputePrice(
            address indexed requester,
            address indexed proposer,
            address indexed disputer,
            bytes32 questionId,
            int256 proposedPrice,
            bytes ancillaryData
        );

        event Settle(
            address indexed requester,
            address indexed proposer,
            bytes32 questionId,
            int256 settledPrice,
            bytes ancillaryData
        );
    }
}

pub fn try_decode_propose_price(log: &LogData) -> Option<DecodedEvent> {
    let decoded = UmaOracleV3::ProposePrice::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::UmaProposePrice {
        requester: decoded.requester,
        proposer: decoded.proposer,
        question_id: decoded.questionId,
        proposed_price: decoded.proposedPrice,
        expiration_timestamp: decoded.expirationTimestamp,
    })
}

pub fn try_decode_dispute_price(log: &LogData) -> Option<DecodedEvent> {
    let decoded = UmaOracleV3::DisputePrice::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::UmaDisputePrice {
        requester: decoded.requester,
        proposer: decoded.proposer,
        disputer: decoded.disputer,
        question_id: decoded.questionId,
        proposed_price: decoded.proposedPrice,
    })
}

pub fn try_decode_settle(log: &LogData) -> Option<DecodedEvent> {
    let decoded = UmaOracleV3::Settle::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::UmaSettle {
        requester: decoded.requester,
        proposer: decoded.proposer,
        question_id: decoded.questionId,
        settled_price: decoded.settledPrice,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{address, b256, I256, U256};
    use alloy_sol_types::SolEvent;

    #[test]
    fn round_trip_propose_price() {
        let event = UmaOracleV3::ProposePrice {
            requester: address!("1111111111111111111111111111111111111111"),
            proposer: address!("2222222222222222222222222222222222222222"),
            questionId: b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            proposedPrice: I256::try_from(1_000_000i64).unwrap(),
            expirationTimestamp: U256::from(9999u64),
            ancillaryData: vec![0x01, 0x02].into(),
        };
        let log = event.encode_log_data();
        let addr = super::super::addresses::UMA_OPTIMISTIC_ORACLE_V3;
        let decoded = super::super::try_decode_log(addr, &log).unwrap();
        match decoded {
            DecodedEvent::UmaProposePrice {
                proposed_price,
                expiration_timestamp,
                ..
            } => {
                assert_eq!(proposed_price, I256::try_from(1_000_000i64).unwrap());
                assert_eq!(expiration_timestamp, U256::from(9999u64));
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn round_trip_settle() {
        let event = UmaOracleV3::Settle {
            requester: address!("1111111111111111111111111111111111111111"),
            proposer: address!("2222222222222222222222222222222222222222"),
            questionId: b256!("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
            settledPrice: I256::try_from(500_000i64).unwrap(),
            ancillaryData: vec![].into(),
        };
        let log = event.encode_log_data();
        let addr = super::super::addresses::UMA_OPTIMISTIC_ORACLE_V3;
        let decoded = super::super::try_decode_log(addr, &log).unwrap();
        assert!(matches!(decoded, DecodedEvent::UmaSettle { .. }));
    }
}
