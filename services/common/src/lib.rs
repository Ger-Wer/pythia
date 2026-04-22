//! Общие типы и утилиты для Dual-Loop Fast Loop сервисов.
//!
//! Phase 1:
//! * [`proto`] — protobuf-типы, сгенерированные `build.rs` из
//!   `schemas/dual_loop/v1/*.proto`.
//! * [`topics`] — имена топиков Redis/NATS (single source of truth в Rust;
//!   симметрично продублировано в `src/services/bus/topics.ts`).
//! * [`bus`] — async-обёртки над Redis Streams и NATS JetStream.
//! * [`observability`], [`time`] — helper'ы из Phase 0.

pub mod bus;
pub mod observability;
pub mod proto;
pub mod time;
pub mod topics;

/// Семантическая версия wire-протокола.
///
/// Должна совпадать с версией пакета в protobuf-схемах (`dual_loop.v1`).
pub const WIRE_VERSION: &str = "v1";

/// Имя сервиса в метриках / tracing.
pub fn service_label(service: &str) -> String {
    format!("dual_loop.{service}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_version_is_v1() {
        assert_eq!(WIRE_VERSION, "v1");
    }

    #[test]
    fn service_label_format() {
        assert_eq!(service_label("sniper_ingest"), "dual_loop.sniper_ingest");
    }
}
