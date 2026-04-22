// GET /api/v1/state/snapshot — returns the current UI-facing state projection.
// Used as a baseline for REST-only clients and as the `snapshot` payload
// pushed on WS connect / periodic re-sync.

import type { FastifyInstance } from "fastify";
import type { Config } from "../config.ts";
import { requireAuth } from "./auth.ts";
import type { EventStore } from "../state/store.ts";

export function registerSnapshotRoutes(
  app: FastifyInstance,
  deps: { config: Config; store: EventStore },
): void {
  const { store } = deps;
  app.get(
    "/api/v1/state/snapshot",
    { preHandler: requireAuth(app) },
    async (_req, _reply) => {
      const s = store.getState();
      return {
        server_version: s.server_version,
        data: {
          signals: s.signals,
          watchlist: s.watchlist,
          triggers: s.triggers,
          exec_status: s.exec_status,
          kill: s.kill,
        },
      };
    },
  );
}
