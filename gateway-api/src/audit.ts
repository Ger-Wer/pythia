// Append-only audit log stored in Redis lists.
//
// Every operator decision and kill command is recorded with a canonical
// envelope so auditors can replay history. We use a per-kind Redis list
// (LPUSH / LRANGE) keyed `gateway:audit:<kind>`; lists are trimmed to a
// reasonable max so a pathological producer can't exhaust memory.

import type { Redis } from "ioredis";

export type AuditKind = "decision" | "kill" | "auth";
const MAX_ENTRIES = 100_000;

export interface AuditEntry {
  ts_unix_ms: number;
  kind: AuditKind;
  operator_id: string;
  action: string;
  detail: Record<string, unknown>;
}

export class AuditLog {
  constructor(private readonly redis: Redis | null) {}

  async append(entry: AuditEntry): Promise<void> {
    if (!this.redis) return;
    const key = `gateway:audit:${entry.kind}`;
    const line = JSON.stringify(entry);
    const pipe = this.redis.pipeline();
    pipe.lpush(key, line);
    pipe.ltrim(key, 0, MAX_ENTRIES - 1);
    await pipe.exec();
  }

  async recent(kind: AuditKind, limit: number): Promise<AuditEntry[]> {
    if (!this.redis) return [];
    const key = `gateway:audit:${kind}`;
    const raw = await this.redis.lrange(key, 0, Math.max(0, limit - 1));
    const out: AuditEntry[] = [];
    for (const line of raw) {
      try {
        out.push(JSON.parse(line) as AuditEntry);
      } catch {
        /* skip */
      }
    }
    return out;
  }
}
