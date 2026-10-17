# Pythia v2

A narrow, quantitatively-grounded tracker for **macro/economic prediction markets**
(FOMC rate decisions, GDP, CPI) on Polymarket. The edge hypothesis: the gap between
professional nowcasts / fed funds futures (ZQ) and Polymarket prices is systematically
tradeable. The moat is a public, on-chain-verifiable track record.

> **Canonical strategy & roadmap:** [`docs/PYTHIA_V2_STRATEGY.md`](docs/PYTHIA_V2_STRATEGY.md) — read first.

## Status — 2026-10-17

Owner decision: **paper trading and the existing Telegram channel are tested
together**. No real orders or funds. The complete new paper cycle and its limits
are described in [PAPER_CHANNEL_PILOT.md](docs/PAPER_CHANNEL_PILOT.md).

Staged go/no-go (each stage is a gate):

| Stage | What | Status |
|---|---|---|
| 0 | Historical backtest | Open: the earlier empty dataset did not establish an edge |
| 1 | Prospective paper experiment | New `paper_cycle.py`: EFFR/ZQ, immutable ledger, asks/depth/fees/risk limits, outcomes and PAPER posts |
| 2 | Real funds | Blocked; requires a separate owner decision and current legal/tax review |
| Channel | Existing Telegram | Tested alongside paper, explicitly labelled PAPER; no claim of profitable forecasts |

## What's in this repo

```
python/evaluation/
├── live_tracker.py        # daily snapshot: Polymarket + Kalshi + FRED + ZQ futures
├── analyze_snapshots.py   # weekly analysis: edge-diff v2 verdict (authoritative) + v1 (deprecated, informational) + drift/volatility
├── edge_diff.py           # v1 test: front-month ZQ vs Kalshi (DEPRECATED 2026-09-16: cut leg saturates 68/68)
├── edge_diff_v2.py        # v2 test: per-meeting ZQ contract vs Kalshi (authoritative; Sep-2026 verdict SYSTEMATIC_DIVERGENCE, n=49)
├── edge_diff_diag.py      # Step-A diagnostic: saturation + front-month roll analysis behind the v1 verdict
├── zq_contracts.py        # per-month ZQ contracts (Yahoo .CBT, free): symbols, CME day-weighted rate extraction, fixture loader + live fetch
├── fedwatch.py            # ZQ futures → implied probabilities + get_live_implied_rate()
├── gdpnow.py              # Atlanta Fed GDPNow RSS parser (feedparser)
├── cpi_nowcast.py         # Cleveland Fed CPI/PCE nowcast JSON parser
├── zq_futures.py          # Yahoo Finance ZQ=F + Barchart fallback
├── fetch_polymarket.py    # Polymarket Gamma API (keyword + slug fallback for FOMC)
├── fetch_kalshi.py        # Kalshi KXFEDDECISION meeting-specific FOMC markets
├── run_backtest.py        # Stage 0 backtest on resolved markets
├── healthcheck.py         # daily health check: snapshot freshness, FRED baselines, market count
├── tests/                 # pytest suite: 37 tests (edge_diff, edge_diff_v2, zq_contracts, gdpnow, cpi_nowcast, zq_futures, fetch_kalshi)
│   └── fixtures/          # committed test fixtures (yahoo_zq.json, kalshi_markets.json, zq_monthly_sep2026.json, ...)
└── data/                  # runtime caches (gitignored)
docs/PYTHIA_V2_STRATEGY.md # canonical strategy, audit, jurisdiction DD, roadmap
```

### Data sources (current snapshot: ~132 markets)

| Bucket | Source | Type | Count |
|---|---|---|---|
| `fomc` | Polymarket Gamma (keyword search) | cumulative/general ("Will X cuts happen in 2026?") | ~24 |
| `gdp` | Polymarket Gamma (keyword search) | GDP / recession | ~43 |
| `kalshi_fomc` | **Kalshi KXFEDDECISION** | **meeting-specific binary strikes** ("Fed Hike 0bps at July 2026?") | ~65 |

Kalshi is the meeting-specific source that satisfies the corpus gate (Polymarket only offers
cumulative "Will X cuts happen in 2026?" markets, not per-meeting markets). The real Stage 1
hypothesis test is `edge_diff.py` (commit `9f1af85`): it compares ZQ-implied cut/hike/hold
probabilities against Kalshi bucket prices of the nearest upcoming FOMC meeting, day by day.
Per-market drift/volatility signals in the weekly report are informational only.

Runtime dependencies: `requests`, `feedparser`, `beautifulsoup4`.
Dev dependencies: `pytest>=8.0` (see `python/requirements-dev.txt`).

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

## Run and test — amper only

Local project execution and testing are prohibited by `PYTHIA_CONSTITUTION.md`.
The following commands belong in the server checkout, reached by `ssh pythia@amper`.

```bash
pip install -r python/requirements.txt
export FRED_API_KEY=...        # optional but recommended; get one free from FRED
python3 python/evaluation/live_tracker.py
```

Run tests:

```bash
pip install -r python/requirements-dev.txt
pytest python/evaluation/tests/ -v
```

## Run in Docker (production, on `amper`)

```bash
docker compose build
docker compose up -d
docker compose logs -f pythia-v2
```

One container (`pythia-v2`) runs `supercronic`, which fires:
- `0 8 * * *`  — daily snapshot (FOMC + GDP + CPI + ZQ futures)
- `15 8 * * *` — paper cycle + existing channel (explicit opt-in; see pilot doc)
- `0 9 * * 0`  — weekly edge analysis (Sundays 09:00 UTC)
- `0 20 * * *` — daily health check (snapshot freshness, FRED baselines, market count)

Snapshots land in `python/evaluation/snapshots/`, reports in `python/evaluation/reports/`.
Both are bind-mounted volumes, so state survives container rebuilds. Paper ledger,
outbox and last completed-work report live in the existing data mount at
`python/evaluation/data/paper/`; they are ignored by Git and survive rebuilds.

## Paper data and accounting safeguards

`paper_sources.py` uses the current effective daily EFFR, not a monthly FEDFUNDS
average. Future/stale sources and unsupported model conditions block forecasts.
The conditional 25bp model is a hypothesis, not an independently calibrated
distribution. The first allowlisted event is October FOMC; adding CPI/GDP/AI
requires its own validated model and market mapping.

`paper_ledger.py` records prospective forecasts, simulated fills and outcomes
append-only. PnL includes fees; equity for open positions is cost-based, not marked
to market. Brier counts first forecasts and reports independent completed events.
`paper_telegram.py` keeps a durable outbox: confirmed posts are not resent, uncertain
delivery needs reconciliation, expired forecast windows are not published.

Weekly legacy analysis no longer silently reads a September fixture. Explicit
`--historical-fixture` is a historical-only mode; legacy snapshots lacking daily
EFFR and provider timestamps are unavailable for current validated analysis.

## v1 decommission

The previous Pythia Capital oracle channel (LLM-driven Polymarket "desk calls" to
Telegram) was retired on 2026-07-04. Its code is preserved on the `archive/v1-final`
git branch. Do not revive it — see the strategy doc for why.
