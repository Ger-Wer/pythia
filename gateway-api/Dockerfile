# Dual-Loop gateway-api.
# Runtime: Bun 1.x. Single-stage image for dev; prod builds should add multi-stage.

FROM oven/bun:1.3.13-alpine AS base

WORKDIR /app

COPY package.json ./
COPY tsconfig.json ./
COPY bunfig.toml ./

# Install deps (no lockfile yet in MVP; `bun install` produces one).
RUN bun install --production=false

COPY src ./src

ENV NODE_ENV=production
ENV PORT=9103
ENV HOST=0.0.0.0

EXPOSE 9103

# Liveness: /api/v1/healthz on 9103.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1:9103/api/v1/healthz || exit 1

CMD ["bun", "run", "src/index.ts"]
