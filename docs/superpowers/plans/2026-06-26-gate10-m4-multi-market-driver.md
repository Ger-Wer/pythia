# Gate #10 M4: Multi-Market Orderbook Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build orchestration layer that manages multiple concurrent Polymarket orderbook trackers (one per watchlist market), with fault tolerance, backpressure, and graceful shutdown.

**Architecture:** Monolithic Rust driver binary using tokio for async concurrency. One task per tracker, HashMap-based tracker pool, NATS for watchlist subscription and orderbook publishing, SQLite for initial state load.

**Tech Stack:** Rust, tokio, async-nats, sqlx, sysinfo, thiserror, tracing, metrics

**Spec Reference:** `docs/superpowers/specs/2026-06-26-gate10-m4-multi-market-driver-design.md`

---

## File Structure

**New Files:**
```
services/sniper-orderbook/
├── src/
│   ├── bin/
│   │   └── driver.rs                    # Main driver binary (entry point)
│   ├── driver/
│   │   ├── mod.rs                       # Module exports
│   │   ├── config.rs                    # DriverConfig + env parsing
│   │   ├── coordinator.rs               # MultiMarketDriver (main coordinator)
│   │   ├── tracker_task.rs              # TrackerTask (async wrapper for Tracker)
│   │   ├── resource_monitor.rs          # ResourceMonitor (backpressure)
│   │   └── backoff.rs                   # BackoffState + circuit breaker
│   └── driver.rs                        # Re-export driver module
└── tests/
    └── driver_integration_test.rs       # Integration tests
```

**Modified Files:**
```
services/sniper-orderbook/
├── Cargo.toml                           # Add dependencies: async-nats, sqlx, sysinfo, axum
└── src/lib.rs                           # Export driver module
```

---

## Phase 1: Foundation (Tasks 1-3)

### Task 1: DriverConfig Implementation

**Files:**
- Create: `services/sniper-orderbook/src/driver/mod.rs`
- Create: `services/sniper-orderbook/src/driver/config.rs`
- Test: `services/sniper-orderbook/src/driver/config.rs` (inline doc tests)

- [ ] **Step 1: Write test for DriverConfig defaults**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_defaults() {
        let config = DriverConfig::default();
        assert_eq!(config.nats_url, "nats://127.0.0.1:4222");
        assert_eq!(config.max_trackers, 100);
        assert_eq!(config.memory_threshold_mb, 2048);
        assert_eq!(config.cpu_threshold_pct, 80.0);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services && cargo test -p sniper-orderbook config_defaults`
Expected: FAIL (DriverConfig not defined)

- [ ] **Step 3: Implement DriverConfig struct**

```rust
// services/sniper-orderbook/src/driver/config.rs
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct DriverConfig {
    pub nats_url: String,
    pub db_path: PathBuf,
    pub max_trackers: usize,
    pub memory_threshold_mb: u64,
    pub cpu_threshold_pct: f64,
    pub graceful_shutdown_timeout_secs: u64,
    pub tracker_shutdown_timeout_secs: u64,
    pub backoff_max_duration_secs: u64,
    pub circuit_breaker_threshold: u32,
    pub resource_check_interval_secs: u64,
}

impl Default for DriverConfig {
    fn default() -> Self {
        Self {
            nats_url: "nats://127.0.0.1:4222".to_string(),
            db_path: PathBuf::from("/app/data/pythia.db"),
            max_trackers: 100,
            memory_threshold_mb: 2048,
            cpu_threshold_pct: 80.0,
            graceful_shutdown_timeout_secs: 30,
            tracker_shutdown_timeout_secs: 10,
            backoff_max_duration_secs: 32,
            circuit_breaker_threshold: 5,
            resource_check_interval_secs: 5,
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook config_defaults`
Expected: PASS

- [ ] **Step 5: Write test for env var parsing**

```rust
#[test]
fn test_config_from_env() {
    std::env::set_var("NATS_URL", "nats://custom:4222");
    std::env::set_var("DRIVER_MAX_TRACKERS", "50");
    
    let config = DriverConfig::from_env().unwrap();
    assert_eq!(config.nats_url, "nats://custom:4222");
    assert_eq!(config.max_trackers, 50);
    
    std::env::remove_var("NATS_URL");
    std::env::remove_var("DRIVER_MAX_TRACKERS");
}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd services && cargo test -p sniper-orderbook config_from_env`
Expected: FAIL (from_env not implemented)

- [ ] **Step 7: Implement from_env method**

```rust
use std::env;

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("Invalid config value: {0}")]
    InvalidValue(String),
}

impl DriverConfig {
    pub fn from_env() -> Result<Self, ConfigError> {
        Ok(Self {
            nats_url: env::var("NATS_URL")
                .unwrap_or_else(|_| "nats://127.0.0.1:4222".to_string()),
            db_path: PathBuf::from(env::var("DRIVER_DB_PATH")
                .unwrap_or_else(|_| "/app/data/pythia.db".to_string())),
            max_trackers: env::var("DRIVER_MAX_TRACKERS")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(100),
            memory_threshold_mb: env::var("DRIVER_MEMORY_THRESHOLD_MB")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(2048),
            cpu_threshold_pct: env::var("DRIVER_CPU_THRESHOLD_PCT")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(80.0),
            graceful_shutdown_timeout_secs: env::var("DRIVER_SHUTDOWN_TIMEOUT_SECS")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(30),
            tracker_shutdown_timeout_secs: env::var("TRACKER_SHUTDOWN_TIMEOUT_SECS")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(10),
            backoff_max_duration_secs: env::var("DRIVER_BACKOFF_MAX_SECS")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(32),
            circuit_breaker_threshold: env::var("DRIVER_CIRCUIT_BREAKER_THRESHOLD")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(5),
            resource_check_interval_secs: env::var("DRIVER_RESOURCE_CHECK_INTERVAL_SECS")
                .ok().and_then(|s| s.parse().ok())
                .unwrap_or(5),
        })
    }
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook config_from_env`
Expected: PASS

- [ ] **Step 9: Create driver module exports**

```rust
// services/sniper-orderbook/src/driver/mod.rs
pub mod config;

pub use config::{DriverConfig, ConfigError};
```

- [ ] **Step 10: Export driver module from lib**

```rust
// services/sniper-orderbook/src/lib.rs (append)
pub mod driver;
```

- [ ] **Step 11: Commit**

```bash
cd services/sniper-orderbook
git add src/driver/ src/lib.rs
git commit -m "feat(driver): add DriverConfig with env parsing

- DriverConfig struct with all parameters
- from_env() method for environment variable loading
- Default impl with production-ready defaults
- Unit tests for defaults and env parsing"
```

### Task 2: BackoffState & Circuit Breaker

**Files:**
- Create: `services/sniper-orderbook/src/driver/backoff.rs`
- Modify: `services/sniper-orderbook/src/driver/mod.rs`

- [ ] **Step 1: Write test for exponential backoff**

```rust
// services/sniper-orderbook/src/driver/backoff.rs
use std::time::{Duration, Instant};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_exponential_backoff_progression() {
        let mut state = BackoffState::default();
        let max = Duration::from_secs(32);
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(1));
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(2));
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(4));
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(8));
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(16));
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(32)); // capped
        
        state.record_failure(max);
        assert_eq!(state.next_retry, Duration::from_secs(32)); // stays at max
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services && cargo test -p sniper-orderbook exponential_backoff`
Expected: FAIL

- [ ] **Step 3: Implement BackoffState**

```rust
#[derive(Debug, Clone)]
pub struct BackoffState {
    pub failures: u32,
    pub last_attempt: Instant,
    pub next_retry: Duration,
    pub circuit_open: bool,
}

impl Default for BackoffState {
    fn default() -> Self {
        Self {
            failures: 0,
            last_attempt: Instant::now(),
            next_retry: Duration::from_secs(0),
            circuit_open: false,
        }
    }
}

impl BackoffState {
    pub fn record_failure(&mut self, max_duration: Duration) {
        self.failures += 1;
        self.last_attempt = Instant::now();
        
        // Exponential: 1, 2, 4, 8, 16, 32 (cap)
        let exp = 2u64.saturating_pow((self.failures - 1).min(5));
        self.next_retry = Duration::from_secs(exp).min(max_duration);
    }
    
    pub fn record_success(&mut self) {
        self.failures = 0;
        self.next_retry = Duration::from_secs(0);
        self.circuit_open = false;
    }
    
    pub fn is_circuit_open(&self, threshold: u32) -> bool {
        self.failures >= threshold
    }
    
    pub fn can_retry_now(&self) -> bool {
        self.last_attempt.elapsed() >= self.next_retry
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook exponential_backoff`
Expected: PASS

- [ ] **Step 5: Write test for circuit breaker**

```rust
#[test]
fn test_circuit_breaker_opens_after_threshold() {
    let mut state = BackoffState::default();
    let max = Duration::from_secs(32);
    
    // Record 4 failures - circuit still closed
    for _ in 0..4 {
        state.record_failure(max);
        assert!(!state.is_circuit_open(5));
    }
    
    // 5th failure - circuit opens
    state.record_failure(max);
    assert!(state.is_circuit_open(5));
    assert_eq!(state.failures, 5);
    
    // Success closes circuit
    state.record_success();
    assert!(!state.is_circuit_open(5));
    assert_eq!(state.failures, 0);
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook circuit_breaker`
Expected: PASS (already implemented)

- [ ] **Step 7: Write test for retry timing**

```rust
#[test]
fn test_can_retry_respects_backoff() {
    let mut state = BackoffState::default();
    state.record_failure(Duration::from_secs(32));
    
    // Immediately after failure - cannot retry
    assert!(!state.can_retry_now());
    
    // After backoff duration - can retry
    std::thread::sleep(Duration::from_millis(1100)); // 1s backoff + margin
    assert!(state.can_retry_now());
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook can_retry`
Expected: PASS

- [ ] **Step 9: Export backoff module**

```rust
// services/sniper-orderbook/src/driver/mod.rs (append)
pub mod backoff;

pub use backoff::BackoffState;
```

- [ ] **Step 10: Commit**

```bash
git add src/driver/backoff.rs src/driver/mod.rs
git commit -m "feat(driver): add BackoffState with circuit breaker

- Exponential backoff: 1s → 2s → 4s → 8s → 16s → 32s (capped)
- Circuit breaker opens after N failures (configurable threshold)
- record_success() resets state and closes circuit
- can_retry_now() respects backoff duration
- Full unit test coverage"
```

### Task 3: ResourceMonitor Implementation

**Files:**
- Create: `services/sniper-orderbook/src/driver/resource_monitor.rs`
- Modify: `services/sniper-orderbook/src/driver/mod.rs`
- Modify: `services/sniper-orderbook/Cargo.toml`

- [ ] **Step 1: Add sysinfo dependency**

```toml
# services/sniper-orderbook/Cargo.toml
[dependencies]
# ... existing deps
sysinfo = "0.30"
```

- [ ] **Step 2: Write test for backpressure detection**

```rust
// services/sniper-orderbook/src/driver/resource_monitor.rs
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

#[derive(Debug, Clone, PartialEq)]
pub enum CanSpawnResult {
    Yes,
    BackpressureMemory { current_mb: u64, threshold_mb: u64 },
    BackpressureCpu { current_pct: f64, threshold_pct: f64 },
    BackpressureTrackerLimit { current: usize, max: usize },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tracker_limit_backpressure() {
        let monitor = ResourceMonitor::new(10, 2048, 80.0);
        
        // Register 10 trackers (at limit)
        for _ in 0..10 {
            monitor.register_tracker();
        }
        
        // Should return backpressure
        match monitor.can_spawn() {
            CanSpawnResult::BackpressureTrackerLimit { current, max } => {
                assert_eq!(current, 10);
                assert_eq!(max, 10);
            }
            _ => panic!("Expected BackpressureTrackerLimit"),
        }
        
        // Unregister one - should allow spawn
        monitor.unregister_tracker();
        assert!(matches!(monitor.can_spawn(), CanSpawnResult::Yes));
    }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services && cargo test -p sniper-orderbook tracker_limit`
Expected: FAIL

- [ ] **Step 4: Implement ResourceMonitor**

```rust
pub struct ResourceMonitor {
    memory_threshold_mb: u64,
    cpu_threshold_pct: f64,
    max_trackers: usize,
    current_trackers: Arc<AtomicUsize>,
}

impl ResourceMonitor {
    pub fn new(max_trackers: usize, memory_threshold_mb: u64, cpu_threshold_pct: f64) -> Self {
        Self {
            memory_threshold_mb,
            cpu_threshold_pct,
            max_trackers,
            current_trackers: Arc::new(AtomicUsize::new(0)),
        }
    }
    
    pub fn can_spawn(&self) -> CanSpawnResult {
        let current = self.current_trackers.load(Ordering::Relaxed);
        
        // Check tracker limit first (cheapest check)
        if current >= self.max_trackers {
            return CanSpawnResult::BackpressureTrackerLimit {
                current,
                max: self.max_trackers,
            };
        }
        
        // TODO: Check memory/CPU in follow-up task
        // For now, only enforce tracker limit
        
        CanSpawnResult::Yes
    }
    
    pub fn register_tracker(&self) {
        self.current_trackers.fetch_add(1, Ordering::Relaxed);
    }
    
    pub fn unregister_tracker(&self) {
        self.current_trackers.fetch_sub(1, Ordering::Relaxed);
    }
    
    pub fn current_count(&self) -> usize {
        self.current_trackers.load(Ordering::Relaxed)
    }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook tracker_limit`
Expected: PASS

- [ ] **Step 6: Export resource_monitor module**

```rust
// services/sniper-orderbook/src/driver/mod.rs (append)
pub mod resource_monitor;

pub use resource_monitor::{ResourceMonitor, CanSpawnResult};
```

- [ ] **Step 7: Commit**

```bash
git add Cargo.toml src/driver/resource_monitor.rs src/driver/mod.rs
git commit -m "feat(driver): add ResourceMonitor with tracker limit

- CanSpawnResult enum for backpressure signaling
- Tracker count enforcement with atomic counter
- register/unregister methods for lifecycle tracking
- Memory/CPU checks deferred to follow-up task
- Unit tests for tracker limit backpressure"
```

---

## Phase 2: Tracker Lifecycle (Tasks 4-6)

### Task 4: TrackerTask Skeleton

**Files:**
- Create: `services/sniper-orderbook/src/driver/tracker_task.rs`
- Modify: `services/sniper-orderbook/src/driver/mod.rs`
- Modify: `services/sniper-orderbook/Cargo.toml`

- [ ] **Step 1: Add tokio + channel dependencies**

```toml
# services/sniper-orderbook/Cargo.toml
[dependencies]
tokio = { workspace = true, features = ["sync", "time"] }
```

- [ ] **Step 2: Define TrackerHealth enum with test**

```rust
// services/sniper-orderbook/src/driver/tracker_task.rs
use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;
use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub enum TrackerHealth {
    Tracking,
    Syncing,
    Degraded(String),
    Stopping,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tracker_health_variants() {
        let tracking = TrackerHealth::Tracking;
        let syncing = TrackerHealth::Syncing;
        let degraded = TrackerHealth::Degraded("sequence gap".to_string());
        let stopping = TrackerHealth::Stopping;
        
        assert_eq!(tracking, TrackerHealth::Tracking);
        assert_ne!(tracking, syncing);
        assert!(matches!(degraded, TrackerHealth::Degraded(_)));
    }
}
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook tracker_health`
Expected: PASS

- [ ] **Step 4: Define TrackerHandle struct**

```rust
pub type AssetId = String;

pub struct TrackerHandle {
    pub asset_id: AssetId,
    pub task: JoinHandle<Result<(), TrackerError>>,
    pub shutdown_tx: oneshot::Sender<()>,
    pub health_rx: watch::Receiver<TrackerHealth>,
}

#[derive(Debug, thiserror::Error)]
pub enum TrackerError {
    #[error("WebSocket connection failed: {0}")]
    WebSocketConnection(String),
    
    #[error("Snapshot request failed: {0}")]
    SnapshotRequest(String),
    
    #[error("NATS publish failed: {0}")]
    NatsPublish(String),
    
    #[error("Degraded: {0}")]
    Degraded(String),
    
    #[error("Shutdown signal received")]
    ShutdownSignal,
}
```

- [ ] **Step 5: Define TrackerTask skeleton**

```rust
pub struct TrackerTask {
    asset_id: AssetId,
    token_id: String,
    health_tx: watch::Sender<TrackerHealth>,
    shutdown_rx: oneshot::Receiver<()>,
}

impl TrackerTask {
    pub fn spawn(asset_id: AssetId, token_id: String) -> TrackerHandle {
        let (health_tx, health_rx) = watch::channel(TrackerHealth::Syncing);
        let (shutdown_tx, shutdown_rx) = oneshot::channel();
        
        let task_instance = Self {
            asset_id: asset_id.clone(),
            token_id,
            health_tx,
            shutdown_rx,
        };
        
        let task = tokio::spawn(task_instance.run());
        
        TrackerHandle {
            asset_id,
            task,
            shutdown_tx,
            health_rx,
        }
    }
    
    async fn run(mut self) -> Result<(), TrackerError> {
        // TODO: Implement in next task
        // For now, just wait for shutdown
        self.shutdown_rx.await.map_err(|_| TrackerError::ShutdownSignal)?;
        self.health_tx.send(TrackerHealth::Stopping).ok();
        Ok(())
    }
}
```

- [ ] **Step 6: Write integration test for spawn/shutdown**

```rust
#[cfg(test)]
mod integration_tests {
    use super::*;
    
    #[tokio::test]
    async fn test_tracker_task_spawn_and_shutdown() {
        let handle = TrackerTask::spawn("test_asset".to_string(), "test_token".to_string());
        
        // Verify initial health
        assert_eq!(*handle.health_rx.borrow(), TrackerHealth::Syncing);
        
        // Send shutdown signal
        handle.shutdown_tx.send(()).unwrap();
        
        // Wait for task to complete
        let result = handle.task.await.unwrap();
        assert!(matches!(result, Err(TrackerError::ShutdownSignal)));
    }
}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd services && cargo test -p sniper-orderbook tracker_task_spawn`
Expected: PASS

- [ ] **Step 8: Export tracker_task module**

```rust
// services/sniper-orderbook/src/driver/mod.rs (append)
pub mod tracker_task;

pub use tracker_task::{TrackerTask, TrackerHandle, TrackerHealth, TrackerError, AssetId};
```

- [ ] **Step 9: Commit**

```bash
git add Cargo.toml src/driver/tracker_task.rs src/driver/mod.rs
git commit -m "feat(driver): add TrackerTask skeleton with lifecycle

- TrackerHealth enum (Tracking, Syncing, Degraded, Stopping)
- TrackerHandle with task, shutdown channel, health channel
- TrackerTask::spawn() creates task + channels
- Skeleton run() loop (shutdown only, no Polymarket yet)
- Integration test for spawn/shutdown flow"
```

---

## Remaining Tasks Summary

Due to context limits, remaining tasks are summarized. Full implementation follows same TDD pattern:

### Task 5: MultiMarketDriver Foundation
- Create `coordinator.rs` with MultiMarketDriver struct
- Implement `new()` with NATS + SQLite connections
- Implement `spawn_tracker()` + `shutdown_tracker()`
- Unit tests for tracker pool management

### Task 6: Watchlist Integration
- Implement NATS subscriber for dl.v1.watchlist.*
- Implement `handle_watchlist_upsert()` + `handle_watchlist_remove()`
- Add backpressure + circuit breaker checks in spawn path
- Integration tests for watchlist messages

### Task 7: TrackerTask Polymarket Integration
- Add Polymarket REST client wrapper (reuse from M2)
- Add Polymarket WS subscriber wrapper (reuse from M2)
- Implement main event loop (WS messages, snapshot, shutdown)
- Add NATS publisher for orderbook updates

### Task 8: Driver Main Loop
- Implement `MultiMarketDriver::run()` event loop
- Handle watchlist messages + resource monitoring
- Implement graceful shutdown logic
- Integration tests for full driver lifecycle

### Task 9: Observability
- Add tracing instrumentation
- Add Prometheus metrics
- Implement health check HTTP endpoint
- Verify metrics export

### Task 10: Main Binary
- Create `bin/driver.rs` entry point
- Parse CLI args + config
- Initialize driver + run
- Handle signals (SIGTERM/SIGINT)

---

## Testing Strategy

**Unit Tests:** Per-component (config, backoff, resource monitor)  
**Integration Tests:** Driver + mock NATS/DB  
**E2E Tests:** Optional with real Polymarket testnet

**Coverage Target:** 80%+ for critical paths

---

## Deployment

**Build:**
```bash
cd services
cargo build --release --bin driver
```

**Run:**
```bash
export NATS_URL=nats://localhost:4222
export DRIVER_DB_PATH=/app/data/pythia.db
./target/release/driver
```

**Docker Compose:** See spec for full configuration

---

## Success Criteria

- [ ] Driver spawns trackers for all active watchlist markets
- [ ] Responds to watchlist.upsert by spawning trackers
- [ ] Responds to watchlist.remove by gracefully shutting down trackers
- [ ] Backpressure prevents spawn when resources exceeded
- [ ] Circuit breaker prevents infinite retry loops
- [ ] Graceful shutdown completes within 30s timeout
- [ ] Health endpoint returns correct status
- [ ] Metrics exported to Prometheus
- [ ] 80%+ test coverage

---

**End of Implementation Plan**
