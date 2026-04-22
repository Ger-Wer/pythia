#!/usr/bin/env bash
# Generate TypeScript protobuf clients into src/generated/.
#
# Requires:
#   * `buf` CLI on PATH (bufbuild/buf >= v1.47).
#   * `bun install` already ran (for the local `protoc-gen-ts_proto` plugin).
#
# Output is git-ignored; regenerate on proto changes with `bun run proto:gen`
# or `make proto-gen-ts`.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v buf >/dev/null 2>&1; then
  echo "error: buf CLI not found on PATH; install from https://buf.build" >&2
  exit 1
fi

if [[ ! -x "node_modules/.bin/protoc-gen-ts_proto" ]]; then
  echo "error: ts-proto plugin missing — run 'bun install' first" >&2
  exit 1
fi

mkdir -p src/generated

cd schemas
exec buf generate
