// In-memory BusClient used by unit tests.
//
// Mimics Redis Streams semantics enough for the gateway bridge: every
// published message is delivered once to every registered subscriber group,
// in publish order, asynchronously.

import type { BusClient, BusMessage, BusSubscription } from "./types.ts";

interface Handler {
  group: string;
  consumer: string;
  cb: (msg: BusMessage) => Promise<void>;
}

export class MemoryBusClient implements BusClient {
  private readonly handlers = new Map<string, Handler[]>();
  private nextId = 0;

  async publish(
    stream: string,
    payload: Record<string, unknown>,
    payloadBytes?: Uint8Array,
  ): Promise<string> {
    this.nextId += 1;
    const id = `${Date.now()}-${this.nextId}`;
    const msg: BusMessage = { id, payload, payloadBytes };
    const handlers = this.handlers.get(stream) ?? [];
    for (const h of handlers) {
      // Fire-and-forget like a real subscriber loop.
      await h.cb(msg);
    }
    return id;
  }

  async subscribeStream(
    stream: string,
    group: string,
    consumer: string,
    cb: (msg: BusMessage) => Promise<void>,
  ): Promise<BusSubscription> {
    const list = this.handlers.get(stream) ?? [];
    const entry: Handler = { group, consumer, cb };
    list.push(entry);
    this.handlers.set(stream, list);
    return {
      async stop() {
        const cur = list.indexOf(entry);
        if (cur >= 0) list.splice(cur, 1);
      },
    };
  }

  async close(): Promise<void> {
    this.handlers.clear();
  }
}
