# Pythia Architecture

## Components
- **Market Selector**: Gamma REST API → filter binary, $5K-$50K, 3-14 days
- **Dexter Research**: virattt/dexter agent → structured research brief
- **Naked R1**: Baseline single-call DeepSeek R1 (permanent control group)
- **Synthesis**: Calibrated prompt → DeepSeek R1 → probability estimate
- **Tracker**: SQLite, Brier scores, cost tracking
- **Alerts**: Telegram Bot — edge alerts, errors, weekly reports

## Design Decisions
1. No MiroFish — too complex, wrong domain (social media sim)
2. No GraphRAG — overkill for 5-20 docs per market
3. No Zep — overkill for single-session forecasting
4. No local GPU — API is 2.5x cheaper at current volume
5. A/B test (Dexter vs Naked R1) — permanent baseline comparison
6. SQLite over PostgreSQL — no concurrent writes, simpler backup
