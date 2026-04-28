# Phase 2 — Risk Engine Operator Guide

## Overview

`sniper-risk` is the pre-trade risk engine for the Dual-Loop Fast Loop. It evaluates every trade intent before broadcast and blocks trades that violate position limits, daily budgets, circuit breaker rules, or operator liveness requirements.

The primary integration point is the `RiskGuard` struct, which aggregates all risk components into a single `evaluate()` call.

## Components

### 1. PreTradeChecks (stateless checks)

Fast O(1) checks run on every trade:

| Check | Env Variable | Default | Behavior |
|-------|-------------|---------|----------|
| Watchlist freshness | — | — | Reject if `watchlist_valid_until_unix_ms ≤ now` |
| Stale signal | `RISK_STALE_SIGNAL_MS` | 60,000 ms | Reject if signal age > threshold |
| Slippage bound | `RISK_MAX_SLIPPAGE_BPS` | 50 bps | Reject if slippage > max |
| Per-market cap | `RISK_PER_MARKET_CAP_USD` | $5,000 | Reject if market exposure + trade > cap |
| Global cap | `RISK_GLOBAL_CAP_USD` | $50,000 | Reject if total exposure + trade > cap |

### 2. CircuitBreaker

State machine that prevents cascading failures:

```
Closed → Open (on trip) → HalfOpen (after timeout) → Closed (on success)
                                                    → Open (on failure)
```

**Configuration:**

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `RISK_CB_CONSECUTIVE_FAILS` | 5 | Consecutive failures to trip |
| `RISK_CB_FAILURE_RATE` | 0.5 | Failure rate threshold (0.0–1.0) |
| `RISK_CB_WINDOW_SECS` | 60 | Sliding window duration |

**Backoff tiers (Open duration):** 30s → 5m → 30m → 1h

After a HalfOpen probe succeeds, the breaker resets to Closed and the backoff tier resets to 0.

### 3. DeadManSwitch

Blocks new trades if no operator heartbeat is received within the timeout.

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `DEAD_MAN_TIMEOUT_MS` | 300,000 (5 min) | Max time without heartbeat |

**Behavior:**
- On startup: initial heartbeat set to `now()` (grace period).
- Operator sends heartbeats via `dl.v1.control.heartbeat` bus topic.
- If `now - last_heartbeat > timeout` → all new trades blocked.

### 4. DailyBudget

Tracks cumulative losses and gas costs within a UTC day.

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `RISK_DAILY_BUDGET_USD` | $100,000 | Maximum daily loss cap |

**Behavior:**
- Resets at UTC midnight (checked on every evaluate call).
- Tracks: realized P&L losses + gas expenditure.
- Positive P&L (profits) do NOT reduce the spent counter (conservative).
- State persisted to Redis: `HSET risk:daily:{date} pnl_usd ... gas_usd ...`.

**Emergency reset:**
```bash
# Connect to Redis and clear today's budget
redis-cli DEL risk:daily:$(date -u +%Y-%m-%d)
# Then restart sniper-risk service
```

### 5. PositionStore

Real-time position tracking via concurrent DashMap.

- Updated on `dl.v1.exec.filled` (increase position) and `dl.v1.exec.reverted` (revert pending).
- Exposes `get(market)`, `total_exposure_usd()`, `market_exposure_usd(market)`.
- Snapshot persisted to Redis every 30s for crash recovery.

## Metrics

All metrics are exported via Prometheus scrape endpoint.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `risk_checks_total` | Counter | `outcome` | Trade evaluation outcomes |
| `circuit_breaker_state` | Gauge | — | 0=closed, 1=halfopen, 2=open |
| `deadman_seconds_since_heartbeat` | Gauge | — | Seconds since last heartbeat |
| `daily_budget_spent_usd` | Gauge | — | Spent today (micro-USD) |
| `daily_budget_remaining_usd` | Gauge | — | Remaining budget (micro-USD) |
| `open_positions_count` | Gauge | — | Number of open positions |
| `open_positions_exposure_usd` | Gauge | — | Total USD exposure |

**`risk_checks_total` outcome labels:**
`accepted`, `position_cap`, `slippage`, `stale`, `budget`, `cb_open`, `deadman`

## Integration with sniper-executor (P2-B)

```rust
use sniper_risk::{RiskGuard, RiskLimits, TradeIntent};

// Initialize
let limits = RiskLimits::from_env();
let now_ms = /* wall clock */ ;
let guard = RiskGuard::new(limits, now_ms);

// Before each trade
match guard.evaluate(&intent) {
    Ok(()) => { /* proceed with broadcast */ }
    Err(e) => {
        tracing::warn!(%e, "trade blocked by risk engine");
        sniper_risk::record_risk_outcome(&Err(e));
    }
}

// After execution results
guard.record_fill(&fill);
guard.record_revert(&revert);

// Periodic heartbeat from operator
guard.record_heartbeat(ts_ms);

// Emit metrics (call periodically, e.g. every 5s)
guard.emit_metrics(now_ms);
```

## Interpreting Circuit Breaker State in Dashboard

| State | Metric Value | UI Indicator | Action Required |
|-------|-------------|-------------|-----------------|
| Closed | 0 | Green | Normal operation |
| HalfOpen | 1 | Yellow | Probe trade in progress; monitor |
| Open | 2 | Red | All trades blocked; investigate root cause |

When the circuit breaker is Open:
1. Check `risk_checks_total{outcome="cb_open"}` rate.
2. Review recent `ExecReverted` events for the failure reason.
3. If the underlying issue is resolved, the breaker will auto-recover after the current backoff timeout expires and a successful HalfOpen probe completes.
4. For immediate recovery: restart the sniper-risk service (resets breaker state to Closed).
