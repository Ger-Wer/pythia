//! ABI decoders for on-chain contracts relevant to the Dual-Loop Fast Loop.
//!
//! Each sub-module exposes Solidity types via `alloy_sol_types::sol!` and a
//! `try_decode_log` helper that maps raw EVM logs to strongly-typed
//! [`DecodedEvent`] variants.

pub mod chainlink_feed;
pub mod polymarket_ctf;
pub mod uma_oracle;

use alloy_primitives::{Address, B256, I256, U256};
use alloy_sol_types::SolEvent;

/// Strongly-typed union of all decoded on-chain events the matcher cares about.
#[derive(Debug, Clone, PartialEq)]
pub enum DecodedEvent {
    // -- Polymarket CTF Exchange --
    CtfOrderFilled {
        order_hash: B256,
        maker: Address,
        taker: Address,
        maker_asset_id: U256,
        taker_asset_id: U256,
        maker_amount_filled: U256,
        taker_amount_filled: U256,
        fee: U256,
    },
    CtfOrderMatched {
        taker_order_hash: B256,
        maker_order_hash: B256,
        taker_asset_id: U256,
        maker_asset_id: U256,
        taker_amount_filled: U256,
        maker_amount_filled: U256,
    },
    CtfTokenRegistered {
        token0: U256,
        token1: U256,
        condition_id: B256,
    },

    // -- UMA Optimistic Oracle V3 --
    UmaProposePrice {
        requester: Address,
        proposer: Address,
        question_id: B256,
        proposed_price: I256,
        expiration_timestamp: U256,
    },
    UmaDisputePrice {
        requester: Address,
        proposer: Address,
        disputer: Address,
        question_id: B256,
        proposed_price: I256,
    },
    UmaSettle {
        requester: Address,
        proposer: Address,
        question_id: B256,
        settled_price: I256,
    },

    // -- Chainlink Aggregator --
    ChainlinkAnswerUpdated {
        current: I256,
        round_id: U256,
        updated_at: U256,
    },
}

/// Known contract addresses on Polygon mainnet.
pub mod addresses {
    use alloy_primitives::address;
    use alloy_primitives::Address;

    pub const POLYMARKET_CTF_EXCHANGE: Address =
        address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E");
    pub const UMA_OPTIMISTIC_ORACLE_V3: Address =
        address!("fb55F43fB9F48F63f9269DB7Dde3BbBe1ebDC0dE");
}

/// Attempt to decode a raw log emitted by `address` into a [`DecodedEvent`].
///
/// Returns `None` if the log topic does not match any known event selector.
pub fn try_decode_log(address: Address, log: &alloy_primitives::LogData) -> Option<DecodedEvent> {
    let topic0 = log.topics().first()?;

    // Polymarket CTF Exchange
    if address == addresses::POLYMARKET_CTF_EXCHANGE {
        if *topic0 == polymarket_ctf::CtfExchange::OrderFilled::SIGNATURE_HASH {
            return polymarket_ctf::try_decode_order_filled(log);
        }
        if *topic0 == polymarket_ctf::CtfExchange::OrderMatched::SIGNATURE_HASH {
            return polymarket_ctf::try_decode_order_matched(log);
        }
        if *topic0 == polymarket_ctf::CtfExchange::TokenRegistered::SIGNATURE_HASH {
            return polymarket_ctf::try_decode_token_registered(log);
        }
    }

    // UMA Oracle V3
    if address == addresses::UMA_OPTIMISTIC_ORACLE_V3 {
        if *topic0 == uma_oracle::UmaOracleV3::ProposePrice::SIGNATURE_HASH {
            return uma_oracle::try_decode_propose_price(log);
        }
        if *topic0 == uma_oracle::UmaOracleV3::DisputePrice::SIGNATURE_HASH {
            return uma_oracle::try_decode_dispute_price(log);
        }
        if *topic0 == uma_oracle::UmaOracleV3::Settle::SIGNATURE_HASH {
            return uma_oracle::try_decode_settle(log);
        }
    }

    // Chainlink — any address can be an aggregator, match by topic only
    if *topic0 == chainlink_feed::ChainlinkAggregator::AnswerUpdated::SIGNATURE_HASH {
        return chainlink_feed::try_decode_answer_updated(log);
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{Bytes, LogData, B256};

    #[test]
    fn unknown_topic_returns_none() {
        let log = LogData::new(vec![B256::ZERO], Bytes::new()).unwrap();
        assert!(try_decode_log(Address::ZERO, &log).is_none());
    }

    #[test]
    fn empty_log_returns_none() {
        let log = LogData::new(vec![], Bytes::new()).unwrap();
        assert!(try_decode_log(Address::ZERO, &log).is_none());
    }
}
