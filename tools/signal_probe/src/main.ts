/**
 * Signal Feed E2E Latency Probe (Paper Operator Release gate #3).
 *
 * Measures the wall-clock latency between "a Slow Loop signal is published
 * to the bus" and "the same signal is delivered to a WebSocket client
 * (emulating the UI)". The probe:
 *
 *  1. Authenticates against the gateway-api with the dev operator credentials
 *     (POST /api/v1/auth/token) and obtains a PASETO v4.local token.
 *  2. Opens `wss?://<host>/ws/v1?token=...` and subscribes to the `signals`
 *     topic.
 *  3. Periodically `XADD`s a synthetic signal payload to the
 *     `dl.v1.signals.emitted` Redis stream. The payload embeds a fresh
 *     `issued_at_unix_ms` timestamp.
 *  4. On every WS snapshot/patch, looks up the matching `signal_id` and
 *     records `received_at - issued_at_unix_ms` into a histogram.
 *  5. After the run window expires, prints percentiles + an ASCII histogram
 *     and exits 0.
 *
 * Per the 2026-05-05 decision log this probe is **measurement-only**: it
 * never returns a non-zero exit code on a slow run, and it is not wired
 * into CI. Operators run it manually against a local dev stack to catch
 * regressions before tagging a release.
 */

import { setTimeout as delay } from "node:timers/promises";
import { Redis } from "ioredis";

import { formatSummary, renderHistogram, summarize } from "./stats.ts";

// ---------------------------------------------------------------------------
// CLI parsing
// ---------------------------------------------------------------------------

export interface ProbeConfig {
  gatewayHttpUrl: string;
  gatewayWsUrl: string;
  redisUrl: string;
  operatorId: string;
  operatorPassword: string;
  durationSecs: number;
  intervalMs: number;
  signalsStream: string;
  warmupMs: number;
  tailWaitMs: number;
}

const DEFAULTS: ProbeConfig = {
  gatewayHttpUrl: "http://127.0.0.1:9103",
  gatewayWsUrl: "ws://127.0.0.1:9103/ws/v1",
  redisUrl: "redis://127.0.0.1:6379",
  operatorId: "admin",
  operatorPassword: "changeme",
  durationSecs: 30,
  intervalMs: 1000,
  signalsStream: "dl.v1.signals.emitted",
  warmupMs: 250,
  tailWaitMs: 2000,
};

function parseFlag(argv: string[], name: string): string | undefined {
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === `--${name}`) return argv[i + 1];
    if (a.startsWith(`--${name}=`)) return a.slice(name.length + 3);
  }
  return undefined;
}

export function parseArgs(argv: string[]): ProbeConfig {
  const num = (v: string | undefined, fallback: number): number => {
    if (v === undefined) return fallback;
    const n = Number(v);
    if (!Number.isFinite(n)) {
      throw new Error(`invalid numeric flag value: ${v}`);
    }
    return n;
  };
  return {
    gatewayHttpUrl: parseFlag(argv, "gateway-http") ?? DEFAULTS.gatewayHttpUrl,
    gatewayWsUrl: parseFlag(argv, "gateway-ws") ?? DEFAULTS.gatewayWsUrl,
    redisUrl: parseFlag(argv, "redis") ?? DEFAULTS.redisUrl,
    operatorId: parseFlag(argv, "operator") ?? DEFAULTS.operatorId,
    operatorPassword:
      parseFlag(argv, "password") ??
      process.env.SIGNAL_PROBE_PASSWORD ??
      DEFAULTS.operatorPassword,
    durationSecs: num(parseFlag(argv, "duration-secs"), DEFAULTS.durationSecs),
    intervalMs: num(parseFlag(argv, "interval-ms"), DEFAULTS.intervalMs),
    signalsStream: parseFlag(argv, "stream") ?? DEFAULTS.signalsStream,
    warmupMs: num(parseFlag(argv, "warmup-ms"), DEFAULTS.warmupMs),
    tailWaitMs: num(parseFlag(argv, "tail-wait-ms"), DEFAULTS.tailWaitMs),
  };
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

async function fetchToken(cfg: ProbeConfig): Promise<string> {
  const res = await fetch(`${cfg.gatewayHttpUrl}/api/v1/auth/token`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      operator_id: cfg.operatorId,
      password: cfg.operatorPassword,
    }),
  });
  if (!res.ok) {
    throw new Error(
      `auth/token returned ${res.status}: ${await res.text().catch(() => "")}`,
    );
  }
  const body = (await res.json()) as { token?: string };
  if (!body.token) {
    throw new Error("auth/token response missing 'token' field");
  }
  return body.token;
}

// ---------------------------------------------------------------------------
// WS subscriber — tracks `signal_id` arrival timestamps
// ---------------------------------------------------------------------------

interface WsSubscriber {
  /** Wait for either the WS to close or `stop()` to be called. */
  waitForExit(): Promise<void>;
  stop(): void;
}

interface ArrivalCallback {
  (signalId: string, receivedAtMs: number): void;
}

function attachWsSubscriber(
  cfg: ProbeConfig,
  token: string,
  onArrival: ArrivalCallback,
  onReady: () => void,
): WsSubscriber {
  const url = `${cfg.gatewayWsUrl}?token=${encodeURIComponent(token)}`;
  const ws = new WebSocket(url);
  const seenSignals = new Set<string>();
  const exit = Promise.withResolvers<void>();
  let stopped = false;

  const handleSignals = (raw: unknown): void => {
    if (typeof raw !== "object" || raw === null) return;
    // snapshot.data.signals -> Record<signal_id, signal>
    // patch.patch -> JsonPatch (array of {op, path, value, ...})
    // We only care about new arrivals, identified by previously-unseen
    // signal_id. The probe's injection payload sets a UUID-ish signal_id
    // per iteration, so any unseen id from a snapshot/patch counts.
    const obj = raw as Record<string, unknown>;
    const kind = typeof obj.kind === "string" ? obj.kind : "";
    const recv = Date.now();

    if (kind === "snapshot") {
      const data = obj.data as Record<string, unknown> | undefined;
      const signals = data?.signals as Record<string, unknown> | undefined;
      if (signals) {
        for (const sid of Object.keys(signals)) {
          if (!seenSignals.has(sid)) {
            seenSignals.add(sid);
            onArrival(sid, recv);
          }
        }
      }
      return;
    }
    if (kind === "patch") {
      const topic = typeof obj.topic === "string" ? obj.topic : "";
      if (topic !== "signals") return;
      const patch = obj.patch as Array<Record<string, unknown>> | undefined;
      if (!Array.isArray(patch)) return;
      for (const op of patch) {
        const path = typeof op.path === "string" ? op.path : "";
        // Paths look like "/<signal_id>" or "/<signal_id>/<field>" inside
        // the signals topic. Treat any add/replace at root level as an
        // arrival.
        if (op.op === "add" || op.op === "replace") {
          const rest = path.startsWith("/") ? path.slice(1) : path;
          if (!rest) continue;
          const sid = rest.split("/")[0]!;
          if (!seenSignals.has(sid)) {
            seenSignals.add(sid);
            onArrival(sid, recv);
          }
        }
      }
    }
  };

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ kind: "subscribe", topics: ["signals"] }));
    onReady();
  });
  ws.addEventListener("message", (ev) => {
    try {
      const data =
        typeof ev.data === "string"
          ? ev.data
          : new TextDecoder().decode(ev.data as ArrayBuffer);
      handleSignals(JSON.parse(data));
    } catch (err) {
      process.stderr.write(`[probe] failed to parse WS message: ${String(err)}\n`);
    }
  });
  ws.addEventListener("close", () => {
    if (!stopped) {
      process.stderr.write("[probe] WebSocket closed unexpectedly\n");
    }
    exit.resolve();
  });
  ws.addEventListener("error", (ev) => {
    process.stderr.write(`[probe] WebSocket error: ${String((ev as ErrorEvent).message ?? ev)}\n`);
  });

  return {
    waitForExit: () => exit.promise,
    stop: () => {
      stopped = true;
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      exit.resolve();
    },
  };
}

// ---------------------------------------------------------------------------
// Publisher — XADDs synthetic signal payloads to dl.v1.signals.emitted
// ---------------------------------------------------------------------------

export interface PendingSignal {
  signal_id: string;
  issued_at_unix_ms: number;
}

export function buildSignalPayload(
  signalId: string,
  issuedAtUnixMs: number,
): Record<string, unknown> {
  return {
    signal_id: signalId,
    server_version: 1,
    market_id: `probe-market-${signalId}`,
    ctf_address: "",
    market_question: `Signal probe synthetic ${signalId}`,
    edge: 0.0,
    p_fundamental: 0.5,
    p_market: 0.5,
    confidence_low: 0.4,
    confidence_high: 0.6,
    expected_value_bps: 0,
    side_hint: "UNSPECIFIED",
    suggested_position_micros: 0,
    expires_at_unix_ms: issuedAtUnixMs + 60_000,
    llm_rationale: "synthetic probe signal",
    evidence_urls: [],
    issued_at_unix_ms: issuedAtUnixMs,
    priority: "LOW",
  };
}

interface PublisherHandle {
  count: number;
  publishOne(): Promise<PendingSignal>;
}

function attachPublisher(redis: Redis, stream: string): PublisherHandle {
  let count = 0;
  return {
    get count() {
      return count;
    },
    async publishOne(): Promise<PendingSignal> {
      count += 1;
      const issuedAt = Date.now();
      const signalId = `probe-${process.pid}-${count}-${issuedAt}`;
      const payload = buildSignalPayload(signalId, issuedAt);
      await redis.xadd(stream, "*", "payload", JSON.stringify(payload));
      return { signal_id: signalId, issued_at_unix_ms: issuedAt };
    },
  };
}

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------

export interface ProbeReport {
  published: number;
  matched: number;
  unmatched: number;
  durationSecs: number;
  latenciesMs: number[];
}

async function runProbe(cfg: ProbeConfig): Promise<ProbeReport> {
  process.stdout.write(
    `[probe] gateway=${cfg.gatewayHttpUrl}  ws=${cfg.gatewayWsUrl}  redis=${cfg.redisUrl}\n`,
  );
  process.stdout.write(
    `[probe] duration=${cfg.durationSecs}s  interval=${cfg.intervalMs}ms  stream=${cfg.signalsStream}\n`,
  );

  const token = await fetchToken(cfg);
  process.stdout.write("[probe] obtained PASETO token\n");

  const pending = new Map<string, number>(); // signal_id -> issued_at_ms
  const latencies: number[] = [];
  const onArrival = (sid: string, recv: number): void => {
    const issued = pending.get(sid);
    if (issued !== undefined) {
      pending.delete(sid);
      latencies.push(recv - issued);
    }
  };

  const wsReady = Promise.withResolvers<void>();
  const sub = attachWsSubscriber(cfg, token, onArrival, () => wsReady.resolve());
  await wsReady.promise;
  process.stdout.write("[probe] WS connected, subscribed to 'signals' topic\n");

  // Drain any pre-existing snapshot before we start; one snapshot is sent
  // immediately on connect and we want to ignore signal_ids that were
  // already in the store.
  await delay(cfg.warmupMs);

  const redis = new Redis(cfg.redisUrl, { lazyConnect: true });
  await redis.connect();
  const pub = attachPublisher(redis, cfg.signalsStream);

  const start = Date.now();
  const deadline = start + cfg.durationSecs * 1000;
  let nextEmit = start;
  let running = true;
  const stop = (): void => {
    running = false;
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);

  while (running && Date.now() < deadline) {
    const now = Date.now();
    if (now < nextEmit) {
      await delay(Math.min(nextEmit - now, 100));
      continue;
    }
    try {
      const p = await pub.publishOne();
      pending.set(p.signal_id, p.issued_at_unix_ms);
    } catch (err) {
      process.stderr.write(`[probe] publish failed: ${String(err)}\n`);
    }
    nextEmit += cfg.intervalMs;
  }

  // Drain late arrivals.
  await delay(cfg.tailWaitMs);

  sub.stop();
  await redis.quit();

  const durationSecs = (Date.now() - start) / 1000;
  return {
    published: pub.count,
    matched: latencies.length,
    unmatched: pub.count - latencies.length,
    durationSecs,
    latenciesMs: latencies,
  };
}

function emitReport(report: ProbeReport): void {
  const summary = summarize(report.latenciesMs);
  process.stdout.write("\n[probe] --- final report ---\n");
  process.stdout.write(
    `[probe] published        = ${report.published}\n` +
      `[probe] matched          = ${report.matched}\n` +
      `[probe] unmatched        = ${report.unmatched}\n` +
      `[probe] duration         = ${report.durationSecs.toFixed(1)}s\n` +
      `[probe] latency          = ${formatSummary(summary)}\n`,
  );
  if (report.latenciesMs.length > 0) {
    process.stdout.write("[probe] histogram:\n");
    process.stdout.write(renderHistogram(report.latenciesMs) + "\n");
  }
  process.stdout.write(
    "[probe] (measurement-only — exit 0 regardless of p95)\n",
  );
}

// ---------------------------------------------------------------------------
// Entry
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const cfg = parseArgs(process.argv.slice(2));
  try {
    const report = await runProbe(cfg);
    emitReport(report);
  } catch (err) {
    process.stderr.write(`[probe] fatal: ${String(err)}\n`);
    // Even on fatal errors we exit 0 — gate #3 is measurement-only and a
    // failed probe (e.g. gateway-api offline) should never block CI.
  }
  process.exit(0);
}

// `import.meta.main` is Bun's idiomatic way to detect the entry script.
// Falls back to `argv[1]` matching this file when run under plain Node.
const isEntry = (() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const meta: any = import.meta;
  if (typeof meta?.main === "boolean") return meta.main as boolean;
  return false;
})();
if (isEntry) {
  void main();
}
