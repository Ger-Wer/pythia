#!/usr/bin/env bun
/**
 * Standalone `bun run db:pg:listen` runner — tails the dual-loop NOTIFY
 * channels and prints each envelope to stdout as a single JSON line.
 *
 * Useful for operators ("is my shadow write landing?") and for the
 * gateway-api smoke test mentioned in
 * `docs/dual_loop/db_migration.md` § 6. Intentionally tiny — no retry
 * logic, no filters, no metrics. Real consumers should use the
 * `PgListener` class directly (see `src/services/pg_listener.ts`).
 */
import { PgListener, DUAL_LOOP_CHANNELS } from '../src/services/pg_listener';

const url =
  process.env.DUAL_LOOP_PG_URL ??
  process.env.DATABASE_URL ??
  'postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev';

const listener = new PgListener({ url, channels: DUAL_LOOP_CHANNELS });
listener.on((event, channel) => {
  // eslint-disable-next-line no-console
  console.log(JSON.stringify({ channel, event }));
});

async function main(): Promise<void> {
  await listener.start();
  // eslint-disable-next-line no-console
  console.error(
    `[pg_listen] subscribed to ${DUAL_LOOP_CHANNELS.length} channels on ${url}. Ctrl+C to stop.`,
  );
}

async function shutdown(): Promise<void> {
  await listener.stop();
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

await main();
