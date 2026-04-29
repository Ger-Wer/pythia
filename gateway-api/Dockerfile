# Dual-Loop gateway-api.
# Runtime: Bun 1.x. Dev image builds protobuf TS codecs from the monorepo
# schemas so the Redis bridge can decode live protobuf payloads.

FROM oven/bun:1.3.13-alpine AS proto-build

WORKDIR /app

RUN apk add --no-cache protobuf

COPY package.json ./
COPY bunfig.toml ./
COPY schemas ./schemas

RUN bun install

RUN mkdir -p src/generated && \
    protoc \
      --plugin=./node_modules/.bin/protoc-gen-ts_proto \
      --ts_proto_out=src/generated \
      --ts_proto_opt=esModuleInterop=true \
      --ts_proto_opt=outputServices=false \
      --ts_proto_opt=useOptionals=messages \
      --ts_proto_opt=env=node \
      --ts_proto_opt=forceLong=bigint \
      --ts_proto_opt=oneof=unions \
      -I schemas \
      schemas/dual_loop/v1/*.proto

FROM oven/bun:1.3.13-alpine AS runtime

WORKDIR /app/gateway-api

COPY gateway-api/package.json ./
COPY gateway-api/tsconfig.json ./

# Install deps for the dev/runtime image. `bun install` is compatible with
# current Bun CLI, while `--production=false` is not.
RUN bun install

COPY gateway-api/src ./src
COPY --from=proto-build /app/src/generated /app/src/generated

ENV NODE_ENV=production
ENV PORT=9103
ENV HOST=0.0.0.0

EXPOSE 9103

# Liveness: /api/v1/healthz on 9103.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1:9103/api/v1/healthz || exit 1

CMD ["bun", "run", "src/index.ts"]
