# 02 — Components (C4 Level 3)

## 2.1. `pythia-core` (Slow Loop)

| Компонент | Отв. | Файлы (целевые) |
|-----------|------|-----------------|
| `MarketPoller` | Rate-limited Gamma API poller (token bucket, 5 req/s) | `src/services/market_selector.ts` (существует) |
| `ContextBuilder` | Агрегирует Exa/Tavily результаты + on-chain историю рынка | `src/services/research_runner.ts` (существует) |
| `LlmPipeline` | `Retriever → Prompter → DeepSeek → Validator(JSON schema) → Scorer` | `src/services/naked_r1.ts`, `src/prompts/*` |
| `SignalEngine` | Рассчитывает `Edge = P_fundamental − P_market`, confidence band, expected value | `src/services/decision_core.ts` (существует) |
| `WatchlistPublisher` | Публикует в `watchlist.upsert` только рынки с `|Edge| > θ` и `liquidity > L_min` | **новый** `src/services/watchlist_publisher.ts`, Phase 1 |
| `PaperPortfolio` | Хранит гипотетические позиции, расчёт hit-rate, Brier score | `src/services/decision_core.ts` (существует, shadow positions) |

## 2.2. `sniper-ingest` (Fast Loop)

| Компонент | Отв. | Файлы | Phase 2 status |
|-----------|------|-------|----------------|
| `MempoolSubscriber` | WS (Anvil, dev/CI) + IPC (Bor/Erigon, staging/prod) mempool stream with auto-reconnect | `services/sniper-ingest/src/mempool.rs` | **implemented** |
| `OracleDecoder` | ABI-декодер: Polymarket CTF Exchange, UMA Oracle V3, Chainlink Aggregator via `alloy_sol_types::sol!` | `services/common/src/abi/` | **implemented** |
| `OrderBookTracker` | In-memory L2 стакан для каждого рынка из watchlist; hot path — zero-alloc ring buffer | `services/sniper-ingest/src/orderbook.rs` | Phase 2-D |
| `TriggerMatcher` | Hot-path matcher: decoded event × watchlist × TriggerPolicy → `dl.v1.triggers.fired` | `services/sniper-ingest/src/matcher.rs` | **implemented** |
| `WatchlistConsumer` | Подписчик на `watchlist.upsert`/`remove`, hot-reload без рестарта | `services/sniper-ingest/src/watchlist.rs` | **implemented** (Phase 1) |
| `ReplayHarness` | Deterministic replay of mempool dumps through the matcher | `services/sniper-ingest/src/bin/replay.rs` | **implemented** |

## 2.3. `sniper-executor`

| Компонент | Отв. | Файлы |
|-----------|------|-------|
| `NonceManager` | Per-account nonce, reservation pool для RBF | `services/sniper-executor/src/nonce.rs` |
| `TxBuilder` | Pre-signed template transactions (EIP-1559), только финальные поля апдейтятся в hot path | `services/sniper-executor/src/tx_builder.rs` |
| `PrivateRelay` | Клиент bloXroute / Merkle / Manifold; fallback на public mempool | `services/sniper-executor/src/relay.rs` |
| `Broadcaster` | Многопутевая рассылка с racing (fastest-wins), метрики latency per-relay | `services/sniper-executor/src/broadcaster.rs` |
| `KillSwitch` | Атомарный `AtomicBool`, проверяемый ДО каждого broadcast; переводит executor в `DRAIN` state | `services/sniper-executor/src/kill.rs` |
| `FillListener` | Слушает receipts, публикует `exec.filled` / `exec.reverted` | `services/sniper-executor/src/fills.rs` |

## 2.4. `sniper-risk`

| Компонент | Отв. | Файлы |
|-----------|------|-------|
| `PreTradeChecks` | Position caps (per-market, per-account, global), slippage max, stale watchlist | `services/sniper-risk/src/pretrade.rs` |
| `CircuitBreaker` | Global halt при realized loss > threshold за окно N минут | `services/sniper-risk/src/circuit.rs` |
| `DailyBudget` | Трекинг суточного notional и gas spend | `services/sniper-risk/src/budget.rs` |
| `DeadManSwitch` | Если `operator_last_heartbeat > 30s` → block new triggers | `services/sniper-risk/src/deadman.rs` |

## 2.5. `command-center` (Operator UI)

| Виджет | Данные | Update rate |
|--------|--------|-------------|
| Status Bar | `telem.ping`, `telem.mempool`, WS heartbeat | 250ms |
| Triage Queue | `signals.pending` → WS `ui.signals` | on push |
| Active Sniper Watchlist | NATS `telem.ob.<market>`, `triggers.*` | 50–100ms |
| Edge Heatmap | Pythia digest snapshot | 1s |
| Execution Panel | REST + WS confirm | sync |
| Portfolio Panel | Pythia aggregate | 1s |
| Alert Toasts | `triggers.*`, `exec.*` | on push |

См. детальную спецификацию в [`05-command-center-ui.md`](./05-command-center-ui.md).

## 2.6. `gateway-api`

| Компонент | Отв. |
|-----------|------|
| `AuthMiddleware` | PASETO-токены, RBAC (operator/admin/viewer) |
| `RestRouter` | Mutations (Accept/Reject/Arm/Kill) с idempotency-key |
| `WssHub` | Persistent WSS `/ws/v1`, topic multiplexing, JSON-Patch дельты + snapshot recovery |
| `BusBridge` | Redis Streams consumer + publisher, mapping bus ↔ WS topics |
| `AuditLog` | Append-only лог всех операторских действий с hash-chain |
