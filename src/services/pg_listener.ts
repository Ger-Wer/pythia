/**
 * LISTEN/NOTIFY subscriber for Pythia's Postgres shadow store.
 *
 * Dual-Loop Phase 1B. Pairs with the AFTER INSERT/UPDATE/DELETE triggers
 * installed by `sql/migrations/0001_listen_notify.sql`. Gateway-api (Track B,
 * future) is the production consumer; this module is also used by the
 * standalone `scripts/pg_listen.ts` runner and by the integration tests.
 *
 * Envelope contract (enforced by `sql/migrations/0001_listen_notify.sql`):
 *
 *     { v: 1, op: 'insert' | 'update' | 'delete',
 *       table: 'predictions',
 *       pk: { id: 42 },
 *       market_id?: 'polymarket:0x…',
 *       ts: '2026-04-22T12:34:56.789Z' }
 *
 * Reliability model:
 *   - LISTEN/NOTIFY is best-effort, per-session, non-durable. Subscribers
 *     that need replay must reconcile by polling the shadow store after
 *     reconnect. See `docs/dual_loop/pg_listen_notify.md`.
 *   - The listener auto-reconnects with exponential back-off; it never
 *     swallows unexpected payloads silently — malformed events are logged
 *     and surfaced via the `onError` hook.
 */
import postgres, { type Notice } from 'postgres';

/** Stable channel names — do not rename without bumping `v`. */
export const DUAL_LOOP_CHANNELS = [
  'dual_loop.predictions',
  'dual_loop.signals',
  'dual_loop.decisions',
  'dual_loop.attention_items',
  'dual_loop.watch_dossiers',
  'dual_loop.watch_updates',
] as const;

export type DualLoopChannel = (typeof DUAL_LOOP_CHANNELS)[number];

export type DualLoopOp = 'insert' | 'update' | 'delete';

export interface DualLoopEvent {
  v: 1;
  op: DualLoopOp;
  table: string;
  pk: Record<string, number | string | null>;
  market_id?: string;
  ts: string;
}

export type EventHandler = (
  event: DualLoopEvent,
  channel: DualLoopChannel,
) => void | Promise<void>;

export interface PgListenerOptions {
  /** Postgres connection URL. */
  url: string;
  /** Channel subset; defaults to every known channel. */
  channels?: readonly DualLoopChannel[];
  /** Optional callback for decoding / handler errors (logged by default). */
  onError?: (err: unknown, ctx: { channel?: DualLoopChannel; raw?: string }) => void;
}

/**
 * Thin wrapper over `postgres.listen`. A listener is a one-shot resource —
 * call `start()` once and `stop()` to release the underlying session.
 */
export class PgListener {
  private readonly url: string;
  private readonly channels: readonly DualLoopChannel[];
  private readonly handlers = new Set<EventHandler>();
  private readonly onError: (
    err: unknown,
    ctx: { channel?: DualLoopChannel; raw?: string },
  ) => void;

  private client: ReturnType<typeof postgres> | null = null;
  private unlistenFns: Array<() => Promise<void>> = [];
  private started = false;

  constructor(opts: PgListenerOptions) {
    this.url = opts.url;
    this.channels = opts.channels ?? DUAL_LOOP_CHANNELS;
    this.onError =
      opts.onError ??
      ((err, ctx) => {
        // eslint-disable-next-line no-console
        console.error('[pg_listener] error', { ...ctx, err });
      });
  }

  /** Register a handler invoked for every decoded event. */
  on(handler: EventHandler): void {
    this.handlers.add(handler);
  }

  /** Remove a previously-registered handler. */
  off(handler: EventHandler): void {
    this.handlers.delete(handler);
  }

  /** Open the LISTEN session and subscribe to every requested channel. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

    // `max: 1` keeps LISTEN pinned to a single backend connection. Without
    // it postgres.js would rotate over the pool and we'd miss events.
    this.client = postgres(this.url, {
      max: 1,
      onnotice: (_notice: Notice) => {
        /* swallow non-NOTIFY server notices */
      },
    });

    await Promise.all(
      this.channels.map(async (channel) => {
        const meta = await this.client!.listen(channel, (raw) => {
          this.dispatch(channel, raw);
        });
        this.unlistenFns.push(meta.unlisten);
      }),
    );
  }

  /** Unlisten from every channel and close the underlying connection. */
  async stop(): Promise<void> {
    if (!this.started) return;
    this.started = false;
    await Promise.all(
      this.unlistenFns.map((fn) =>
        fn().catch((err) => this.onError(err, {})),
      ),
    );
    this.unlistenFns = [];
    if (this.client) {
      await this.client.end({ timeout: 5 });
      this.client = null;
    }
  }

  /** Decode a raw NOTIFY payload and fan it out to registered handlers. */
  private dispatch(channel: DualLoopChannel, raw: string): void {
    let event: DualLoopEvent;
    try {
      event = JSON.parse(raw) as DualLoopEvent;
    } catch (err) {
      this.onError(err, { channel, raw });
      return;
    }

    if (event.v !== 1 || typeof event.op !== 'string' || typeof event.table !== 'string') {
      this.onError(new Error('malformed dual-loop event'), { channel, raw });
      return;
    }

    for (const handler of this.handlers) {
      try {
        const result = handler(event, channel);
        if (result && typeof (result as Promise<void>).catch === 'function') {
          (result as Promise<void>).catch((err) =>
            this.onError(err, { channel, raw }),
          );
        }
      } catch (err) {
        this.onError(err, { channel, raw });
      }
    }
  }
}

/**
 * Feature flag parity with the shadow wrapper. The GUC
 * `dual_loop.pg_notify = 'on'` must also be set inside the writing session
 * for triggers to actually fire — see the migration comment.
 */
export function isPgNotifyEnabled(): boolean {
  return process.env.DUAL_LOOP_PG_NOTIFY === 'true';
}
