// Auth routes.
//
// POST /api/v1/auth/token — issues a PASETO v4.local token for the hardcoded
// operator. Body: {operator_id, password}.

import type { FastifyInstance } from "fastify";
import type { Config } from "../config.ts";
import { issueToken, verifyToken } from "../auth/paseto.ts";
import { verifyPassword } from "../auth/password.ts";
import type { AuditLog } from "../audit.ts";

interface TokenRequest {
  operator_id: string;
  password: string;
}

// Fastify decorate: request.user populated by preHandler when a valid token
// is presented.
declare module "fastify" {
  interface FastifyRequest {
    user?: { operator_id: string; exp: number };
  }
}

export async function authenticate(
  app: FastifyInstance,
  config: Config,
  rawToken: string | undefined,
): Promise<{ operator_id: string; exp: number } | null> {
  if (!rawToken) return null;
  try {
    const claims = await verifyToken(config.pasetoKey, rawToken);
    return { operator_id: claims.sub, exp: claims.exp };
  } catch (err) {
    app.log.debug({ err: (err as Error).message }, "token verify failed");
    return null;
  }
}

export function registerAuthRoutes(
  app: FastifyInstance,
  deps: { config: Config; audit: AuditLog },
): void {
  const { config, audit } = deps;

  // Global preHandler: attach `req.user` if an Authorization: Bearer header
  // (or `?token=` query param) is present and valid. Routes choose whether
  // to require auth individually.
  app.addHook("preHandler", async (req) => {
    const header = req.headers.authorization;
    let token: string | undefined;
    if (header && header.startsWith("Bearer ")) {
      token = header.slice("Bearer ".length).trim();
    } else if (typeof req.query === "object" && req.query) {
      const q = req.query as Record<string, unknown>;
      if (typeof q.token === "string") token = q.token;
    }
    const user = await authenticate(app, config, token);
    if (user) req.user = user;
  });

  app.post<{ Body: TokenRequest }>(
    "/api/v1/auth/token",
    {
      schema: {
        body: {
          type: "object",
          required: ["operator_id", "password"],
          additionalProperties: false,
          properties: {
            operator_id: { type: "string", minLength: 1, maxLength: 64 },
            password: { type: "string", minLength: 1, maxLength: 256 },
          },
        },
        response: {
          200: {
            type: "object",
            required: ["token", "operator_id", "exp_unix_ms"],
            properties: {
              token: { type: "string" },
              operator_id: { type: "string" },
              exp_unix_ms: { type: "integer" },
            },
          },
          401: { type: "object" },
        },
      },
      config: { rateLimit: { max: 5, timeWindow: "1 minute" } },
    },
    async (req, reply) => {
      const { operator_id, password } = req.body;
      if (operator_id !== config.operatorId) {
        reply.code(401);
        return { error: "invalid_credentials" };
      }
      const ok = await verifyPassword(password, config.operatorPasswordHash);
      if (!ok) {
        reply.code(401);
        return { error: "invalid_credentials" };
      }
      const { token, claims } = await issueToken(
        config.pasetoKey,
        operator_id,
        config.tokenTtlSeconds,
      );
      const expMs = Date.parse(claims.exp);
      await audit.append({
        ts_unix_ms: Date.now(),
        kind: "auth",
        operator_id,
        action: "issue_token",
        detail: { exp: claims.exp },
      });
      return {
        token,
        operator_id,
        exp_unix_ms: expMs,
      };
    },
  );
}

class UnauthorizedError extends Error {
  readonly statusCode = 401;
  constructor(message = "unauthorized") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export function requireAuth(
  _app: FastifyInstance,
): (
  req: import("fastify").FastifyRequest,
  reply: import("fastify").FastifyReply,
) => Promise<void> {
  return async (req, _reply) => {
    if (!req.user) {
      throw new UnauthorizedError();
    }
  };
}

export function installAuthErrorHandler(app: FastifyInstance): void {
  app.setErrorHandler((err: Error, _req, reply) => {
    if (err instanceof UnauthorizedError || err.name === "UnauthorizedError") {
      reply.code(401).send({ error: "unauthorized" });
      return;
    }
    reply.send(err);
  });
}
