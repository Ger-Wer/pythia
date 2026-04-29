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
use alloy_primitives::Address;
use dual_loop_common::abi::addresses;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TargetContract {
    PolymarketCtfExchange,
    UmaOptimisticOracleV3,
}

impl TargetContract {
    pub fn label(self) -> &'static str {
        match self {
            Self::PolymarketCtfExchange => "polymarket_ctf_exchange",
            Self::UmaOptimisticOracleV3 => "uma_optimistic_oracle_v3",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PendingCallObservation {
    pub contract: TargetContract,
    pub to: Address,
    pub selector: Option<[u8; 4]>,
    pub input_len: usize,
}

impl PendingCallObservation {
    pub fn selector_hex(&self) -> Option<String> {
        self.selector.map(hex::encode)
    }
}

pub fn classify_target_address(address: Address) -> Option<TargetContract> {
    if address == addresses::POLYMARKET_CTF_EXCHANGE {
        return Some(TargetContract::PolymarketCtfExchange);
    }
    if address == addresses::UMA_OPTIMISTIC_ORACLE_V3 {
        return Some(TargetContract::UmaOptimisticOracleV3);
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
    let contract = classify_target_address(to)?;
    let input = tx.input();
    Some(PendingCallObservation {
        contract,
        to,
        selector: selector_from_input(input.as_ref()),
        input_len: input.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloy_primitives::address;

    #[test]
    fn classifies_known_target_addresses() {
        assert_eq!(
            classify_target_address(addresses::POLYMARKET_CTF_EXCHANGE),
            Some(TargetContract::PolymarketCtfExchange)
        );
        assert_eq!(
            classify_target_address(addresses::UMA_OPTIMISTIC_ORACLE_V3),
            Some(TargetContract::UmaOptimisticOracleV3)
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
        };
        assert_eq!(observation.selector_hex().as_deref(), Some("deadbeef"));
    }
}
