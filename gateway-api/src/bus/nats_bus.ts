// NATS JetStream telemetry subscriber.
//
// Feeds low-priority metrics (latency, logs) into Prometheus counters. We
// don't mutate the exposed UI state from NATS traffic — telemetry is high-
// volume and should be observed via `/metrics` or Grafana dashboards.

import { type NatsConnection, connect } from "nats";
import type { Logger } from "../logger.ts";
import { telemetryMessagesTotal } from "../metrics.ts";

export interface NatsTelemetryHandle {
  stop(): Promise<void>;
}

export async function startNatsTelemetry(opts: {
  url: string;
  log: Logger;
  subject?: string;
}): Promise<NatsTelemetryHandle> {
  const log = opts.log.child({ component: "nats-telemetry" });
  const subject = opts.subject ?? "telemetry.>";
  let nc: NatsConnection;
  try {
    nc = await connect({
      servers: opts.url,
      reconnect: true,
      maxReconnectAttempts: -1,
      waitOnFirstConnect: false,
    });
  } catch (err) {
    log.warn("nats connect failed; telemetry disabled", {
      err: (err as Error).message,
    });
    return { async stop() {} };
  }

  const sub = nc.subscribe(subject);
  (async () => {
    for await (const m of sub) {
      const parts = m.subject.split(".");
      telemetryMessagesTotal.inc({
        kind: parts[1] ?? "unknown",
      });
    }
  })().catch((err: unknown) =>
    log.warn("nats subscription closed", { err: (err as Error).message }),
  );

  log.info("nats telemetry subscribed", { subject });

  return {
    async stop() {
      try {
        await sub.drain();
      } catch {
        /* ignore */
      }
      try {
        await nc.close();
      } catch {
        /* ignore */
      }
    },
  };
}
