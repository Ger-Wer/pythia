//! End-of-run aggregation: drop rate + latency percentiles.

use hdrhistogram::Histogram;
use serde::Serialize;

use crate::publisher::PublisherReport;

#[derive(Debug, Clone)]
pub struct LatencySample {
    #[allow(dead_code)] // useful for future per-tx debugging dumps
    pub evidence_hash: Vec<u8>,
    pub latency_ns: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunReport {
    pub published: u64,
    pub observed: u64,
    pub drop_rate: f64,
    pub elapsed_ms: u64,
    pub actual_rate_per_min: f64,

    // All in milliseconds, computed from a microsecond-resolution
    // hdrhistogram. We expose ms because the gate is stated in ms.
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub p99_ms: f64,
    pub max_ms: f64,
    pub mean_ms: f64,
}

impl RunReport {
    pub fn compute(publisher: &PublisherReport, samples: &[LatencySample]) -> anyhow::Result<Self> {
        // 1ns..60s, 3 significant digits. Generous upper bound so a
        // misbehaving CI run records the full tail rather than clipping.
        let mut hist: Histogram<u64> = Histogram::new_with_bounds(1, 60_000_000_000, 3)?;
        for sample in samples {
            hist.record(sample.latency_ns)?;
        }

        let observed = samples.len() as u64;
        let drop_rate = if publisher.published > 0 {
            (publisher.published.saturating_sub(observed)) as f64 / publisher.published as f64
        } else {
            0.0
        };

        let to_ms = |ns: u64| ns as f64 / 1_000_000.0;
        let mean_ms = if observed > 0 {
            hist.mean() / 1_000_000.0
        } else {
            0.0
        };

        Ok(Self {
            published: publisher.published,
            observed,
            drop_rate,
            elapsed_ms: publisher.elapsed_ms,
            actual_rate_per_min: publisher.actual_rate_per_min,
            p50_ms: to_ms(hist.value_at_quantile(0.50)),
            p95_ms: to_ms(hist.value_at_quantile(0.95)),
            p99_ms: to_ms(hist.value_at_quantile(0.99)),
            max_ms: to_ms(hist.max()),
            mean_ms,
        })
    }

    pub fn human(&self) -> String {
        format!(
            "load_harness report:\n\
             ---------------------\n\
             published          : {published}\n\
             observed           : {observed}\n\
             drop_rate          : {drop_rate:.4}\n\
             elapsed_ms         : {elapsed_ms}\n\
             actual_rate/min    : {actual_rate_per_min:.1}\n\
             latency p50  (ms)  : {p50_ms:.3}\n\
             latency p95  (ms)  : {p95_ms:.3}\n\
             latency p99  (ms)  : {p99_ms:.3}\n\
             latency max  (ms)  : {max_ms:.3}\n\
             latency mean (ms)  : {mean_ms:.3}\n",
            published = self.published,
            observed = self.observed,
            drop_rate = self.drop_rate,
            elapsed_ms = self.elapsed_ms,
            actual_rate_per_min = self.actual_rate_per_min,
            p50_ms = self.p50_ms,
            p95_ms = self.p95_ms,
            p99_ms = self.p99_ms,
            max_ms = self.max_ms,
            mean_ms = self.mean_ms,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(latency_ns: u64) -> LatencySample {
        LatencySample {
            evidence_hash: vec![],
            latency_ns,
        }
    }

    #[test]
    fn report_drop_rate_with_no_samples_is_one() {
        let pub_report = PublisherReport {
            published: 100,
            elapsed_ms: 60_000,
            actual_rate_per_min: 100.0,
        };
        let report = RunReport::compute(&pub_report, &[]).unwrap();
        assert_eq!(report.observed, 0);
        assert_eq!(report.drop_rate, 1.0);
        assert_eq!(report.p95_ms, 0.0);
    }

    #[test]
    fn report_percentiles_are_monotonic() {
        let pub_report = PublisherReport {
            published: 1000,
            elapsed_ms: 60_000,
            actual_rate_per_min: 1000.0,
        };
        // Roughly uniform across 1..1000 µs.
        let samples: Vec<LatencySample> = (1..=1000).map(|i| sample(i * 1_000)).collect();
        let report = RunReport::compute(&pub_report, &samples).unwrap();
        assert!(report.p50_ms <= report.p95_ms);
        assert!(report.p95_ms <= report.p99_ms);
        assert!(report.p99_ms <= report.max_ms);
        // 1000 samples observed, 1000 published.
        assert_eq!(report.observed, 1000);
        assert!(report.drop_rate.abs() < 1e-9);
    }

    #[test]
    fn report_drop_rate_partial() {
        let pub_report = PublisherReport {
            published: 1000,
            elapsed_ms: 60_000,
            actual_rate_per_min: 1000.0,
        };
        let samples: Vec<LatencySample> = (1..=900).map(|i| sample(i * 1_000)).collect();
        let report = RunReport::compute(&pub_report, &samples).unwrap();
        assert_eq!(report.observed, 900);
        assert!((report.drop_rate - 0.10).abs() < 1e-9);
    }
}
