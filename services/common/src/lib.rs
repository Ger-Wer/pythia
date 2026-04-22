//! Общие типы и утилиты для Dual-Loop Fast Loop сервисов.
//!
//! В Phase 0 этот крейт содержит только начальные заглушки и базовые
//! helper'ы (observability, time). Phase 1 добавит сгенерированные из
//! `schemas/dual_loop/v1/*.proto` типы через `prost-build`.

pub mod observability;
pub mod time;

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
