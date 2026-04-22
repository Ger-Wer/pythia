// Redis Streams transport.
//
// Each subscription creates a dedicated consumer in the `gateway` consumer
// group and loops on XREADGROUP. XAUTOCLAIM every 10s reclaims pending
// messages from dead consumers (task spec §4; 04-bus-protocol.md §77).

import { randomUUID } from "node:crypto";
import type { Redis } from "ioredis";
import type { Logger } from "../logger.ts";
import type { BusClient, BusMessage, BusSubscription } from "./types.ts";

const AUTOCLAIM_INTERVAL_MS = 10_000;
const BLOCK_MS = 5_000;
const BATCH_SIZE = 32;

interface StreamEntry {
  id: string;
  fields: string[];
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
    const [id, fields] = e as [string, string[]];
    out.push({ id, fields });
  }
  return out;
}

function fieldsToPayload(fields: string[]): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (let i = 0; i < fields.length; i += 2) {
    const k = fields[i];
    const v = fields[i + 1];
    if (k === undefined || v === undefined) continue;
    if (k === "payload") {
      try {
        Object.assign(payload, JSON.parse(v) as Record<string, unknown>);
      } catch {
        payload[k] = v;
      }
    } else {
      payload[k] = v;
    }
  }
  return payload;
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
  ): Promise<string> {
    const id = await this.redis.xadd(
      stream,
      "*",
      "payload",
      JSON.stringify(payload),
    );
    return id ?? "";
  }

  async subscribeStream(
    stream: string,
    group: string,
    consumer: string,
    handler: (msg: BusMessage) => Promise<void>,
  ): Promise<BusSubscription> {
    // Create the group (idempotent). MKSTREAM so we don't need the stream
    // to exist ahead of time.
    try {
      await this.redis.xgroup("CREATE", stream, group, "$", "MKSTREAM");
    } catch (err) {
      const msg = (err as Error).message;
      if (!msg.includes("BUSYGROUP")) throw err;
    }

    const log = this.log.child({ stream, group, consumer });
    let stopped = false;
    const consumerName = `${consumer}-${randomUUID().slice(0, 8)}`;

    const loop = async () => {
      while (!stopped) {
        try {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const raw: any = await this.redis.xreadgroup(
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
            ([s, entries]: [string, unknown]) => ({
              stream: s,
              entries: parseEntries(entries),
            }),
          );
          for (const r of results) {
            for (const entry of r.entries) {
              const payload = fieldsToPayload(entry.fields);
              try {
                await handler({ id: entry.id, payload });
                await this.redis.xack(stream, group, entry.id);
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
        await new Promise((r) => setTimeout(r, AUTOCLAIM_INTERVAL_MS));
        if (stopped) break;
        try {
          // XAUTOCLAIM <stream> <group> <consumer> <min-idle-ms> 0-0
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          await (this.redis as any).xautoclaim(
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
