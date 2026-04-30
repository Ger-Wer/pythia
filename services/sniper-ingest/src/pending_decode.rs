//! Pending transaction target classification for the real mempool path.
//!
//! Phase 2-C/real-mempool preparation:
//! - filter pending txs down to the contracts we actually care about;
//! - extract the 4-byte selector so calldata decoding can be layered on
//!   without reworking the subscriber loop;
//! - keep the logic honest: we only classify addresses and selectors that
//!   are backed by canonical contract addresses already defined in
//!   `dual_loop_common::abi`.

use alloy::consensus::Transaction as TxTrait;
use alloy::rpc::types::Transaction;
use alloy_primitives::{Address, B256};
use alloy_sol_types::{sol, SolCall, SolValue};
use dual_loop_common::abi::addresses;

sol! {
    interface UmaCtfAdapter {
        function resolve(bytes32 questionID) external;
        function resolveManually(bytes32 questionID, uint256[] calldata payouts) external;
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TargetContract {
    PolymarketCtfExchange,
    PolymarketUmaAdapter,
}

impl TargetContract {
    pub fn label(self) -> &'static str {
        match self {
            Self::PolymarketCtfExchange => "polymarket_ctf_exchange",
            Self::PolymarketUmaAdapter => "polymarket_uma_adapter",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolutionObservation {
    pub selector_name: &'static str,
    pub question_id: B256,
    pub market_specific_key: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PendingCallObservation {
    pub contract: TargetContract,
    pub to: Address,
    pub selector: Option<[u8; 4]>,
    pub input_len: usize,
    pub resolution: Option<ResolutionObservation>,
}

impl PendingCallObservation {
    pub fn selector_hex(&self) -> Option<String> {
        self.selector.map(hex::encode)
    }
}

pub fn classify_target_address(address: Address) -> Option<TargetContract> {
    if addresses::is_polymarket_ctf_exchange(address) {
        return Some(TargetContract::PolymarketCtfExchange);
    }
    if addresses::is_polymarket_uma_adapter(address) {
        return Some(TargetContract::PolymarketUmaAdapter);
    }
    None
}

pub fn selector_from_input(input: &[u8]) -> Option<[u8; 4]> {
    if input.len() < 4 {
        return None;
    }
    let mut selector = [0u8; 4];
    selector.copy_from_slice(&input[..4]);
    Some(selector)
}

pub fn classify_pending_tx(tx: &Transaction) -> Option<PendingCallObservation> {
    let to = tx.to()?;
    let input = tx.input();
    classify_pending_call(to, input.as_ref())
}

pub fn classify_pending_call(to: Address, input: &[u8]) -> Option<PendingCallObservation> {
    let contract = classify_target_address(to)?;
    Some(PendingCallObservation {
        contract,
        to,
        selector: selector_from_input(input),
        input_len: input.len(),
        resolution: decode_resolution_call(contract, to, input),
    })
}

pub fn condition_id_from_question_id(oracle: Address, question_id: B256) -> String {
    let encoded = (oracle, question_id, alloy_primitives::U256::from(2u64)).abi_encode_packed();
    format!("0x{}", hex::encode(alloy_primitives::keccak256(encoded)))
}

fn decode_resolution_call(
    contract: TargetContract,
    to: Address,
    input: &[u8],
) -> Option<ResolutionObservation> {
    if contract != TargetContract::PolymarketUmaAdapter {
        return None;
    }

    if let Ok(call) = UmaCtfAdapter::resolveCall::abi_decode(input, true) {
        let question_id = call.questionID;
        return Some(ResolutionObservation {
            selector_name: "resolve",
            question_id,
            market_specific_key: condition_id_from_question_id(to, question_id),
        });
    }

    if let Ok(call) = UmaCtfAdapter::resolveManuallyCall::abi_decode(input, true) {
        let question_id = call.questionID;
        return Some(ResolutionObservation {
            selector_name: "resolveManually",
            question_id,
            market_specific_key: condition_id_from_question_id(to, question_id),
        });
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::{address, b256};

    #[test]
    fn classifies_known_target_addresses() {
        assert_eq!(
            classify_target_address(addresses::POLYMARKET_CTF_EXCHANGE),
            Some(TargetContract::PolymarketCtfExchange)
        );
        assert_eq!(
            classify_target_address(addresses::POLYMARKET_UMA_ADAPTER_V3),
            Some(TargetContract::PolymarketUmaAdapter)
        );
        assert_eq!(
            classify_target_address(address!("1111111111111111111111111111111111111111")),
            None
        );
    }

    #[test]
    fn extracts_selector_from_calldata() {
        assert_eq!(
            selector_from_input(&[0xde, 0xad, 0xbe, 0xef, 0x01, 0x02]),
            Some([0xde, 0xad, 0xbe, 0xef])
        );
        assert_eq!(selector_from_input(&[0xde, 0xad, 0xbe]), None);
    }

    #[test]
    fn selector_hex_formats_lowercase() {
        let observation = PendingCallObservation {
            contract: TargetContract::PolymarketCtfExchange,
            to: addresses::POLYMARKET_CTF_EXCHANGE,
            selector: Some([0xde, 0xad, 0xbe, 0xef]),
            input_len: 4,
            resolution: None,
        };
        assert_eq!(observation.selector_hex().as_deref(), Some("deadbeef"));
    }

    #[test]
    fn decodes_uma_adapter_resolve_to_market_specific_key() {
        let question_id = b256!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        let input = UmaCtfAdapter::resolveCall {
            questionID: question_id,
        }
        .abi_encode();

        let observation =
            classify_pending_call(addresses::POLYMARKET_UMA_ADAPTER_V3, &input).expect("decode");
        assert_eq!(observation.contract, TargetContract::PolymarketUmaAdapter);
        assert_eq!(
            observation
                .resolution
                .as_ref()
                .map(|value| value.selector_name),
            Some("resolve")
        );
        assert_eq!(
            observation
                .resolution
                .as_ref()
                .map(|value| value.question_id),
            Some(question_id)
        );
        assert_eq!(
            observation
                .resolution
                .as_ref()
                .map(|value| value.market_specific_key.as_str()),
            Some(
                condition_id_from_question_id(addresses::POLYMARKET_UMA_ADAPTER_V3, question_id)
                    .as_str()
            )
        );
    }
}
