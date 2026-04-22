# Multi-stage build for the Dual-Loop Fast Loop Rust workspace.
#
# Produces small runtime images for `sniper-ingest`, `sniper-executor`,
# `sniper-risk`. The target binary is selected via the BIN build-arg.
#
# Usage:
#   docker build -f services/Dockerfile --build-arg BIN=sniper-executor -t sniper-executor .
#
# Context: the whole repo root (because `build.rs` reads `schemas/`).

FROM rust:1.85-bookworm AS builder

# System deps: protoc is vendored via prost-build crate, nothing else needed
# beyond libssl which ships with the base image.
WORKDIR /build

# Cache dependency compilation: copy only manifests + lockfile first.
COPY services/Cargo.toml services/Cargo.lock* ./services/
COPY services/common/Cargo.toml ./services/common/
COPY services/sniper-ingest/Cargo.toml ./services/sniper-ingest/
COPY services/sniper-executor/Cargo.toml ./services/sniper-executor/
COPY services/sniper-risk/Cargo.toml ./services/sniper-risk/

# Dummy source to materialise the dependency graph.
RUN mkdir -p services/common/src services/sniper-ingest/src \
             services/sniper-executor/src services/sniper-risk/src && \
    echo 'fn main() {}' > services/sniper-ingest/src/main.rs && \
    echo 'fn main() {}' > services/sniper-executor/src/main.rs && \
    echo 'fn main() {}' > services/sniper-risk/src/main.rs && \
    touch services/common/src/lib.rs

# Copy the real sources.
COPY services/ ./services/
COPY schemas/ ./schemas/

ARG BIN=sniper-executor
RUN cd services && cargo build --release --bin ${BIN} && \
    cp target/release/${BIN} /usr/local/bin/dual-loop-bin

# ---------- runtime ----------

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/dual-loop-bin /usr/local/bin/dual-loop-bin

EXPOSE 9101 9102

USER nobody
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/dual-loop-bin"]
