# Pythia v2

A narrow, quantitatively-grounded tracker for **macro/economic prediction markets**
(FOMC rate decisions, GDP, CPI) on Polymarket. The edge hypothesis: the gap between
professional nowcasts / fed funds futures (ZQ) and Polymarket prices is systematically
tradeable. The moat is a public, on-chain-verifiable track record.

> **Canonical strategy & roadmap:** [`docs/PYTHIA_V2_STRATEGY.md`](docs/PYTHIA_V2_STRATEGY.md) — read first.

## Status

Staged go/no-go (each stage is a gate):

| Stage | What | Status |
|---|---|---|
| 0 | Backtest FOMC/ZQ edge | ✅ done — `python/backtest/run_backtest.py` |
| 1 | Live paper track (daily snapshots + weekly edge analysis) | 🔄 running — `live_tracker.py`, `analyze_snapshots.py` |
| 2 | Real on-chain wallet via OÜ (legal path confirmed) | 🟡 ready — 2-3 weeks setup |
| 3 | Audience & ads on top of a proven record | ⏸ pending |

## What's in this repo

```
python/backtest/
├── live_tracker.py        # daily FOMC snapshot + FRED baseline (cron: 08:00 UTC)
├── analyze_snapshots.py   # weekly edge detection: drift/volatility/spread
├── fred_data.py           # FRED API loader (fed funds rate)
├── fedwatch.py            # ZQ futures → implied probabilities
├── fetch_polymarket.py    # Gamma API market fetcher
├── run_backtest.py        # Stage 0 backtest on resolved markets
└── data/macro_markets.json
docs/PYTHIA_V2_STRATEGY.md # canonical strategy, audit, jurisdiction DD, roadmap
```

Only runtime dependency: `requests`. No database, no LLM, no dashboard.

## Security

- Container runs as **non-root user** (`pythia`, UID 1002)
- **Read-only filesystem** with tmpfs `/tmp`
- `.env` protected: `chmod 600`, owned by `pythia:pythia`
- Resource limits: `mem_limit: 512m`, `cpus: 0.5`, `pids_limit: 128`
- OWASP Secure Coding Practices audit passed (commit `21a86c1`)

## Legal (Stage 2)

Trading via **Estonian OÜ** is the confirmed legal path (legal opinion obtained 2026-07-05):
- 0% corporate tax on undistributed profit; losses are deductible
- Individual trading = 22% gross-gain tax without loss offset — **not viable**
- Red lines: no third-party funds, no affiliate links
- Details: `docs/ESTONIA_LEGAL_OPINION.md`

## Run locally

```bash
pip install -r python/requirements.txt
export FRED_API_KEY=...        # optional but recommended; get one free from FRED
python3 python/backtest/live_tracker.py
```

## Run in Docker (production, on `amper`)

```bash
docker compose build
docker compose up -d
docker compose logs -f pythia-v2
```

One container (`pythia-v2`) runs `supercronic`, which fires:
- `0 8 * * *`  — daily snapshot
- `0 9 * * 0`  — weekly edge analysis (Sundays 09:00 UTC)

Snapshots land in `python/backtest/snapshots/`, reports in `python/backtest/reports/`.
Both are bind-mounted volumes, so state survives container rebuilds.

## v1 decommission

The previous Pythia Capital oracle channel (LLM-driven Polymarket "desk calls" to
Telegram) was retired on 2026-07-04. Its code is preserved on the `archive/v1-final`
git branch. Do not revive it — see the strategy doc for why.
