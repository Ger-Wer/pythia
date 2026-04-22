//! Утилиты для времени: wall-clock (UTC) + monotonic clock (для latency).

use std::time::Instant;

/// Пара (wall_clock_unix_ms, monotonic_ns) — для стэмпинга событий bus'а.
///
/// wall_clock_unix_ms используется для human-readable correlation между хостами.
/// monotonic_ns используется для точных latency-расчётов внутри одного хоста.
#[derive(Debug, Clone, Copy)]
pub struct StampedTime {
    pub wall_clock_unix_ms: i64,
    pub monotonic_ns: u64,
}

impl StampedTime {
    /// Снять текущий timestamp.
    pub fn now() -> Self {
        let wall_clock_unix_ms = time::OffsetDateTime::now_utc()
            .unix_timestamp_nanos()
            .div_euclid(1_000_000) as i64;

        // monotonic_ns от boot; для cross-host ни в коем случае не сравниваем.
        let monotonic_ns = monotonic_base().elapsed().as_nanos() as u64;

        Self {
            wall_clock_unix_ms,
            monotonic_ns,
        }
    }

    /// Разница monotonic_ns между двумя timestamp'ами с одного хоста.
    /// Возвращает None если other был позже self.
    pub fn elapsed_since(&self, earlier: &StampedTime) -> Option<u64> {
        self.monotonic_ns.checked_sub(earlier.monotonic_ns)
    }
}

fn monotonic_base() -> Instant {
    use once_cell::sync::Lazy;
    static BASE: Lazy<Instant> = Lazy::new(Instant::now);
    *BASE
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamped_time_now_returns_recent() {
        let t = StampedTime::now();
        assert!(t.wall_clock_unix_ms > 0);
    }

    #[test]
    fn elapsed_since_monotonic() {
        let a = StampedTime::now();
        std::thread::sleep(std::time::Duration::from_millis(1));
        let b = StampedTime::now();
        assert!(b.elapsed_since(&a).is_some_and(|d| d > 0));
    }
}
