//! Chainlink Aggregator decoder.
//!
//! Any address can host an aggregator proxy, so matching is topic-only
//! (no address filter in [`super::try_decode_log`]).

use alloy_primitives::LogData;
use alloy_sol_types::{sol, SolEvent};

use super::DecodedEvent;

sol! {
    contract ChainlinkAggregator {
        event AnswerUpdated(
            int256 indexed current,
            uint256 indexed roundId,
            uint256 updatedAt
        );
    }
}

pub fn try_decode_answer_updated(log: &LogData) -> Option<DecodedEvent> {
    let decoded = ChainlinkAggregator::AnswerUpdated::decode_log_data(log, true).ok()?;
    Some(DecodedEvent::ChainlinkAnswerUpdated {
        current: decoded.current,
        round_id: decoded.roundId,
        updated_at: decoded.updatedAt,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{Address, I256, U256};
    use alloy_sol_types::SolEvent;

    #[test]
    fn round_trip_answer_updated() {
        let event = ChainlinkAggregator::AnswerUpdated {
            current: I256::try_from(123_456_789i64).unwrap(),
            roundId: U256::from(42u64),
            updatedAt: U256::from(1_700_000_000u64),
        };
        let log = event.encode_log_data();
        let decoded = super::super::try_decode_log(Address::ZERO, &log).unwrap();
        match decoded {
            DecodedEvent::ChainlinkAnswerUpdated {
                current,
                round_id,
                updated_at,
            } => {
                assert_eq!(current, I256::try_from(123_456_789i64).unwrap());
                assert_eq!(round_id, U256::from(42u64));
                assert_eq!(updated_at, U256::from(1_700_000_000u64));
            }
            other => panic!("unexpected: {other:?}"),
        }
    }
}
