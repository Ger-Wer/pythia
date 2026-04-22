// EventStore: in-memory projection of bus events, with JSON Patch diffing.
//
// Semantics:
//   * `state` is the single source of truth exposed to UI / REST snapshot.
//   * Every mutation goes through `apply(mut)` which bumps server_version,
//     computes the JSON Patch for each touched topic, and broadcasts to
//     subscribers.
//   * Subscribers are plain callbacks registered per-topic; the WS layer
//     uses them to fan out deltas.
//   * State snapshots are persisted to Redis (`gateway:state`) every
//     `snapshotIntervalMs` and loaded on boot so gateway reboots are
//     transparent to the UI — on WS connect we always send a full snapshot
//     anyway, so persistence is an optimization, not a correctness concern.

import * as jsonpatch from "fast-json-patch";
import type { Redis } from "ioredis";
import type { Logger } from "../logger.ts";
import {
  type GatewayState,
  type JsonPatchOp,
  type Topic,
  emptyState,
} from "../types.ts";

export type Mutator = (s: GatewayState) => void;
export type Subscriber = (
  topic: Topic,
  patch: readonly JsonPatchOp[],
  serverVersion: number,
) => void;

const REDIS_STATE_KEY = "gateway:state";

const TOPIC_ROOT: Record<Topic, keyof GatewayState> = {
  signals: "signals",
  watchlist: "watchlist",
  triggers: "triggers",
  exec_status: "exec_status",
};

export class EventStore {
  private state: GatewayState;
  private readonly subscribers = new Set<Subscriber>();
  private readonly log: Logger;
  private readonly redis: Redis | null;
  private persistTimer: NodeJS.Timeout | null = null;

  constructor(opts: {
    log: Logger;
    redis: Redis | null;
    snapshotIntervalMs: number;
  }) {
    this.state = emptyState();
    this.log = opts.log.child({ component: "event-store" });
    this.redis = opts.redis;
    if (this.redis && opts.snapshotIntervalMs > 0) {
      this.persistTimer = setInterval(() => {
        void this.persist();
      }, opts.snapshotIntervalMs);
      if (typeof this.persistTimer.unref === "function") {
        this.persistTimer.unref();
      }
    }
  }

  async start(): Promise<void> {
    if (!this.redis) return;
    try {
      const raw = await this.redis.get(REDIS_STATE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<GatewayState>;
      this.state = { ...emptyState(), ...parsed };
      this.log.info("state restored from redis", {
        server_version: this.state.server_version,
        signals: Object.keys(this.state.signals).length,
        watchlist: Object.keys(this.state.watchlist).length,
        triggers: Object.keys(this.state.triggers).length,
        exec_status: Object.keys(this.state.exec_status).length,
      });
    } catch (err) {
      this.log.warn("state restore failed; starting empty", {
        err: (err as Error).message,
      });
    }
  }

  async stop(): Promise<void> {
    if (this.persistTimer) clearInterval(this.persistTimer);
    await this.persist();
  }

  private async persist(): Promise<void> {
    if (!this.redis) return;
    try {
      await this.redis.set(REDIS_STATE_KEY, JSON.stringify(this.state));
    } catch (err) {
      this.log.warn("state persist failed", { err: (err as Error).message });
    }
  }

  getState(): GatewayState {
    return this.state;
  }

  getServerVersion(): number {
    return this.state.server_version;
  }

  subscribe(cb: Subscriber): () => void {
    this.subscribers.add(cb);
    return () => this.subscribers.delete(cb);
  }

  // Apply a mutation. Computes per-topic JSON Patches and broadcasts them.
  // Returns the new server_version.
  apply(topic: Topic, mut: Mutator): number {
    const root = TOPIC_ROOT[topic];
    const before = jsonpatch.deepClone(this.state[root]);
    mut(this.state);
    const after = this.state[root];
    const ops = jsonpatch.compare(
      before as object,
      after as object,
    ) as JsonPatchOp[];
    if (ops.length === 0) {
      return this.state.server_version;
    }
    this.state.server_version += 1;
    // Rebase paths so they match the on-wire contract:
    // the delta describes changes relative to the topic root,
    // e.g. `/<signal_id>/edge` inside the `signals` topic.
    const v = this.state.server_version;
    for (const sub of this.subscribers) {
      try {
        sub(topic, ops, v);
      } catch (err) {
        this.log.warn("subscriber threw", { err: (err as Error).message });
      }
    }
    return v;
  }

  // Helper for ops that touch fields outside a topic root (e.g. kill switch).
  // Increments version without emitting a patch.
  bumpVersion(): number {
    this.state.server_version += 1;
    return this.state.server_version;
  }
}
