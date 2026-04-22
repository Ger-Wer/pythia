// Minimal structured logger.
//
// Fastify ships with pino for request-scoped logs; for background workers and
// bus bridges we want a named logger that matches Fastify's output format but
// doesn't force us to pull pino transitively. Output is JSON lines on stderr.

export type LogLevel = "debug" | "info" | "warn" | "error";

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

export interface Logger {
  info: (msg: unknown, extra?: Record<string, unknown>) => void;
  warn: (msg: unknown, extra?: Record<string, unknown>) => void;
  error: (msg: unknown, extra?: Record<string, unknown>) => void;
  debug: (msg: unknown, extra?: Record<string, unknown>) => void;
  child: (bindings: Record<string, unknown>) => Logger;
}

function emit(
  threshold: number,
  level: LogLevel,
  bindings: Record<string, unknown>,
  msg: unknown,
  extra?: Record<string, unknown>,
): void {
  if (LEVEL_ORDER[level] < threshold) return;
  const payload: Record<string, unknown> = {
    level,
    time: Date.now(),
    ...bindings,
    ...(extra ?? {}),
    msg: typeof msg === "string" ? msg : JSON.stringify(msg),
  };
  process.stderr.write(JSON.stringify(payload) + "\n");
}

export function makeLogger(
  levelName: string,
  bindings: Record<string, unknown> = {},
): Logger {
  const normalized = (
    levelName in LEVEL_ORDER ? levelName : "info"
  ) as LogLevel;
  const threshold = LEVEL_ORDER[normalized];
  return {
    info: (m, e) => emit(threshold, "info", bindings, m, e),
    warn: (m, e) => emit(threshold, "warn", bindings, m, e),
    error: (m, e) => emit(threshold, "error", bindings, m, e),
    debug: (m, e) => emit(threshold, "debug", bindings, m, e),
    child: (extra) => makeLogger(levelName, { ...bindings, ...extra }),
  };
}
