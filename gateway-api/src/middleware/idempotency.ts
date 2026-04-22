// Redis-backed idempotency cache.
//
// Each decision endpoint accepts an `idempotency_key` in the request body.
// We atomically register the key with SET NX PX; if the key already exists,
// we return the previously-stored response verbatim with the same status.

import type { Redis } from "ioredis";

export interface StoredResult {
  status: number;
  body: Record<string, unknown>;
}

function keyFor(operatorId: string, idempotencyKey: string): string {
  return `gateway:idem:${operatorId}:${idempotencyKey}`;
}

export interface IdempotencyStore {
  // Returns the previously-stored result if the key has already been used.
  // Otherwise reserves the slot and returns null. The reservation holds for
  // `ttlSeconds`; the caller must call `save()` to populate the slot with
  // the final result before the TTL expires.
  reserveOrGet(
    operatorId: string,
    idempotencyKey: string,
    ttlSeconds: number,
  ): Promise<StoredResult | null>;
  save(
    operatorId: string,
    idempotencyKey: string,
    result: StoredResult,
    ttlSeconds: number,
  ): Promise<void>;
}

const RESERVED = "__reserved__";

export class RedisIdempotencyStore implements IdempotencyStore {
  constructor(private readonly redis: Redis) {}

  async reserveOrGet(
    operatorId: string,
    idempotencyKey: string,
    ttlSeconds: number,
  ): Promise<StoredResult | null> {
    const key = keyFor(operatorId, idempotencyKey);
    const ok = await this.redis.set(
      key,
      RESERVED,
      "EX",
      ttlSeconds,
      "NX",
    );
    if (ok === "OK") {
      return null; // reservation granted
    }
    // Key exists. Busy-wait briefly for in-flight request to populate the
    // slot, then return whatever is there.
    for (let attempt = 0; attempt < 50; attempt++) {
      const v = await this.redis.get(key);
      if (v && v !== RESERVED) {
        try {
          return JSON.parse(v) as StoredResult;
        } catch {
          return null;
        }
      }
      await new Promise((r) => setTimeout(r, 20));
    }
    return null;
  }

  async save(
    operatorId: string,
    idempotencyKey: string,
    result: StoredResult,
    ttlSeconds: number,
  ): Promise<void> {
    const key = keyFor(operatorId, idempotencyKey);
    await this.redis.set(key, JSON.stringify(result), "EX", ttlSeconds);
  }
}

// In-memory fallback used when Redis is unavailable (tests, dev-light).
export class MemoryIdempotencyStore implements IdempotencyStore {
  private readonly entries = new Map<
    string,
    { value: StoredResult | typeof RESERVED; expiresAt: number }
  >();

  async reserveOrGet(
    operatorId: string,
    idempotencyKey: string,
    ttlSeconds: number,
  ): Promise<StoredResult | null> {
    const now = Date.now();
    const key = keyFor(operatorId, idempotencyKey);
    const existing = this.entries.get(key);
    if (existing && existing.expiresAt > now) {
      if (existing.value === RESERVED) {
        for (let i = 0; i < 50; i++) {
          await new Promise((r) => setTimeout(r, 20));
          const e2 = this.entries.get(key);
          if (e2 && e2.value !== RESERVED && e2.expiresAt > Date.now()) {
            return e2.value;
          }
        }
        return null;
      }
      return existing.value;
    }
    this.entries.set(key, {
      value: RESERVED,
      expiresAt: now + ttlSeconds * 1000,
    });
    return null;
  }

  async save(
    operatorId: string,
    idempotencyKey: string,
    result: StoredResult,
    ttlSeconds: number,
  ): Promise<void> {
    const key = keyFor(operatorId, idempotencyKey);
    this.entries.set(key, {
      value: result,
      expiresAt: Date.now() + ttlSeconds * 1000,
    });
  }
}
