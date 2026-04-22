/**
 * Redis Streams publisher + consumer helpers.
 *
 * Symmetric to the Rust client in `services/common/src/bus/redis.rs`:
 *   * payload bytes live in the `payload` field of an XADD entry
 *   * optional trace id lives in the `trace_id` field
 *   * XADD uses `MAXLEN ~ 10000` by default to bound stream length
 *
 * Protobuf encoding/decoding is the caller's responsibility — generic
 * `encode` / `decode` functions are supplied by the ts-proto module for
 * each message type.
 */

import { Redis, type RedisOptions } from 'ioredis';

export const PAYLOAD_FIELD = 'payload';
export const TRACE_FIELD = 'trace_id';

export interface BusLogger {
  info: (msg: string, fields?: Record<string, unknown>) => void;
  warn: (msg: string, fields?: Record<string, unknown>) => void;
  error: (msg: string, fields?: Record<string, unknown>) => void;
}

const defaultLogger: BusLogger = {
  info: (msg, fields) =>
    console.log(JSON.stringify({ level: 'info', msg, ...fields })),
  warn: (msg, fields) =>
    console.warn(JSON.stringify({ level: 'warn', msg, ...fields })),
  error: (msg, fields) =>
    console.error(JSON.stringify({ level: 'error', msg, ...fields })),
};

/** ts-proto `encode` call returns a `BinaryWriter` with `.finish()`. */
export interface ProtoCodec<T> {
  encode(message: T): { finish: () => Uint8Array };
  decode(input: Uint8Array): T;
}

/**
 * Thin XADD publisher. Caller provides the ts-proto `encode` entry point.
 *
 * ```ts
 * const pub = new StreamPublisher({ url: process.env.REDIS_URL! });
 * await pub.publish(WATCHLIST_UPSERT, WatchlistUpsert, { ...msg });
 * ```
 */
export interface StreamPublisherOpts {
  url?: string;
  redis?: Redis;
  redisOpts?: RedisOptions;
  /** Approximate MAXLEN (`MAXLEN ~`). Set `null` to disable. Default 10k. */
  maxlenApprox?: number | null;
  logger?: BusLogger;
}

export class StreamPublisher {
  private readonly redis: Redis;
  private readonly ownsRedis: boolean;
  private readonly maxlenApprox: number | null;
  private readonly log: BusLogger;

  constructor(opts: StreamPublisherOpts = {}) {
    if (opts.redis) {
      this.redis = opts.redis;
      this.ownsRedis = false;
    } else if (opts.url) {
      this.redis = new Redis(opts.url, opts.redisOpts ?? {});
      this.ownsRedis = true;
    } else {
      this.redis = new Redis(opts.redisOpts ?? {});
      this.ownsRedis = true;
    }
    this.maxlenApprox = opts.maxlenApprox === undefined ? 10_000 : opts.maxlenApprox;
    this.log = opts.logger ?? defaultLogger;
  }

  async publish<T>(
    topic: string,
    codec: ProtoCodec<T>,
    message: T,
    traceId?: string,
  ): Promise<string> {
    const payload = Buffer.from(codec.encode(message).finish());

    const args: (string | Buffer | number)[] = [topic];
    if (this.maxlenApprox !== null) {
      args.push('MAXLEN', '~', this.maxlenApprox);
    }
    args.push('*', PAYLOAD_FIELD, payload);
    if (traceId) {
      args.push(TRACE_FIELD, traceId);
    }
    // ioredis types accept (string | Buffer | number)[] for `xadd`.
    const id = (await (this.redis as unknown as {
      xadd: (...a: unknown[]) => Promise<string>;
    }).xadd(...args)) as string;
    return id;
  }

  async close(): Promise<void> {
    if (this.ownsRedis) {
      await this.redis.quit().catch(() => this.redis.disconnect());
    }
  }
}

/**
 * One entry pulled from a consumer group.
 */
export interface ConsumerMessage {
  id: string;
  topic: string;
  payload: Uint8Array;
  traceId?: string;
}

export interface ConsumerGroupOpts {
  url?: string;
  redis?: Redis;
  redisOpts?: RedisOptions;
  topic: string;
  group: string;
  consumer: string;
  blockMs?: number;
  batch?: number;
  logger?: BusLogger;
}

/**
 * Small `XREADGROUP` loop. Symmetric to the Rust `ConsumerGroup`.
 * `consume` calls `handler` for each message; on a resolved promise the
 * message is `XACK`ed, on a rejection it is left pending.
 */
export class ConsumerGroup {
  private readonly redis: Redis;
  private readonly ownsRedis: boolean;
  readonly topic: string;
  readonly group: string;
  readonly consumer: string;
  readonly blockMs: number;
  readonly batch: number;
  private readonly log: BusLogger;
  private stopped = false;

  constructor(opts: ConsumerGroupOpts) {
    if (opts.redis) {
      this.redis = opts.redis;
      this.ownsRedis = false;
    } else if (opts.url) {
      this.redis = new Redis(opts.url, opts.redisOpts ?? {});
      this.ownsRedis = true;
    } else {
      this.redis = new Redis(opts.redisOpts ?? {});
      this.ownsRedis = true;
    }
    this.topic = opts.topic;
    this.group = opts.group;
    this.consumer = opts.consumer;
    this.blockMs = opts.blockMs ?? 1_000;
    this.batch = opts.batch ?? 64;
    this.log = opts.logger ?? defaultLogger;
  }

  async ensureGroup(): Promise<void> {
    try {
      await this.redis.xgroup('CREATE', this.topic, this.group, '$', 'MKSTREAM');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (!msg.includes('BUSYGROUP')) throw err;
    }
  }

  async readNext(): Promise<ConsumerMessage[]> {
    const reply = (await (this.redis as unknown as {
      xreadgroup: (...a: unknown[]) => Promise<unknown>;
    }).xreadgroup(
      'GROUP',
      this.group,
      this.consumer,
      'COUNT',
      this.batch,
      'BLOCK',
      this.blockMs,
      'STREAMS',
      this.topic,
      '>',
    )) as [string, [string, (string | Buffer)[]][]][] | null;

    if (!reply) return [];
    const out: ConsumerMessage[] = [];
    for (const [topic, entries] of reply) {
      for (const [id, fields] of entries) {
        let payload: Uint8Array | undefined;
        let traceId: string | undefined;
        for (let i = 0; i < fields.length; i += 2) {
          const k = fields[i];
          const v = fields[i + 1];
          const key = typeof k === 'string' ? k : Buffer.from(k).toString('utf8');
          if (key === PAYLOAD_FIELD) {
            payload = v instanceof Buffer ? new Uint8Array(v) : Buffer.from(String(v));
          } else if (key === TRACE_FIELD) {
            traceId = v instanceof Buffer ? v.toString('utf8') : String(v);
          }
        }
        if (!payload) {
          this.log.warn('stream entry missing payload field', { id, topic });
          continue;
        }
        out.push({ id, topic, payload, traceId });
      }
    }
    return out;
  }

  async ack(id: string): Promise<void> {
    await this.redis.xack(this.topic, this.group, id);
  }

  /**
   * Run until `stop()` is called. For each message the handler is awaited;
   * on resolution the message is ACKed.
   */
  async consume(handler: (msg: ConsumerMessage) => Promise<void>): Promise<void> {
    await this.ensureGroup();
    while (!this.stopped) {
      let msgs: ConsumerMessage[];
      try {
        msgs = await this.readNext();
      } catch (err) {
        this.log.warn('XREADGROUP failed; backing off', {
          err: err instanceof Error ? err.message : String(err),
        });
        await new Promise((r) => setTimeout(r, 500));
        continue;
      }
      for (const msg of msgs) {
        try {
          await handler(msg);
          await this.ack(msg.id);
        } catch (err) {
          this.log.warn('handler failed; leaving unacked', {
            id: msg.id,
            err: err instanceof Error ? err.message : String(err),
          });
        }
      }
    }
  }

  stop(): void {
    this.stopped = true;
  }

  async close(): Promise<void> {
    this.stop();
    if (this.ownsRedis) {
      await this.redis.quit().catch(() => this.redis.disconnect());
    }
  }
}
