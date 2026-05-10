/**
 * Pure statistics + ASCII histogram for the signal probe.
 *
 * Kept dependency-free so it can be unit-tested under `bun test` without
 * Redis or a live gateway.
 */

export interface LatencySummary {
  count: number;
  mean_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
  min_ms: number;
}

export function summarize(samples_ms: readonly number[]): LatencySummary {
  if (samples_ms.length === 0) {
    return {
      count: 0,
      mean_ms: 0,
      p50_ms: 0,
      p95_ms: 0,
      p99_ms: 0,
      max_ms: 0,
      min_ms: 0,
    };
  }
  const sorted = [...samples_ms].sort((a, b) => a - b);
  const sum = sorted.reduce((acc, v) => acc + v, 0);
  const pick = (q: number): number => {
    // Nearest-rank percentile. For small samples this is more meaningful than
    // linear interpolation between two adjacent observations.
    const idx = Math.min(
      sorted.length - 1,
      Math.max(0, Math.ceil(q * sorted.length) - 1),
    );
    return sorted[idx]!;
  };
  return {
    count: sorted.length,
    mean_ms: sum / sorted.length,
    p50_ms: pick(0.5),
    p95_ms: pick(0.95),
    p99_ms: pick(0.99),
    max_ms: sorted[sorted.length - 1]!,
    min_ms: sorted[0]!,
  };
}

/**
 * Render an ASCII histogram with N equal-width buckets across [min, max].
 * Each row is `"  [lo..hi)  ##### (count)"`.
 */
export function renderHistogram(
  samples_ms: readonly number[],
  bucketCount = 10,
  width = 40,
): string {
  if (samples_ms.length === 0) return "  (no samples)";
  const sorted = [...samples_ms].sort((a, b) => a - b);
  const min = sorted[0]!;
  const max = sorted[sorted.length - 1]!;
  if (max === min) return `  [${min.toFixed(2)}ms]  ${"#".repeat(Math.min(width, sorted.length))} (${sorted.length})`;
  const span = max - min;
  const buckets = new Array<number>(bucketCount).fill(0);
  for (const v of sorted) {
    let idx = Math.floor(((v - min) / span) * bucketCount);
    if (idx >= bucketCount) idx = bucketCount - 1;
    if (idx < 0) idx = 0;
    buckets[idx]! += 1;
  }
  const peak = Math.max(...buckets);
  const lines: string[] = [];
  for (let i = 0; i < bucketCount; i++) {
    const lo = min + (span * i) / bucketCount;
    const hi = min + (span * (i + 1)) / bucketCount;
    const count = buckets[i]!;
    const bar = "#".repeat(Math.round((count / peak) * width));
    lines.push(
      `  [${lo.toFixed(2)}..${hi.toFixed(2)} ms]  ${bar} (${count})`,
    );
  }
  return lines.join("\n");
}

/**
 * Format a one-line summary suitable for stdout / CI logs.
 */
export function formatSummary(s: LatencySummary): string {
  if (s.count === 0) return "samples=0  (no signals matched)";
  return (
    `samples=${s.count}  ` +
    `min=${s.min_ms.toFixed(2)}ms  ` +
    `mean=${s.mean_ms.toFixed(2)}ms  ` +
    `p50=${s.p50_ms.toFixed(2)}ms  ` +
    `p95=${s.p95_ms.toFixed(2)}ms  ` +
    `p99=${s.p99_ms.toFixed(2)}ms  ` +
    `max=${s.max_ms.toFixed(2)}ms`
  );
}
