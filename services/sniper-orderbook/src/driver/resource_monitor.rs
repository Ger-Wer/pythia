//! Resource monitoring and backpressure control for tracker spawning.
//!
//! ResourceMonitor prevents the driver from spawning too many trackers
//! and exhausting system resources (memory, CPU, tracker count).

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// Result of checking whether a new tracker can be spawned.
#[derive(Debug, Clone, PartialEq)]
pub enum CanSpawnResult {
    /// No backpressure, safe to spawn.
    Yes,
    /// Memory usage exceeds threshold.
    BackpressureMemory {
        current_mb: u64,
        threshold_mb: u64,
    },
    /// CPU usage exceeds threshold.
    BackpressureCpu {
        current_pct: f64,
        threshold_pct: f64,
    },
    /// Tracker count at or above limit.
    BackpressureTrackerLimit { current: usize, max: usize },
}

/// Monitors system resources and enforces backpressure limits.
pub struct ResourceMonitor {
    #[allow(dead_code)] // TODO: Used in future memory check implementation
    memory_threshold_mb: u64,
    #[allow(dead_code)] // TODO: Used in future CPU check implementation
    cpu_threshold_pct: f64,
    max_trackers: usize,
    current_trackers: Arc<AtomicUsize>,
}

impl ResourceMonitor {
    /// Creates a new ResourceMonitor with specified limits.
    pub fn new(max_trackers: usize, memory_threshold_mb: u64, cpu_threshold_pct: f64) -> Self {
        Self {
            memory_threshold_mb,
            cpu_threshold_pct,
            max_trackers,
            current_trackers: Arc::new(AtomicUsize::new(0)),
        }
    }

    /// Checks whether a new tracker can be spawned.
    ///
    /// Returns `CanSpawnResult::Yes` if all limits are satisfied,
    /// or a specific backpressure variant if a limit is exceeded.
    pub fn can_spawn(&self) -> CanSpawnResult {
        // TODO: Check memory usage (current_mb < memory_threshold_mb)
        // TODO: Check CPU usage (current_pct < cpu_threshold_pct)

        // Check tracker limit (cheapest check, MVP implementation)
        // Relaxed ordering is sufficient: we only need atomic increments/decrements,
        // no synchronization with other data structures
        let current = self.current_trackers.load(Ordering::Relaxed);
        if current >= self.max_trackers {
            return CanSpawnResult::BackpressureTrackerLimit {
                current,
                max: self.max_trackers,
            };
        }

        CanSpawnResult::Yes
    }

    /// Registers a new tracker (increments counter).
    pub fn register_tracker(&self) {
        // Relaxed ordering is sufficient: we only need atomic increments/decrements,
        // no synchronization with other data structures
        self.current_trackers.fetch_add(1, Ordering::Relaxed);
    }

    /// Unregisters a tracker (decrements counter).
    pub fn unregister_tracker(&self) {
        // Relaxed ordering is sufficient: we only need atomic increments/decrements,
        // no synchronization with other data structures
        self.current_trackers.fetch_sub(1, Ordering::Relaxed);
    }

    /// Returns the current number of registered trackers.
    pub fn current_count(&self) -> usize {
        // Relaxed ordering is sufficient: we only need atomic increments/decrements,
        // no synchronization with other data structures
        self.current_trackers.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tracker_limit_backpressure() {
        let monitor = ResourceMonitor::new(10, 1024, 80.0);

        // Under limit: should allow spawn
        for _ in 0..5 {
            monitor.register_tracker();
        }
        assert_eq!(monitor.current_count(), 5);
        assert_eq!(monitor.can_spawn(), CanSpawnResult::Yes);

        // Register up to limit
        for _ in 0..5 {
            monitor.register_tracker();
        }
        assert_eq!(monitor.current_count(), 10);

        // Exactly at limit: should trigger backpressure
        assert_eq!(
            monitor.can_spawn(),
            CanSpawnResult::BackpressureTrackerLimit {
                current: 10,
                max: 10
            }
        );

        // Unregister one: should allow spawn again
        monitor.unregister_tracker();
        assert_eq!(monitor.current_count(), 9);
        assert_eq!(monitor.can_spawn(), CanSpawnResult::Yes);
    }
}
