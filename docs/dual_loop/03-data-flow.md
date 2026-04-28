# 03 — Data Flow (End-to-End)

## Сквозной сценарий: от обнаружения рынка до исполнения

```
 T+0ms     Gamma poll → new market discovered
 T+2s      ContextBuilder fetches Exa/news
 T+10s     LLM inference → P_fundamental = 0.72
 T+10.1s   SignalEngine: P_market=0.55, Edge=+0.17, EV=+4.2%
 T+10.2s   Signal published → dl.v1.signals.emitted
 T+10.3s   Operator sees card in Triage Queue
 T+12s     Operator hits [A] Accept (hotkey)
 T+12.01s  dl.v1.watchlist.upsert → Sniper loads market, subscribes to CTF contract
 T+12.02s  Sniper streams OB depth to Command Center via WS
 ...
 T+Nms     Oracle tx appears in mempool (resolution pending)
 T+N+2ms   OracleDecoder matches payload → Trigger emitted
 T+N+3ms   Risk checks pass → TxBuilder assembles signed tx
 T+N+5ms   Broadcast via private relay
 T+N+800ms Tx mined → fill event → exec.filled → Portfolio updated
 T+N+801ms Command Center shows filled state; Pythia scores decision
```

## Фазы потока

### Фаза 1: Discovery & Analysis (Slow Loop, T=0…T+10s)

```
Gamma API ──poll──▶ MarketPoller ──▶ ContextBuilder ──▶ LlmPipeline
                                                            │
                                                            ▼
                                                      SignalEngine
                                                            │
                                                            ▼
                                                     dl.v1.signals.emitted
```

- Poll-интервал: 5 минут для новых рынков, 15 мин для существующих.
- Rate limit: 5 req/s, token bucket, backoff при 429.
- LLM inference: batched, до 10 рынков в одном prompt-e (для экономии стоимости).

### Фаза 2: Operator Triage (Human-in-the-Loop, T+10s…T+60s)

```
dl.v1.signals.emitted ──▶ gateway-api ──WSS──▶ Command Center (Triage Queue)
                                                │
                                    [A] Accept / [R] Reject / [S] Snooze
                                                │
                                                ▼
                                        dl.v1.signals.decisions
                                                │
                                ┌───────────────┴───────────────┐
                                ▼                               ▼
                          pythia-core                      sniper-ingest
                          (записывает                      (если Accept:
                           decision в Postgres)             публикует в
                                                            dl.v1.watchlist.upsert)
```

- Idempotency-key на `dl.v1.signals.decisions` предотвращает double-accept.
- `Reject` пишет причину в decision journal для будущего retraining промптов.
- `Snooze` возвращает карту в очередь через N минут (конфигурируемо, default 15m).

### Фаза 3: Armed & Watching (Fast Loop, T+12s…T+Nms)

```
dl.v1.watchlist.upsert ──▶ sniper-ingest
                         │
                         ├──▶ OrderBookTracker (REST warm-up + WS incremental)
                         ├──▶ OracleDecoder.register(ctf_address)
                         └──▶ telem.ob.<market> ──▶ NATS ──▶ Command Center
                                                               (Active Watchlist)
```

- Watchlist entry имеет TTL (конфиг, default 24h).
- Sniper держит до 150 одновременных рынков (hard cap по Phase 4 целям).
- Stale entries auto-expire и публикуются `dl.v1.watchlist.remove`.

### Фаза 4: Trigger & Risk (Hot Path, T+N…T+N+3ms)

```
Polygon Node ──IPC──▶ MempoolSubscriber ──▶ OracleDecoder
                                                │
                                                ▼
                                          TriggerMatcher
                                                │
                                      (∈ watchlist? slippage ok?)
                                                │
                                                ▼
                                          sniper-risk
                                      (caps, budget, dead-man)
                                                │
                                         ┌──────┴──────┐
                                       PASS          FAIL
                                         │              │
                                         ▼              ▼
                                   dl.v1.triggers.fired
```

- **Hot path budget: 3ms.** Zero-alloc, no-lock-contention.
- Все checks в sniper-risk — O(1), pre-computed lookup tables.
- blocked/rejected trigger paths остаются audit-only и не должны расширять hot path.

### Фаза 5: Execution (Hot Path, T+N+3ms…T+N+5ms)

```
dl.v1.triggers.fired ──▶ sniper-executor
                         │
                         ├──▶ NonceManager.reserve()
                         ├──▶ TxBuilder.finalize(template, nonce, gas, data)
                         ├──▶ KillSwitch.check() ── if engaged → abort
                         └──▶ Broadcaster
                                  │
                           ┌──────┼──────┐
                           ▼      ▼      ▼
                         relayA  relayB  public (fallback)
                           │      │      │
                           └──────┬──────┘
                                  ▼
                            exec.submitted
```

- Pre-signed template: все поля кроме `nonce`, `maxFeePerGas`, `data` — pre-computed при Accept.
- Racing: первый relay, подтвердивший приём — выигрывает. Остальные cancel'ятся (same-nonce cancel tx).

### Фаза 6: Fill & Feedback (T+N+5ms…T+N+800ms)

```
Polygon Node ──block──▶ FillListener
                             │
                             ▼
                    exec.filled / exec.reverted
                             │
                  ┌──────────┴──────────┐
                  ▼                     ▼
            pythia-core            Command Center
            (Portfolio update,     (UI update, toast)
             decision scoring)
                  │
                  ▼
            Daily recon vs paper-mode baseline
```

## Back-pressure и failure modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Redis lag > 500 msg | Prometheus `redis_stream_lag` | Pythia сужает watchlist (повышает θ) |
| Polygon node stale (>200ms heartbeat miss) | sniper-ingest health probe | Auto-failover на secondary node |
| Operator idle > 30s | Gateway heartbeat | sniper-risk блокирует new triggers (dead-man) |
| Private relay timeout | Broadcaster | Fallback на public mempool с warning toast |
| DB write lag | Postgres `pg_stat_activity` | Pythia deferrable tasks, critical writes only |
| LLM API errors | `pythia-llm` circuit breaker | Тротлинг новых сигналов, paper-only fallback |

## Observability-срез для каждой фазы

| Фаза | Ключевые метрики | Дашборд |
|------|------------------|---------|
| Discovery | `gamma_poll_rps`, `llm_inference_p95_ms` | "Pythia Pipeline" |
| Triage | `signals_pending_count`, `triage_decision_latency_p50_s` | "Operator Activity" |
| Watching | `watchlist_size`, `ob_update_rate`, `ob_staleness_ms` | "Sniper Health" |
| Trigger | `mempool_tps`, `trigger_match_p99_us`, `risk_reject_rate` | "Hot Path" |
| Execution | `broadcast_p99_ms`, `relay_success_rate`, `fill_rate` | "Executor" |
| Fill | `realized_pnl_usd`, `brier_score`, `paper_vs_live_delta` | "P&L" |
