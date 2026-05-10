/**
 * Unit tests for the pure stats helpers. Hermetic — no Redis, no WS.
 */

import { describe, expect, test } from "bun:test";
import { formatSummary, renderHistogram, summarize } from "./stats";

describe("summarize", () => {
  test("returns zeroed summary on empty input", () => {
    const s = summarize([]);
    expect(s.count).toBe(0);
    expect(s.mean_ms).toBe(0);
    expect(s.p50_ms).toBe(0);
    expect(s.p95_ms).toBe(0);
    expect(s.max_ms).toBe(0);
  });

  test("computes basic stats over a tiny sample", () => {
    const s = summarize([10, 20, 30]);
    expect(s.count).toBe(3);
    expect(s.min_ms).toBe(10);
    expect(s.max_ms).toBe(30);
    expect(s.mean_ms).toBeCloseTo(20.0);
    expect(s.p50_ms).toBe(20);
  });

  test("nearest-rank percentile picks the right bucket", () => {
    // 100 samples 1..100, p95 must be 95 with nearest-rank, not 95.05
    const samples = Array.from({ length: 100 }, (_, i) => i + 1);
    const s = summarize(samples);
    expect(s.p50_ms).toBe(50);
    expect(s.p95_ms).toBe(95);
    expect(s.p99_ms).toBe(99);
    expect(s.max_ms).toBe(100);
    expect(s.min_ms).toBe(1);
  });

  test("does not mutate the input array", () => {
    const input = [3, 1, 2];
    summarize(input);
    expect(input).toEqual([3, 1, 2]);
  });
});

describe("renderHistogram", () => {
  test("returns a no-samples placeholder on empty input", () => {
    expect(renderHistogram([])).toMatch(/no samples/);
  });

  test("collapses identical samples into a single bucket", () => {
    const out = renderHistogram([5, 5, 5]);
    expect(out).toMatch(/5\.00/);
    expect(out).toMatch(/\(3\)/);
  });

  test("produces N rows for N buckets when samples span a range", () => {
    const samples = Array.from({ length: 50 }, (_, i) => i + 1);
    const out = renderHistogram(samples, 5);
    const rows = out.split("\n");
    expect(rows.length).toBe(5);
    for (const r of rows) {
      expect(r).toMatch(/\[\d+\.\d+\.\.\d+\.\d+ ms\]/);
    }
  });
});

describe("formatSummary", () => {
  test("flags zero-sample case explicitly", () => {
    expect(formatSummary(summarize([]))).toMatch(/no signals matched/);
  });

  test("includes all expected fields", () => {
    const out = formatSummary(summarize([10, 20, 30]));
    expect(out).toMatch(/samples=3/);
    expect(out).toMatch(/min=10\.00ms/);
    expect(out).toMatch(/p50=/);
    expect(out).toMatch(/p95=/);
    expect(out).toMatch(/p99=/);
    expect(out).toMatch(/max=30\.00ms/);
  });
});
