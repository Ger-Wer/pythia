# Gate #10 M4: Multi-Market Orderbook Driver - Design Specification

**Date:** 2026-06-26  
**Status:** Approved  
**Milestone:** Gate #10 M4  
**Dependencies:** M1-M3 (sniper-orderbook foundation), watchlist publisher, NATS bus

---

## Overview

The Multi-Market Driver (M4) is the orchestration layer that manages multiple concurrent Polymarket orderbook trackers, one per active market in the watchlist. It bridges the Slow Loop (watchlist updates) and Fast Loop (orderbook tracking), enabling real-time liquidity monitoring across all active markets for sniper-risk pre-trade checks and sniper-executor order placement.

**Core Responsibilities:**
1. Subscribe to watchlist updates (`dl.v1.watchlist.upsert/remove`)
2. Dynamically spawn/shutdown tracker tasks per market
3. Manage resources (memory, CPU, connection pooling)
4. Implement fault tolerance (backoff, circuit breaker, graceful shutdown)
5. Publish orderbook updates to NATS bus for downstream consumers

**Scope:**
- ✅ **In scope:** Core driver logic, backpressure, circuit breaker, graceful shutdown, observability
- ❌ **Out of scope:** Horizontal scaling, worker pools, advanced load balancing (future if needed)

---

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────┐
│ MultiMarketDriver (main coordinator)                    │
│  - watchlist_state: WatchlistState                      │
│  - trackers: HashMap<AssetId, TrackerHandle>            │
│  - resource_monitor: ResourceMonitor                    │
│  - backoff_registry: HashMap<AssetId, BackoffState>     │
│  - config: DriverConfig                                 │
└──────────┬──────────────────────────────────────────────┘
           │
           ├──► SQLite (initial watchlist load)
           ├──► NATS subscriber (dl.v1.watchlist.*)
           └──► Spawns N TrackerTasks ──► NATS publisher (dl.v1.orderbook.*)
```

**Design Approach:** Monolithic driver with one tokio task per tracker. Chosen for:
- Simplicity: single binary, straightforward debugging
- Current scale: 20-30 markets don't require distributed architecture
- Fast MVP: 1-2 days vs 3-5 for supervisor/worker pool
- Future-proof: task-per-tracker architecture ready for horizontal scaling if needed

### Core Components

#### 1. MultiMarketDriver

Main coordinator that owns the tracker pool and orchestrates lifecycle.

```rust
pub struct MultiMarketDriver {
    /// Active tracker handles indexed by asset_id
    trackers: HashMap<AssetId, TrackerHandle>,
    
    /// NATS connection for watchlist subscription
    nats_client: async_nats::Client,
    
    /// Watchlist subscriber (dl.v1.watchlist.*)
    watchlist_sub: NatsSubscription,
    
    /// SQLite connection for initial state load
    db_pool: SqlitePool,
    
    /// Resource monitoring for backpressure
    resource_monitor: ResourceMonitor,
    
    /// Per-tracker backoff/circuit breaker state
    backoff_registry: HashMap<AssetId, BackoffState>,
    
    /// Configuration
    config: DriverConfig,
    
    /// Shutdown coordination
    shutdown_tx: broadcast::Sender<()>,
    shutdown_rx: broadcast::Receiver<()>,
}
```

**Key Methods:**
- `new(config) -> Result<Self>` - Initialize driver, load watchlist from DB, spawn initial trackers
- `run() -> Result<()>` - Main event loop: handle watchlist updates + monitor resources
- `spawn_tracker(asset_id, token_id) -> Result<()>` - Spawn new tracker task
- `shutdown_tracker(asset_id) -> Result<()>` - Graceful shutdown with timeout
- `can_spawn(asset_id) -> CanSpawnResult` - Check backpressure + circuit breaker
- `shutdown() -> Result<()>` - Graceful shutdown all trackers

#### 2. TrackerHandle

Per-market wrapper that manages task lifecycle.

```rust
struct TrackerHandle {
    asset_id: AssetId,
    task: JoinHandle<Result<(), TrackerError>>,
    shutdown_tx: oneshot::Sender<ShutdownSignal>,
    health_rx: watch::Receiver<TrackerHealth>,
}
```

Driver holds `HashMap<AssetId, TrackerHandle>` for O(1) lookup and shutdown.

#### 3. TrackerTask

Async task that wraps existing `sniper_orderbook::Tracker` (from M2).

```rust
pub struct TrackerTask {
    asset_id: AssetId,
    token_id: String,
    tracker: Tracker,  // from M2
    
    /// Polymarket REST client
    rest_client: PolymarketRestClient,
    
    /// Polymarket WS subscriber
    ws_subscriber: PolymarketWsSubscriber,
    
    /// NATS publisher for orderbook updates
    nats_publisher: OrderBookPublisher,
    
    /// Health status reporter (send to driver)
    health_tx: watch::Sender<TrackerHealth>,
    
    /// Shutdown signal receiver
    shutdown_rx: oneshot::Receiver<ShutdownSignal>,
}
```

**Task Lifecycle:**
1. Spawn → connect WS → start buffering
2. Request snapshot via REST
3. Snapshot received → drain buffer → transition to Tracking
4. Loop: WS messages → update Tracker → publish BBO changes to NATS
5. On Degraded → reconnect + re-snapshot (with internal backoff)
6. On shutdown signal → flush pending → close connections

**Health States:**
```rust
pub enum TrackerHealth {
    Tracking,        // fully synced, publishing updates
    Syncing,         // draining buffer after snapshot
    Degraded(String), // sequence gap, reconnecting
    Stopping,        // graceful shutdown in progress
}
```

#### 4. ResourceMonitor

Backpressure controller that monitors system resources.

```rust
pub struct ResourceMonitor {
    memory_threshold_mb: u64,
    cpu_threshold_pct: f64,
    max_trackers: usize,
    current_trackers: Arc<AtomicUsize>, // shared counter
}

pub enum CanSpawnResult {
    Yes,
    BackpressureMemory { current_mb: u64, threshold_mb: u64 },
    BackpressureCpu { current_pct: f64, threshold_pct: f64 },
    BackpressureTrackerLimit { current: usize, max: usize },
}
```

**Monitoring Strategy:**
- Use `sysinfo` crate for memory/CPU metrics
- Sample every 5 seconds (configurable)
- Store last N samples for smoothing (avoid spike-triggered false positives)
- Return `CanSpawn::Yes` only if ALL conditions pass:
  - `current_trackers < max_trackers`
  - `memory_usage < threshold`
  - `cpu_usage < threshold`

#### 5. BackoffState & Circuit Breaker

Per-tracker fault tolerance mechanism.

```rust
pub struct BackoffState {
    failures: u32,
    last_attempt: Instant,
    next_retry: Duration,
    circuit_open: bool,
}
```

**Exponential Backoff:**
- 1s → 2s → 4s → 8s → 16s → 32s (max)
- Resets on success

**Circuit Breaker:**
- Opens after 5 consecutive failures (configurable)
- While open: no spawn attempts for that asset_id
- After `backoff_max_duration` (32s): attempt reset
- One success → closes circuit, resets backoff

---

## Data Flow

### Startup Sequence

```
1. Driver starts
2. Load DriverConfig from environment variables
3. Connect to NATS (nats_url)
4. Connect to SQLite (db_path)
5. Read active markets from watchlist table
6. For each market:
   - Check can_spawn() (should pass on clean startup)
   - Spawn TrackerTask
   - Store TrackerHandle in HashMap
7. Subscribe to NATS topics:
   - dl.v1.watchlist.upsert
   - dl.v1.watchlist.remove
8. Start ResourceMonitor background task (samples every 5s)
9. Enter main event loop
```

### Runtime: Watchlist Upsert

```
1. Receive NATS message: dl.v1.watchlist.upsert
   {
     "asset_id": "123456",
     "token_id": "0xabc...",
     "market_slug": "...",
     ...
   }

2. Check if tracker already exists:
   - Yes → log warning, skip (idempotent)
   - No → proceed

3. Check BackoffState for asset_id:
   - Circuit open? → log warning, skip spawn
   - Can retry now? → proceed
   - Still in backoff? → skip spawn

4. Check ResourceMonitor.can_spawn():
   - CanSpawnResult::Yes → proceed
   - Backpressure* → log warning, queue spawn for later retry

5. Spawn TrackerTask:
   - Create TrackerTask with asset_id, token_id
   - tokio::spawn(task.run())
   - Store TrackerHandle in HashMap
   - Increment resource_monitor.current_trackers

6. On spawn failure:
   - Record failure in BackoffState
   - Log error + emit metric
```

### Runtime: Watchlist Remove

```
1. Receive NATS message: dl.v1.watchlist.remove
   {
     "asset_id": "123456"
   }

2. Lookup TrackerHandle in HashMap:
   - Not found → log warning, skip (idempotent)
   - Found → proceed

3. Send shutdown signal via oneshot channel:
   shutdown_tx.send(ShutdownSignal)

4. Await task completion with timeout (10s):
   - Completed gracefully → proceed
   - Timeout → force abort task (JoinHandle.abort())

5. Cleanup:
   - Remove from trackers HashMap
   - Remove from backoff_registry (clear circuit breaker state)
   - Decrement resource_monitor.current_trackers
   - Log shutdown duration metric
```

### Runtime: TrackerTask Loop

```
1. Connect to Polymarket WS for token_id
2. tracker.on_ws_connected()
3. Request REST snapshot
4. Loop:
   select! {
     // WS message received
     msg = ws_subscriber.next() => {
       tracker.on_ws_message(msg);
       if tracker.state() == Tracking {
         if let Some(update) = tracker.get_bbo_update() {
           nats_publisher.publish(update).await;
         }
       }
     }
     
     // Snapshot received
     snapshot = rest_client.fetch_snapshot() => {
       tracker.on_snapshot_received(snapshot);
       // Drain buffer, transition to Syncing/Tracking
     }
     
     // Shutdown signal
     _ = shutdown_rx => {
       info!("Shutdown signal received, flushing...");
       // Flush pending NATS publishes
       // Close WS connection gracefully
       return Ok(());
     }
     
     // Health check (every 1s)
     _ = interval.tick() => {
       let health = match tracker.state() {
         Tracking => TrackerHealth::Tracking,
         Syncing => TrackerHealth::Syncing,
         Degraded { reason } => TrackerHealth::Degraded(reason),
         Stopped => TrackerHealth::Stopping,
         _ => TrackerHealth::Syncing,
       };
       health_tx.send(health);
     }
   }
   
5. On Degraded state:
   - Close WS connection
   - Internal backoff (1s → 2s → 4s → ...)
   - Reconnect + re-snapshot
   - Loop continues (task does NOT exit)

6. Task only exits on:
   - Shutdown signal (graceful)
   - Unrecoverable error (e.g., token_id invalid)
```

### Graceful Shutdown

```
1. Process receives SIGTERM/SIGINT
2. Driver.shutdown() called:
   - Stop accepting new watchlist updates (close subscriber)
   - Send shutdown signal to ALL trackers (broadcast channel)
   - Await all tasks with global timeout (30s)
   - Force abort stragglers (JoinHandle.abort())
   - Close NATS connections
   - Exit
```

---

## Error Handling & Recovery

### Error Types

```rust
#[derive(Debug, thiserror::Error)]
pub enum DriverError {
    #[error("Failed to connect to NATS: {0}")]
    NatsConnection(#[from] async_nats::ConnectError),
    
    #[error("Failed to load watchlist from database: {0}")]
    DatabaseLoad(#[from] sqlx::Error),
    
    #[error("Watchlist subscription error: {0}")]
    WatchlistSubscription(String),
    
    #[error("Graceful shutdown timeout exceeded")]
    ShutdownTimeout,
}

#[derive(Debug, thiserror::Error)]
pub enum SpawnError {
    #[error("Backpressure: {0:?}")]
    Backpressure(CanSpawnResult),
    
    #[error("Circuit breaker open for asset {0}")]
    CircuitOpen(AssetId),
    
    #[error("Tracker already exists for asset {0}")]
    AlreadyExists(AssetId),
    
    #[error("Failed to spawn tracker task: {0}")]
    TaskSpawn(#[from] tokio::task::JoinError),
}

#[derive(Debug, thiserror::Error)]
pub enum TrackerError {
    #[error("Polymarket WS connection failed: {0}")]
    WebSocketConnection(String),
    
    #[error("Polymarket REST snapshot failed: {0}")]
    SnapshotRequest(String),
    
    #[error("NATS publish failed: {0}")]
    NatsPublish(#[from] async_nats::PublishError),
    
    #[error("Tracker degraded: {0}")]
    Degraded(String),
    
    #[error("Shutdown signal received")]
    ShutdownSignal,
}
```

### Recovery Strategies

**TrackerTask Degraded State (Self-Recovery):**
- Task handles Degraded internally (does NOT exit)
- Close WS → wait backoff → reconnect → re-snapshot
- Loop continues, health_tx reports status to driver
- Driver does NOT restart task (isolation principle)

**Circuit Breaker (Driver-Level):**
- After 5 consecutive spawn failures → circuit opens
- No spawn attempts while circuit open
- After backoff_max_duration (32s) → circuit resets
- One successful spawn → circuit closes immediately

**Backpressure Handling:**
- When `can_spawn()` returns Backpressure* → log warning, emit metric
- Queue spawn request for later retry (when resources free up)
- ResourceMonitor background task periodically checks queue
- Retry spawn when conditions improve

**Graceful Shutdown:**
- Per-tracker timeout: 10s (flush pending publishes, close WS)
- Global timeout: 30s (all trackers must complete)
- Stragglers after timeout → force abort (JoinHandle.abort())

---

## Configuration

```rust
pub struct DriverConfig {
    pub nats_url: String,
    pub db_path: PathBuf,
    pub max_trackers: usize,              // soft limit, default: 100
    pub memory_threshold_mb: u64,          // default: 2048
    pub cpu_threshold_pct: f64,            // default: 80.0
    pub graceful_shutdown_timeout_secs: u64, // default: 30
    pub tracker_shutdown_timeout_secs: u64,  // default: 10
    pub backoff_max_duration_secs: u64,    // default: 32
    pub circuit_breaker_threshold: u32,    // failures before circuit opens, default: 5
    pub resource_check_interval_secs: u64, // default: 5
}
```

**Environment Variables:**
- `NATS_URL` → default: `nats://127.0.0.1:4222`
- `DRIVER_DB_PATH` → default: `/app/data/pythia.db`
- `DRIVER_MAX_TRACKERS` → default: `100`
- `DRIVER_MEMORY_THRESHOLD_MB` → default: `2048`
- `DRIVER_CPU_THRESHOLD_PCT` → default: `80.0`
- `DRIVER_SHUTDOWN_TIMEOUT_SECS` → default: `30`
- `TRACKER_SHUTDOWN_TIMEOUT_SECS` → default: `10`
- `DRIVER_BACKOFF_MAX_SECS` → default: `32`
- `DRIVER_CIRCUIT_BREAKER_THRESHOLD` → default: `5`
- `DRIVER_RESOURCE_CHECK_INTERVAL_SECS` → default: `5`

---

## Observability

### Structured Logging

Use `tracing` crate with instrument macros:
```rust
#[instrument(skip(self), fields(asset_id = %asset_id))]
async fn spawn_tracker(&mut self, asset_id: AssetId, token_id: String) {
    info!("Spawning tracker for asset {asset_id}");
    // ...
}
```

**Log Levels:**
- `INFO` - normal operations (spawn, shutdown, state transitions)
- `WARN` - backpressure, circuit breaker, degraded trackers
- `ERROR` - spawn failures, unrecoverable errors

### Prometheus Metrics

```rust
use metrics::{counter, gauge, histogram};

// Active tracker count
gauge!("driver.active_trackers").set(self.trackers.len() as f64);

// Spawn events
counter!("driver.tracker_spawned", "asset_id" => asset_id.to_string()).increment(1);

// Backpressure events
counter!("driver.backpressure", "type" => "memory").increment(1);

// Circuit breaker trips
counter!("driver.circuit_breaker_open", "asset_id" => asset_id.to_string()).increment(1);

// Shutdown duration
histogram!("driver.tracker_shutdown_duration_ms").record(duration.as_millis() as f64);

// Tracker health states
gauge!("tracker.health_state", "asset_id" => asset_id.to_string(), "state" => "tracking").set(1.0);
```

**Key Metrics:**
- `driver.active_trackers` (gauge) - current tracker count
- `driver.tracker_spawned` (counter) - total spawns
- `driver.tracker_shutdown` (counter) - total shutdowns
- `driver.backpressure` (counter by type) - backpressure events
- `driver.circuit_breaker_open` (counter) - circuit breaker trips
- `driver.graceful_shutdown_timeout` (counter) - forced kills
- `tracker.health_state` (gauge by asset_id) - health status per tracker
- `tracker.ws_reconnects` (counter) - WS reconnection count
- `tracker.orderbook_publishes` (counter) - NATS publish count

### Health Check Endpoint

HTTP endpoint for monitoring:
```rust
#[derive(Serialize)]
struct HealthStatus {
    status: &'static str, // "healthy", "degraded", "unhealthy"
    active_trackers: usize,
    degraded_trackers: usize,
    circuit_breakers_open: usize,
    backpressure_active: bool,
}

// GET /health
// Returns 200 if healthy, 503 if degraded/unhealthy
```

---

## Testing Strategy

### Unit Tests

**Per-Component Isolation:**
- `ResourceMonitor`: backpressure thresholds, tracker count limits
- `BackoffState`: exponential progression, circuit breaker logic
- `DriverConfig`: env var parsing, defaults

**Example:**
```rust
#[test]
fn test_exponential_backoff() {
    let mut state = BackoffState::default();
    state.record_failure(Duration::from_secs(32));
    assert_eq!(state.next_retry, Duration::from_secs(1));
    state.record_failure(Duration::from_secs(32));
    assert_eq!(state.next_retry, Duration::from_secs(2));
    // ... verify up to 32s
}
```

### Integration Tests

**Driver + Mock Components:**
- Mock NATS subscriber (inject test messages)
- Mock TrackerTask (no real Polymarket I/O)
- Verify spawn/shutdown lifecycle

**Example:**
```rust
#[tokio::test]
async fn test_watchlist_upsert_spawns_tracker() {
    let (driver, nats_mock) = setup_test_driver().await;
    nats_mock.publish("dl.v1.watchlist.upsert", json!({
        "asset_id": "12345",
        "token_id": "67890"
    })).await;
    
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert_eq!(driver.trackers.len(), 1);
}
```

### End-to-End Tests

**Optional (run with `--ignored` flag):**
- Real Polymarket testnet connection
- Verify full flow: WS connect → snapshot → NATS publish

**Coverage Target:** 80%+ for critical paths (spawn, shutdown, backoff, backpressure)

---

## Implementation Phases

### Phase 1: Foundation (Day 1)
- Implement `DriverConfig` + env parsing
- Implement `MultiMarketDriver` struct + `new()`
- Implement SQLite watchlist loader
- Unit tests for config

### Phase 2: Tracker Lifecycle (Day 1-2)
- Implement `TrackerTask` + `TrackerHandle`
- Implement `spawn_tracker()` + `shutdown_tracker()`
- Integration tests for spawn/shutdown

### Phase 3: Watchlist Integration (Day 2)
- Implement NATS subscriber (dl.v1.watchlist.*)
- Implement `handle_watchlist_upsert()` + `handle_watchlist_remove()`
- Integration tests for watchlist updates

### Phase 4: Fault Tolerance (Day 2)
- Implement `ResourceMonitor` + backpressure logic
- Implement `BackoffState` + circuit breaker
- Unit tests for backoff/circuit breaker

### Phase 5: Observability (Day 3)
- Add tracing instrumentation
- Add Prometheus metrics
- Implement health check endpoint

### Phase 6: Testing & Polish (Day 3)
- Integration tests for backpressure
- Integration tests for graceful shutdown
- E2E test with Polymarket testnet (optional)
- Documentation + README

---

## Deployment

**Binary Location:** `services/sniper-orderbook/src/bin/driver.rs`

**Docker Compose Integration:**
```yaml
services:
  sniper-orderbook-driver:
    build:
      context: .
      dockerfile: services/sniper-orderbook/Dockerfile
    environment:
      - NATS_URL=nats://nats:4222
      - DRIVER_DB_PATH=/app/data/pythia.db
      - DRIVER_MAX_TRACKERS=50
      - DRIVER_MEMORY_THRESHOLD_MB=2048
      - DRIVER_CPU_THRESHOLD_PCT=80.0
    volumes:
      - ./data:/app/data
    depends_on:
      - nats
      - pythia-scheduler
    restart: unless-stopped
```

**Health Check:**
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
  interval: 30s
  timeout: 5s
  retries: 3
```

---

## Success Criteria

**MVP Complete When:**
- ✅ Driver spawns trackers for all active watchlist markets on startup
- ✅ Driver responds to `watchlist.upsert` by spawning new trackers
- ✅ Driver responds to `watchlist.remove` by gracefully shutting down trackers
- ✅ Backpressure prevents spawn when resources exceeded
- ✅ Circuit breaker prevents infinite retry loops
- ✅ Graceful shutdown completes within timeout (30s)
- ✅ Health check endpoint returns correct status
- ✅ Metrics exported to Prometheus
- ✅ 80%+ test coverage for critical paths

**Production-Ready When:**
- ✅ MVP criteria met
- ✅ Deployed to `amper` server via docker-compose
- ✅ Integrated with existing dual-loop stack
- ✅ Monitored via Grafana dashboard
- ✅ Operator can view active trackers in Command Center UI

---

## Future Enhancements (Out of Scope for M4)

**M5: Replay Determinism**
- Golden fixture tests (capture WS/REST traffic → replay → verify orderbook state)
- Similar to PR #25's replay binary for paper executor

**Horizontal Scaling (if needed at scale):**
- Supervisor/worker pool architecture (Approach 2 from design)
- Load balancing across multiple driver instances
- Distributed health monitoring

**Advanced Features:**
- Dynamic maxVolume adjustment based on Polymarket API stats
- Predictive backpressure (ML-based resource forecasting)
- Per-market priority (critical markets spawn first during backpressure)

---

## References

- **M1-M3 Foundation:** `services/sniper-orderbook/README.md`
- **Bus Protocol:** `docs/dual_loop/04-bus-protocol.md`
- **Dual Loop Roadmap:** `docs/dual_loop/06-roadmap.md`
- **Watchlist Publisher:** `src/services/watchlist_publisher.ts`
- **Existing Probe Tool:** `tools/orderbook_probe/src/main.rs` (single-market reference)

---

**End of Specification**
