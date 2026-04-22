import type { Config } from 'drizzle-kit';

/**
 * Drizzle-kit config for the Postgres shadow adapter.
 *
 * - `dialect: "postgresql"` — the Dual-Loop persistent store (see
 *   `docs/dual_loop/01-containers.md` C3 `pythia-db`).
 * - `schema` — single source of truth for the shadow schema
 *   (mirrors `src/services/db.ts` + `sql/*.sql`).
 * - `out` — generated migrations live under `sql/migrations/`
 *   and are applied via `scripts/pg_migrate.sh`.
 *
 * Connection URL defaults match `docker-compose.dev.yml` (`pythia_dev`).
 * Override via `DUAL_LOOP_PG_URL` or `DATABASE_URL` env vars.
 */
const connectionUrl =
  process.env.DUAL_LOOP_PG_URL ??
  process.env.DATABASE_URL ??
  'postgres://pythia:pythia_dev_password@127.0.0.1:5432/pythia_dev';

export default {
  dialect: 'postgresql',
  schema: './src/db/pg/schema.ts',
  out: './sql/migrations',
  dbCredentials: {
    url: connectionUrl,
  },
  strict: true,
  verbose: true,
} satisfies Config;
