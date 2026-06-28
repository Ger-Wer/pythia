# Pythia Capital

**Prediction market research desk for Polymarket.**
Publishes daily data-driven calls to a public Telegram channel with a transparent virtual portfolio.

---

## What it does

1. **Forecast pipeline** — scans active Polymarket markets, runs multi-search (Brave/SearXNG/DuckDuckGo), generates research briefs via LLM, produces probability estimates
2. **Forecast: Mistral Small** — free LLM for naked R1, synthesis, ensemble
3. **Prophecy: DeepSeek V4 Pro** — paid LLM for final oracle picks (resolving old markets)
4. **Virtual portfolio** — every published pick opens a virtual position with Kelly stake sizing, tracks ROI on resolution
5. **Telegram channel** — 5 daily publishing slots, English quant-desk format, pinned portfolio summary
6. **Score tracking** — automatic resolution detection, Brier score calculation, weekly scorecard

---

## Architecture

```
Forecast Pipeline (TypeScript/Bun) — Mistral only (free)
├── market_selector.ts     — select active markets
├── research_runner.ts     — multi-search (Brave/SearXNG/DDG)
├── naked_r1.ts            — baseline probability estimate
├── ensemble_predictor.ts  — weighted ensemble
├── synthesis.ts           — research + R1 synthesis
└── main.ts                — orchestrates pipeline

Oracle Layer (Python) — DeepSeek V4 Pro
├── oracle_publisher.py    — selects top-N picks, formats & publishes
├── portfolio_manager.py   — Kelly sizing, ROI tracking, pinned message
├── oracle_score_updater.py— resolution tracking, position closing
├── oracle_scorecard.py    — weekly performance report
├── oracle_backfill.py     — historic archive creation
├── market_categorizer.py  — sports subcategories (NBA, NFL, soccer, etc.)
├── llm_stats.py           — Brier score per LLM (internal)
└── llm_stats_publisher.py — stats publisher (internal)

Stats Bot (Python)
├── telegram_stats_bot.py  — /stats, /weekly commands

Infrastructure
├── Docker Compose         — scheduler, telegram bot, dashboard, searxng
├── SearXNG                — self-hosted meta-search (free)
└── SQLite                 — predictions, oracle_prophecies, portfolio_positions
```

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- API keys: DeepSeek, Mistral, Telegram Bot

### Configuration
```bash
cp .env.example .env
# Edit .env with your keys:
# - MISTRAL_API_KEY (required, forecast pipeline — free)
# - DEEPSEEK_API_KEY (required, oracle prophecy — paid)
# - ORACLE_TELEGRAM_BOT_TOKEN (required, for channel publishing)
# - ORACLE_TELEGRAM_CHANNEL_ID (required, channel ID)
# - BRAVE_SEARCH_API_KEY (optional, free 2k req/month)
# - SEARXNG_BASE_URL (set automatically in Docker)
```

### Local Development
```bash
npm install
cd python && pip install -r requirements.txt

# Run forecast pipeline
npm run dev

# Run oracle publisher (dry run)
python python/oracle_publisher.py --dry-run

# Run backfill
python python/oracle_backfill.py --dry-run --limit 5
```

### Production Deployment
```bash
# On server (amper):
cd /home/pythia/pythia
git pull origin main
docker compose build
docker compose up -d --force-recreate
```

---

## Cron Schedule

| Time (UTC) | Command | Description |
|------------|---------|-------------|
| 06:00 | `forecast` | Daily forecast run |
| 07:00 | `oracle-publish` | Publish top-5 picks |
| 11:00 | `oracle-publish` | Publish top-5 picks |
| 15:00 | `oracle-publish` | Publish top-5 picks |
| 19:00 | `oracle-publish` | Publish top-5 picks |
| 19:00 | `oracle-update-scores` | Check resolutions, close positions |
| 23:00 | `oracle-publish` | Publish top-5 picks |
| 00:30 | `portfolio-update` | Update pinned portfolio message |
| Sun 10:00 | `oracle-scorecard` | Weekly performance report |

---

## LLM Configuration

| Stage | Model | Cost |
|-------|-------|------|
| Forecast (naked R1, synthesis, ensemble) | Mistral Small (`mistral-small-latest`) | Free |
| Oracle Prophecy | DeepSeek V4 Pro (`deepseek-v4-pro`) | ~$0.005/day |

Forecast pipeline uses Mistral only (free tier). DeepSeek V4 Pro is reserved for oracle prophecy generation (resolving old markets). No operator alerts in forecast — channel is for prophecies only.

---

## Repository Structure

```
pythia/
├── src/                    # TypeScript forecast pipeline
│   ├── services/
│   │   ├── ensemble_predictor.ts  # Weighted ensemble
│   │   ├── research_runner.ts      # Multi-search (Brave/SearXNG/DDG)
│   │   ├── naked_r1.ts            # Baseline prediction
│   │   └── db.ts                  # SQLite + PredictionRow
│   ├── prompts/synthesis.ts       # Synthesis prompt
│   └── main.ts                    # Pipeline orchestrator
├── python/                 # Oracle layer
│   ├── oracle_publisher.py        # Pick selection & publishing (DeepSeek V4 Pro)
│   ├── portfolio_manager.py       # Kelly sizing & ROI
│   ├── oracle_score_updater.py    # Resolution tracking
│   ├── oracle_scorecard.py        # Weekly report
│   ├── oracle_backfill.py         # Historic archive
│   ├── market_categorizer.py      # Sports subcategories
│   ├── llm_stats.py               # Brier score per LLM (internal)
│   ├── llm_stats_publisher.py     # Stats publisher (internal)
│   └── telegram_stats_bot.py      # /stats, /weekly commands
├── dashboard/              # React operator dashboard
├── sql/migrations/         # Database migrations
├── searxng/                # SearXNG config
├── scripts/               # Entrypoint & migrations
├── Dockerfile             # Multi-stage (Bun + Python + Node)
└── docker-compose.yml     # Production stack
```

---

## Database Schema

- **predictions** — forecast results (dexter_p, naked_p, ensemble_p, dual-LLM columns)
- **oracle_prophecies** — published picks with resolution tracking
- **portfolio_positions** — virtual positions with Kelly stakes & realized PnL
- **portfolio_state** — singleton: AUM, cash, total trades, win count

---

## Disclaimer

Pythia Capital publishes virtual portfolio results for research purposes.
All predictions are AI-generated and carry no guarantee of accuracy.
Prediction markets carry risk of financial loss.
Not financial advice.
