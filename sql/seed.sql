-- Minimal seed data for the Pythia Postgres shadow store (dev only).
-- Phase 1 (Dual-Loop Track D). Mirrors the default books inserted at the
-- end of sql/decision_core.sql — keeps parity with SQLite bootstrap.

INSERT INTO books (
    name, mode, currency, starting_balance, current_balance,
    max_risk_per_trade_pct, max_total_exposure_pct, status,
    created_at, updated_at
) VALUES
    ('shadow_default',   'shadow',   'USDC', 10000, 10000, 0.02, 0.20, 'active',
     NOW()::text, NOW()::text),
    ('operator_default', 'operator', 'USDC', 10000, 10000, 0.02, 0.20, 'active',
     NOW()::text, NOW()::text)
ON CONFLICT (name) DO NOTHING;
