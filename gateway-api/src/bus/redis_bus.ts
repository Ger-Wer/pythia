// Redis Streams transport.
//
// Each subscription creates a dedicated consumer in the `gateway` consumer
// group and loops on XREADGROUP. XAUTOCLAIM every 10s reclaims pending
// messages from dead consumers (task spec §4; 04-bus-protocol.md §77).
//
// Receive-side: preserves raw protobuf bytes when the `payload` field is not
// parseable JSON. Publish-side: still writes JSON compatibility envelopes for
// gateway-originated operator mutations.

import { randomUUID } from "node:crypto";
import type { Redis } from "ioredis";
import type { Logger } from "../logger.ts";
import type { BusClient, BusMessage, BusSubscription } from "./types.ts";

const AUTOCLAIM_INTERVAL_MS = 10_000;
const BLOCK_MS = 5_000;
const BATCH_SIZE = 32;

interface SubscriptionRedisClient {
  connect?: () => Promise<unknown>;
}

interface DuplicatingRedisClient<T extends SubscriptionRedisClient> {
  duplicate(): T;
}

interface StreamEntry {
  id: string;
  fields: Array<string | Buffer>;
}
interface ReadResult {
  stream: string;
  entries: StreamEntry[];
}

function parseEntries(raw: unknown): StreamEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: StreamEntry[] = [];
  for (const e of raw) {
    if (!Array.isArray(e) || e.length < 2) continue;
    const [id, fields] = e as [string | Buffer, Array<string | Buffer>];
    out.push({
      id: Buffer.isBuffer(id) ? id.toString("utf8") : String(id),
      fields,
    });
  }
  return out;
}

function maybeParseJson(input: string): Record<string, unknown> | null {
  const trimmed = input.trim();
  if (!(trimmed.startsWith("{") && trimmed.endsWith("}"))) {
    return null;
  }
  try {
    return JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function fieldsToPayload(fields: Array<string | Buffer>): {
  payload: Record<string, unknown>;
  payloadBytes?: Uint8Array;
} {
  const payload: Record<string, unknown> = {};
  let payloadBytes: Uint8Array | undefined;
  for (let i = 0; i < fields.length; i += 2) {
    const k = fields[i];
    const v = fields[i + 1];
    if (k === undefined || v === undefined) continue;
    const key = Buffer.isBuffer(k) ? k.toString("utf8") : String(k);
    if (key === "payload") {
      if (Buffer.isBuffer(v)) {
        const maybeJson = maybeParseJson(v.toString("utf8"));
        if (maybeJson) {
          Object.assign(payload, maybeJson);
        } else {
          payloadBytes = new Uint8Array(v);
        }
      } else {
        const maybeJson = maybeParseJson(String(v));
        if (maybeJson) {
          Object.assign(payload, maybeJson);
        } else {
          payload[key] = String(v);
        }
      }
    } else {
      payload[key] = Buffer.isBuffer(v) ? v.toString("utf8") : v;
    }
  }
  return { payload, payloadBytes };
}

export async function openSubscriptionRedis<T extends SubscriptionRedisClient>(
  redis: DuplicatingRedisClient<T>,
): Promise<T> {
  const subscriptionRedis = redis.duplicate();
  if (typeof subscriptionRedis.connect === "function") {
    await subscriptionRedis.connect();
  }
  return subscriptionRedis;
}

export class RedisBusClient implements BusClient {
  private readonly redis: Redis;
  private readonly log: Logger;
  private readonly running = new Set<() => Promise<void>>();

  constructor(redis: Redis, log: Logger) {
    this.redis = redis;
    this.log = log.child({ component: "redis-bus" });
  }

  async publish(
    stream: string,
    payload: Record<string, unknown>,
    payloadBytes?: Uint8Array,
  ): Promise<string> {
    const id = await this.redis.xadd(
      stream,
      "*",
      "payload",
      payloadBytes ? Buffer.from(payloadBytes) : JSON.stringify(payload),
    );
    return id ?? "";
  }

  async subscribeStream(
    stream: string,
    group: string,
    consumer: string,
    handler: (msg: BusMessage) => Promise<void>,
  ): Promise<BusSubscription> {
    const subscriptionRedis = await openSubscriptionRedis(this.redis);

    // Create the group (idempotent). MKSTREAM so we don't need the stream
    // to exist ahead of time.
    try {
      await subscriptionRedis.xgroup("CREATE", stream, group, "$", "MKSTREAM");
    } catch (err) {
      const msg = (err as Error).message;
      if (!msg.includes("BUSYGROUP")) throw err;
    }

    const log = this.log.child({ stream, group, consumer });
    let stopped = false;
    const consumerName = `${consumer}-${randomUUID().slice(0, 8)}`;
    let resolveStopWait = () => {};
    const stopWait = new Promise<void>((resolve) => {
      resolveStopWait = resolve;
    });

    const loop = async () => {
      while (!stopped) {
        try {
          // Force Buffer replies so protobuf payload bytes survive the Redis
          // client boundary intact. Fall back to the normal helper if the
          // runtime client does not expose `callBuffer`.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const client: any = subscriptionRedis as any;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const raw: any =
            typeof client.callBuffer === "function"
              ? await client.callBuffer(
                  "XREADGROUP",
                  "GROUP",
                  group,
                  consumerName,
                  "COUNT",
                  BATCH_SIZE,
                  "BLOCK",
                  BLOCK_MS,
                  "STREAMS",
                  stream,
                  ">",
                )
              : await subscriptionRedis.xreadgroup(
                  "GROUP",
                  group,
                  consumerName,
                  "COUNT",
                  BATCH_SIZE,
                  "BLOCK",
                  BLOCK_MS,
                  "STREAMS",
                  stream,
                  ">",
                );
          if (!raw) continue;
          const results: ReadResult[] = raw.map(
            ([s, entries]: [string | Buffer, unknown]) => ({
              stream: Buffer.isBuffer(s) ? s.toString("utf8") : String(s),
              entries: parseEntries(entries),
            }),
          );
          for (const r of results) {
            for (const entry of r.entries) {
              const { payload, payloadBytes } = fieldsToPayload(entry.fields);
              try {
                await handler({ id: entry.id, payload, payloadBytes });
                await subscriptionRedis.xack(stream, group, entry.id);
              } catch (err) {
                log.warn("handler failed; leaving message pending", {
                  id: entry.id,
                  err: (err as Error).message,
                });
              }
            }
          }
        } catch (err) {
          if (stopped) break;
          log.warn("xreadgroup error; backing off", {
            err: (err as Error).message,
          });
          await new Promise((r) => setTimeout(r, 1_000));
        }
      }
    };

    const autoclaim = async () => {
      while (!stopped) {
        await Promise.race([
          new Promise((r) => setTimeout(r, AUTOCLAIM_INTERVAL_MS)),
          stopWait,
        ]);
        if (stopped) break;
        try {
          // XAUTOCLAIM <stream> <group> <consumer> <min-idle-ms> 0-0
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          await (subscriptionRedis as any).xautoclaim(
            stream,
            group,
            consumerName,
            30_000,
            "0-0",
            "COUNT",
            BATCH_SIZE,
          );
        } catch (err) {
          log.debug("xautoclaim noop", { err: (err as Error).message });
        }
      }
    };

    const loopPromise = loop();
    const autoclaimPromise = autoclaim();

    const stop = async () => {
      stopped = true;
      resolveStopWait();
      try {
        await subscriptionRedis.quit();
      } catch {
        try {
          subscriptionRedis.disconnect(false);
        } catch {
          /* ignore */
        }
      }
      await Promise.allSettled([loopPromise, autoclaimPromise]);
    };
    this.running.add(stop);

    return { stop };
  }

  async close(): Promise<void> {
    const stops = Array.from(this.running);
    this.running.clear();
    await Promise.allSettled(stops.map((s) => s()));
  }
}
