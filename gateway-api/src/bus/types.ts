// Bus abstraction for Redis Streams (control-plane) and NATS JetStream (telemetry).
//
// We keep these behind interfaces so tests can inject in-memory fakes. The task
// spec (04-bus-protocol.md) dictates Redis Streams for control + NATS for
// telemetry; the abstraction below mirrors that split.
//
// Wire format note: the gateway keeps a UI-friendly JSON projection in memory,
// but Redis Stream entries may now arrive either as legacy JSON payloads or as
// protobuf bytes in the `payload` field. Consumers should prefer
// `payloadBytes` when present and fall back to `payload` for tests / legacy
// writers.

export interface BusMessage {
  id: string;
  payload: Record<string, unknown>;
  payloadBytes?: Uint8Array;
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
