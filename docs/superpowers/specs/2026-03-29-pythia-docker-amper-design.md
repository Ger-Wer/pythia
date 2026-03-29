# Pythia Docker Deployment Design on `amper`

Date: 2026-03-29
Status: Approved for planning
Scope: Deployment architecture and operational model only. Core Pythia forecasting logic remains unchanged unless a deployment requirement forces a narrow compatibility change.

## Goal

Move Pythia from the original Hetzner bare-metal deployment plan to a Docker-first deployment model on the existing Oracle server `amper`, while keeping Pythia operationally isolated from `SproutSignal`.

This design replaces `setup.sh + host cron` as the primary deployment path. The Docker path becomes the single active deployment model in documentation and operations.

## Constraints

- Pythia and SproutSignal run on the same host, so they cannot be completely isolated at the kernel or Docker daemon level.
- Within that limit, Pythia must be isolated operationally:
  - separate Linux user
  - separate repository path
  - separate Docker Compose project
  - separate network
  - separate data and logs
  - no inbound ports
  - no shared volumes or runtime paths
- Pythia remains cron-style batch software:
  - daily forecast run
  - daily resolution checker
  - weekly analysis and Telegram reporting
- The business logic in `src/**` and `python/**` should remain stable and reusable.

## Current Host Context

- Host: `amper`
- Existing project on host: `SproutSignal`
- Host has enough spare CPU, RAM, and disk for Pythia.
- Pythia does not need public HTTP ingress.
- Pythia only needs outbound HTTPS to DeepSeek, Polymarket, Exa or Tavily, and Telegram.

## Evaluated Approaches

### Approach A: Separate VPS

Deploy Pythia on its own server as originally planned.

Pros:
- strongest isolation boundary
- no shared host risk with SproutSignal

Cons:
- extra monthly cost
- extra host to manage
- unnecessary for current Pythia resource needs

### Approach B: Shared host, host-native runtime

Install Bun, Python, and cron directly on `amper`.

Pros:
- simple to start
- low container overhead

Cons:
- weaker operational isolation
- higher risk of path, user, cron, and runtime collisions
- harder to keep dependencies reproducible

### Approach C: Shared host, Docker-first deployment

Run Pythia on `amper` inside its own Docker Compose stack under a dedicated user.

Pros:
- strong operational isolation on the existing host
- reproducible runtime
- easy rebuild and update cycle
- avoids exposing ports
- avoids dependency drift on the host

Cons:
- still shares host kernel and Docker daemon with SproutSignal
- container image must handle Bun, Python, and Dexter dependencies correctly on both `amd64` and `arm64`

## Chosen Approach

Approach C is the approved design.

Pythia will run on `amper` under a dedicated Linux user `pythia`, in `/home/pythia/pythia`, with its own Docker Compose project and an internal scheduler based on `supercronic`.

## Deployment Architecture

### Host-Level Isolation

- Linux user: `pythia`
- Repository path: `/home/pythia/pythia`
- All commands are executed from that directory and under that user.
- Pythia must never be operated from `/home/ubuntu/SproutSignal`.

### Compose Project

- Compose project name: `pythia`
- Primary long-running service: `pythia-scheduler`
- One dedicated bridge network: `pythia_internal`
- No published ports
- No shared networks with SproutSignal

### Runtime Model

- A single application image contains:
  - Bun runtime
  - Python 3.11 runtime
  - SQLite tools
  - application code
  - Dexter clone
  - `supercronic`
- The scheduler container runs continuously and executes batch jobs on schedule.
- Manual operations reuse the same image to avoid host/runtime drift.

## Container Build Design

### Base Image

Use `python:3.11-slim` as the Docker base image instead of `ubuntu:22.04`.

Rationale:
- guarantees Python 3.11 in a multi-arch image
- simplifies package management
- reduces image size and moving parts

### Required System Packages

The image should install the minimum packages needed for Pythia and its build chain:

- `curl`
- `git`
- `jq`
- `sqlite3`
- `ca-certificates`
- `unzip`
- `procps`
- `build-essential`
- `pkg-config`

Rationale:
- Bun installer needs `unzip`
- health checks use `pgrep` from `procps`
- `better-sqlite3` may need native compilation, especially on ARM

### Bun Installation

- Install Bun inside the image
- Ensure the Bun binary is on `PATH`
- Keep Bun entirely inside the container runtime; do not require host Bun

### Supercronic

- Install `supercronic` inside the image
- Use the architecture-aware binary chosen by Docker build architecture
- `supercronic` becomes the scheduler process for the long-running container

### Dexter

- Clone Dexter into the image at build time
- Install Dexter dependencies at build time
- Keep Dexter updates tied to image rebuilds

## Compose Service Design

### Service: `pythia-scheduler`

Responsibilities:
- run `supercronic`
- execute scheduled forecast, resolution, and weekly tasks
- emit logs to stdout and to bind-mounted log files as needed

Key properties:
- `restart: unless-stopped`
- `env_file: .env`
- `TZ=UTC`
- bind mounts:
  - `./data:/app/data`
  - `./logs:/app/logs`
- no `.env` bind mount
- no published ports
- attached only to `pythia_internal`

### Resource Boundaries

Prefer Docker Compose service-level runtime limits for actual enforcement:

- `mem_limit: 2g`
- `cpus: 1.0`
- `pids_limit: 256`

Rationale:
- protects SproutSignal from accidental starvation
- matches Pythia's low expected resource usage

### Logging

- Docker JSON logging driver with rotation
- application logs continue to write into `/app/logs`
- log retention should be bounded

### Health Check

- scheduler health check should verify `supercronic` is alive
- use a simple `pgrep`-based check

## Scheduling Model

`supercronic.crontab` becomes the only active scheduler definition.

Planned jobs:

- `06:00 UTC` -> forecast run
- `18:00 UTC` -> resolution checker
- `Sunday 20:00 UTC` -> analysis + weekly Telegram report

There is no host cron for Pythia in the active deployment model.

## Entrypoint and Manual Operations

### `scripts/entrypoint.sh`

Single operational entrypoint for:

- `forecast`
- `resolve`
- `weekly`
- `scheduler`
- `shell`

Responsibilities:
- export container-default paths
- route subcommands to the correct Bun or Python entrypoints
- append task output to date-based log files

### `scripts/manual_run.sh`

Host helper for operators. It wraps `docker compose run` or `docker compose logs` for:

- manual forecast
- manual resolution run
- weekly report
- edge calculation
- DB shell
- logs
- container shell
- status summary

This helper becomes the primary operational interface for routine maintenance.

## Data and Secret Handling

### Persistent Data

Persistent directories:

- `/home/pythia/pythia/data`
- `/home/pythia/pythia/logs`

Container paths:

- `/app/data`
- `/app/logs`

SQLite database path default becomes:

- `DB_PATH=/app/data/pythia.db`

Log directory default becomes:

- `LOG_DIR=/app/logs`

### Secrets

- `.env` lives only at `/home/pythia/pythia/.env`
- secrets are injected with `env_file`
- `.env` is not bind-mounted into the container as a normal file

Rationale:
- keeps runtime simple
- reduces accidental file-level secret exposure inside the container

## Documentation Changes

### README

Rewrite the active quick start around Docker-first deployment on `amper`:

- create user `pythia`
- clone repo into `/home/pythia/pythia`
- copy `.env.example` to `.env`
- run `scripts/day0_check.sh`
- `docker compose build`
- `bash scripts/manual_run.sh forecast`
- `docker compose up -d`

Add an operations section covering:

- `bash scripts/manual_run.sh status`
- `bash scripts/manual_run.sh logs`
- `bash scripts/manual_run.sh edge`
- `git pull && docker compose build && docker compose up -d`

### `docs/ARCHITECTURE.md`

Add a deployment section describing:

- shared host `amper`
- dedicated `pythia` user
- isolated Compose project and network
- no inbound ports
- bind-mounted data and logs
- operational isolation from SproutSignal with shared-host caveat

### `docs/DEPLOYMENT.md`

Create a Docker deployment runbook covering:

- host preparation
- user setup
- clone and configuration
- first build
- smoke test
- scheduler startup
- update process
- operational safety and isolation rules

### Legacy Deployment References

The old Hetzner bare-metal path is removed from active documentation.

That means:
- no active README instructions using `scripts/setup.sh`
- no active README instructions using host `crontab`
- no ambiguous dual deployment story

If legacy files remain in the repository temporarily, they must be clearly de-emphasized until removed.

## Files In Scope for Implementation

### New files

- `Dockerfile`
- `docker-compose.yml`
- `supercronic.crontab`
- `.dockerignore`
- `scripts/entrypoint.sh`
- `scripts/manual_run.sh`
- `docs/DEPLOYMENT.md`

### Updated files

- `README.md`
- `docs/ARCHITECTURE.md`
- `.env.example`

### Out of scope unless required for container compatibility

- `src/**`
- `python/**`

## Operational Safety Rules

- All Pythia work runs as `pythia`
- All Pythia commands are executed from `/home/pythia/pythia`
- No shared Docker network with SproutSignal
- No shared bind mounts with SproutSignal
- No exposed HTTP ports
- No host Bun or host Python dependency for the active runtime

## Success Criteria

The Docker deployment layer is considered ready for implementation when it supports the following operator flow:

1. Clone Pythia into `/home/pythia/pythia`
2. Create `.env`
3. Build the image with `docker compose build`
4. Run a manual forecast with `bash scripts/manual_run.sh forecast`
5. Confirm records are written to SQLite in `data/`
6. Start the scheduler with `docker compose up -d`
7. Observe daily and weekly tasks through container logs and log files

## Non-Goals

- No web UI
- No inbound Telegram webhook service
- No host-based cron for Pythia
- No shared runtime with SproutSignal
- No change to forecasting product scope
