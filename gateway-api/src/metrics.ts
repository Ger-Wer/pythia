// Prometheus metrics. Exposed at GET /metrics.
//
// Every metric is prefixed with `gateway_` so users can `curl /metrics |
// grep gateway_` to see only this service's signals (task DoD).

import { Counter, Gauge, Histogram, Registry, collectDefaultMetrics } from "prom-client";

export const registry = new Registry();
registry.setDefaultLabels({ service: "gateway-api" });
collectDefaultMetrics({ register: registry, prefix: "gateway_" });

export const httpRequestsTotal = new Counter({
  name: "gateway_http_requests_total",
  help: "Total HTTP requests received.",
  labelNames: ["method", "route", "status"] as const,
  registers: [registry],
});

export const httpRequestDurationSeconds = new Histogram({
  name: "gateway_http_request_duration_seconds",
  help: "HTTP request duration in seconds.",
  labelNames: ["method", "route", "status"] as const,
  buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
  registers: [registry],
});

export const wsConnectionsActive = new Gauge({
  name: "gateway_ws_connections_active",
  help: "Currently-connected WebSocket clients.",
  registers: [registry],
});

export const wsMessagesSentTotal = new Counter({
  name: "gateway_ws_messages_sent_total",
  help: "WebSocket frames sent to clients.",
  labelNames: ["kind"] as const,
  registers: [registry],
});

export const wsMessagesReceivedTotal = new Counter({
  name: "gateway_ws_messages_received_total",
  help: "WebSocket frames received from clients.",
  labelNames: ["kind"] as const,
  registers: [registry],
});

export const wsDisconnectsTotal = new Counter({
  name: "gateway_ws_disconnects_total",
  help: "WebSocket disconnects grouped by reason.",
  labelNames: ["reason"] as const,
  registers: [registry],
});

export const busMessagesTotal = new Counter({
  name: "gateway_bus_messages_total",
  help: "Bus messages consumed from Redis Streams.",
  labelNames: ["stream"] as const,
  registers: [registry],
});

export const telemetryMessagesTotal = new Counter({
  name: "gateway_telemetry_messages_total",
  help: "NATS telemetry messages observed.",
  labelNames: ["kind"] as const,
  registers: [registry],
});

export const decisionsPublishedTotal = new Counter({
  name: "gateway_decisions_published_total",
  help: "SignalDecision messages published to the bus.",
  labelNames: ["action"] as const,
  registers: [registry],
});

export const killCommandsTotal = new Counter({
  name: "gateway_kill_commands_total",
  help: "KillCommand messages published to the bus.",
  labelNames: ["scope", "status"] as const,
  registers: [registry],
});

export const stateServerVersion = new Gauge({
  name: "gateway_state_server_version",
  help: "Current server_version of the gateway state projection.",
  registers: [registry],
});
