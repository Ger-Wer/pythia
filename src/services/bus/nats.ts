/**
 * NATS JetStream publish-only helper (optional, MVP).
 *
 * `nats.js` is not yet a hard dependency of pythia. Rather than force-pull
 * it, this module exposes a minimal shape and lazy-imports the client so
 * callers can opt in without bloating cold start. If `nats` isn't
 * installed, `JetStreamPublisher.connect` throws a clear error.
 *
 * Rust symmetric: `services/common/src/bus/nats.rs`.
 */

import type { ProtoCodec } from './redis_streams';

export interface JetStreamPublisherOpts {
  url: string;
}

export class JetStreamPublisher {
  // `unknown` because we don't want to require `nats` at import time.
  private readonly js: { publish: (subject: string, data: Uint8Array) => Promise<unknown> };

  private constructor(js: {
    publish: (subject: string, data: Uint8Array) => Promise<unknown>;
  }) {
    this.js = js;
  }

  static async connect(opts: JetStreamPublisherOpts): Promise<JetStreamPublisher> {
    type NatsLike = {
      connect: (o: { servers: string }) => Promise<{
        jetstream: () => {
          publish: (subject: string, data: Uint8Array) => Promise<unknown>;
        };
      }>;
    };

    let mod: NatsLike;
    try {
      // Dynamic `import` so that the 'nats' package can stay optional.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      mod = (await import(/* @vite-ignore */ 'nats' as any)) as unknown as NatsLike;
    } catch {
      throw new Error(
        "optional dependency 'nats' is not installed; `bun add nats` to use JetStreamPublisher",
      );
    }
    const nc = await mod.connect({ servers: opts.url });
    return new JetStreamPublisher(nc.jetstream());
  }

  async publish<T>(subject: string, codec: ProtoCodec<T>, message: T): Promise<void> {
    const bytes = codec.encode(message).finish();
    await this.js.publish(subject, bytes);
  }
}
