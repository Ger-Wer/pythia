# Pythia 🔮

Autonomous prediction market intelligence system.

Pythia monitors Polymarket daily, generates probability forecasts using
LLM-powered research and analysis, tracks accuracy with Brier scores,
and reports results via Telegram.

## Architecture

```text
Polymarket Gamma API → Market Selection → [Dexter Research | Naked R1 Baseline]
→ DeepSeek R1 Synthesis → SQLite → Brier Score Tracking → Telegram Reports
```

## Quick Start

```bash
# 1. Clone
git clone git@github.com:YOUR_USER/pythia.git && cd pythia

# 2. Setup
bash scripts/setup.sh

# 3. Configure
cp .env.example .env && nano .env  # add API keys

# 4. Day 0 check — verify market availability
bash scripts/day0_check.sh

# 5. Test run
bun run src/main.ts

# 6. Install cron (edit paths first)
nano scripts/crontab.txt  # update paths
crontab scripts/crontab.txt
```

## Decision Framework

After 4 weeks (~50 resolved predictions), run:

```bash
bun run check-edge
```

- `GO` → Proceed to Phase 2 (multi-agent debate)
- `PIVOT` → Iterate prompts, adjust filters
- `STOP` → Approach doesn't work. Total cost: ~$40.

## Cost

Phase 1: ~$13/month (VPS $5.60 + API ~$7.20)
