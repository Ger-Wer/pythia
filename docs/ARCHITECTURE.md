# Pythia Capital — Architecture

## Overview

Pythia Capital is a prediction market research desk that publishes daily AI-generated calls on Polymarket markets to a public Telegram channel. Each call is tracked in a virtual portfolio with transparent ROI reporting.

## Pipeline

```
1. Market Selection (06:00 UTC)
   └── selectMarkets() — fetch active Polymarket markets, filter by volume/liquidity

2. Research (per market)
   ├── multiSearch() — Brave → SearXNG → DuckDuckGo cascade
   └── buildResearchBrief() — LLM generates structured brief (DeepSeek V4-Flash)

3. Prediction (per market, parallel)
   ├── nakedR1Predict() — baseline probability (DeepSeek V4-Flash)
   ├── runSynthesis() — research + R1 synthesis (DeepSeek V4-Flash)
   └── predictDualLLM() — ensemble (DeepSeek V4-Pro + Mistral Small)
       ├── ensemble_p = weighted average (60% DS + 40% Mistral)
       └── disagreement = |DS - Mistral| → confidence level

4. Storage
   └── insertPrediction() — saves all probabilities + reasoning to SQLite

5. Publishing (5 slots/day: 07:00, 11:00, 15:00, 19:00, 23:00 UTC)
   ├── get_recent_signals() — pull predictions from last 24h
   ├── filter_markets() — edge ≥ 8pp, volume ≥ $500k, days 3-45
   ├── select_top_signals(n=5) — score by edge × confidence × volume_weight
   ├── generate_prophecy_llm() — LLM formats English reasoning (Mistral Small)
   ├── send_to_channel() — publish to Telegram
   ├── save_prophecy_record() — audit trail in oracle_prophecies
   └── open_position() — virtual portfolio with Kelly stake sizing

6. Resolution (19:00 UTC daily)
   ├── get_pending_prophecies() — find markets past close date
   ├── get_market_outcome() — check Polymarket resolution
   ├── update_prophecy_result() — WIN/LOSS + Brier score
   ├── close_position() — realized PnL
   └── edit_telegram_post() — update original post with result

7. Portfolio Update (00:30 UTC)
   ├── mark_to_market() — update unrealized PnL
   └── update_pinned_message() — refresh pinned portfolio summary

8. Weekly Scorecard (Sun 10:00 UTC)
   └── publish_weekly_scorecard() — 7-day + 30-day performance report
```

## LLM Stack

| Stage | Model | Purpose |
|-------|-------|---------|
| Research | DeepSeek V4-Flash | Generate research brief from search results |
| Naked R1 | DeepSeek V4-Flash | Baseline probability estimate |
| Synthesis | DeepSeek V4-Flash | Research + R1 synthesis with confidence |
| Prediction A | DeepSeek V4-Pro | High-quality probability estimate |
| Prediction B | Mistral Small 4 | Second opinion for ensemble |
| Post formatting | Mistral Small 4 | English quant-desk reasoning text |

## Virtual Portfolio

- **Starting AUM:** $1,000 (configurable)
- **Stake sizing:** 0.25 fractional Kelly (quarter Kelly, conservative)
- **Max position:** 5% of AUM
- **Tracking:** entry price → exit price → realized PnL
- **Pinned message:** updated daily with AUM, return, win rate, best/worst calls

## Search Stack

- **Brave Search API** (primary, free 2k req/month)
- **SearXNG** (secondary, self-hosted in Docker, aggregates Google/Bing/DDG)
- **DuckDuckGo Instant Answer** (emergency fallback, no key needed)
- URL deduplication across all providers
