// Bus abstraction for Redis Streams (control-plane) and NATS JetStream (telemetry).
//
// We keep these behind interfaces so tests can inject in-memory fakes. The task
// spec (04-bus-protocol.md) dictates Redis Streams for control + NATS for
// telemetry; the abstraction below mirrors that split.
//
// Wire format note: Phase 1 uses JSON-encoded payloads on Redis Streams.
// Protobuf codegen for TypeScript is wired in `buf.gen.yaml` but is not yet
// integrated into this service (out of scope for Track B MVP — see PR body).
// Downstream Rust consumers in Phase 2 switch to protobuf once the ts-proto
// pipeline is stable.

export interface BusMessage {
  id: string;
  payload: Record<string, unknown>;
}

export interface BusSubscription {
  stop(): Promise<void>;
}

export interface BusClient {
  publish(stream: string, payload: Record<string, unknown>): Promise<string>;

  subscribeStream(
    stream: string,
    group: string,
    consumer: string,
    handler: (msg: BusMessage) => Promise<void>,
  ): Promise<BusSubscription>;

  close(): Promise<void>;
}
