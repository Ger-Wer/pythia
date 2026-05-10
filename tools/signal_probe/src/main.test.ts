/**
 * Unit tests for the parts of `main.ts` that don't need a live gateway:
 * CLI parsing and the payload builder.
 */

import { describe, expect, test } from "bun:test";
import { buildSignalPayload, parseArgs } from "./main";

describe("parseArgs", () => {
  test("returns sensible defaults when called with no flags", () => {
    const cfg = parseArgs([]);
    expect(cfg.gatewayHttpUrl).toMatch(/^http:\/\//);
    expect(cfg.gatewayWsUrl).toMatch(/^ws:\/\//);
    expect(cfg.redisUrl).toMatch(/^redis:\/\//);
    expect(cfg.operatorId).toBe("admin");
    expect(cfg.durationSecs).toBeGreaterThan(0);
    expect(cfg.intervalMs).toBeGreaterThan(0);
    expect(cfg.signalsStream).toBe("dl.v1.signals.emitted");
  });

  test("supports both --flag value and --flag=value syntax", () => {
    const cfg = parseArgs([
      "--duration-secs",
      "60",
      "--interval-ms=250",
      "--operator",
      "ops",
    ]);
    expect(cfg.durationSecs).toBe(60);
    expect(cfg.intervalMs).toBe(250);
    expect(cfg.operatorId).toBe("ops");
  });

  test("rejects non-numeric numeric flags", () => {
    expect(() => parseArgs(["--duration-secs", "soon"]))
      .toThrow(/invalid numeric flag/);
  });

  test("environment variable provides password fallback", () => {
    const prev = process.env.SIGNAL_PROBE_PASSWORD;
    process.env.SIGNAL_PROBE_PASSWORD = "from-env";
    try {
      const cfg = parseArgs([]);
      expect(cfg.operatorPassword).toBe("from-env");
    } finally {
      if (prev === undefined) {
        delete process.env.SIGNAL_PROBE_PASSWORD;
      } else {
        process.env.SIGNAL_PROBE_PASSWORD = prev;
      }
    }
  });

  test("explicit --password beats the env fallback", () => {
    const prev = process.env.SIGNAL_PROBE_PASSWORD;
    process.env.SIGNAL_PROBE_PASSWORD = "from-env";
    try {
      const cfg = parseArgs(["--password", "from-cli"]);
      expect(cfg.operatorPassword).toBe("from-cli");
    } finally {
      if (prev === undefined) {
        delete process.env.SIGNAL_PROBE_PASSWORD;
      } else {
        process.env.SIGNAL_PROBE_PASSWORD = prev;
      }
    }
  });
});

describe("buildSignalPayload", () => {
  test("preserves the issued_at timestamp byte-for-byte", () => {
    const issuedAt = 1_730_000_000_000;
    const p = buildSignalPayload("probe-1", issuedAt);
    expect(p.signal_id).toBe("probe-1");
    expect(p.issued_at_unix_ms).toBe(issuedAt);
  });

  test("expires_at is one minute after issued_at", () => {
    const issuedAt = 1_730_000_000_000;
    const p = buildSignalPayload("probe-1", issuedAt);
    expect(p.expires_at_unix_ms).toBe(issuedAt + 60_000);
  });

  test("payload contains every field the gateway bridge consumes", () => {
    const p = buildSignalPayload("probe-x", Date.now());
    // Gateway-api/src/bus/bridge.ts:STREAM_SIGNALS_EMITTED handler reads
    // these fields — make sure we don't drop one.
    const required = [
      "signal_id",
      "server_version",
      "market_id",
      "ctf_address",
      "market_question",
      "edge",
      "p_fundamental",
      "p_market",
      "confidence_low",
      "confidence_high",
      "expected_value_bps",
      "side_hint",
      "suggested_position_micros",
      "expires_at_unix_ms",
      "llm_rationale",
      "evidence_urls",
      "issued_at_unix_ms",
      "priority",
    ];
    for (const k of required) {
      expect(p).toHaveProperty(k);
    }
  });
});
