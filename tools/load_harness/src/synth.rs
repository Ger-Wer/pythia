//! Deterministic synthetic markets used by the load harness.

use dual_loop_common::proto::common::MarketId;

/// One synthetic market: stable `polymarket_id`, `ctf_address`, and
/// `market_specific_key`. The `ctf_address` is the bus key sniper-ingest
/// uses to look the entry up in its watchlist (see
/// `services/sniper-ingest/src/matcher.rs::resolve_market_key_from_address`).
#[derive(Debug, Clone)]
pub struct SyntheticMarket {
    pub polymarket_id: String,
    pub ctf_address: String,
    pub market_specific_key: String,
}

impl SyntheticMarket {
    pub fn to_market_id(&self) -> MarketId {
        MarketId {
            polymarket_id: self.polymarket_id.clone(),
            ctf_address: self.ctf_address.clone(),
            market_specific_key: self.market_specific_key.clone(),
        }
    }
}

/// Generate `count` distinct synthetic markets with deterministic
/// addresses. Addresses are pure functions of the index so two harness
/// runs converge on the same watchlist entries (matters when the dev
/// stack is re-used across runs).
pub fn synthetic_markets(count: usize) -> Vec<SyntheticMarket> {
    (0..count)
        .map(|i| {
            // The leading bytes spell "load" in valid hex (`0xa110ad…`)
            // so the synthetic markets are visually distinct from real
            // Polymarket / CTF addresses while still parsing as
            // `alloy_primitives::Address` (sniper-ingest's matcher
            // expects strict hex; see resolve_market_key_from_address).
            let ctf_address = format!("0xa110ad00{:032x}", i + 1);
            let polymarket_id = format!("load-harness-market-{i:03}");
            let market_specific_key = format!("0x{:064x}", (i as u64) * 0x1111_1111_1111_1111);
            SyntheticMarket {
                polymarket_id,
                ctf_address,
                market_specific_key,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_markets_are_distinct() {
        let markets = synthetic_markets(8);
        assert_eq!(markets.len(), 8);
        let mut ids = std::collections::HashSet::new();
        for m in &markets {
            assert!(ids.insert(m.ctf_address.clone()), "ctf_address dup");
            assert!(ids.insert(m.polymarket_id.clone()), "polymarket_id dup");
        }
    }

    #[test]
    fn synthetic_markets_are_deterministic() {
        let a = synthetic_markets(4);
        let b = synthetic_markets(4);
        for (x, y) in a.iter().zip(b.iter()) {
            assert_eq!(x.ctf_address, y.ctf_address);
            assert_eq!(x.polymarket_id, y.polymarket_id);
            assert_eq!(x.market_specific_key, y.market_specific_key);
        }
    }

    /// Sniper-ingest's mock consumer uses
    /// `alloy_primitives::Address::from_str(&market.ctf_address)` which
    /// rejects anything that isn't strict hex. A previous version of this
    /// generator used a `0xLOAD…` prefix (literal letters L, O, A, D)
    /// which made every mock tx fail with `invalid character 'L'` and
    /// silently produced a 100% drop rate. Lock that in.
    #[test]
    fn synthetic_market_ctf_addresses_are_valid_hex() {
        for market in synthetic_markets(8) {
            let parsed = market.ctf_address.parse::<alloy_primitives::Address>();
            assert!(
                parsed.is_ok(),
                "ctf_address `{}` failed to parse: {:?}",
                market.ctf_address,
                parsed.err()
            );
        }
    }
}
