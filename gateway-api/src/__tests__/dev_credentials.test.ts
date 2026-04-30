import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "bun:test";
import bcrypt from "bcryptjs";

import { loadConfig } from "../config.ts";

const DEV_OPERATOR_ID = "admin";
const DEV_PASSWORD = "changeme";

function readComposeDevCredentials(): {
  operatorId: string;
  operatorPasswordHash: string;
} {
  const composePath = resolve(import.meta.dir, "../../../docker-compose.dev.yml");
  const compose = readFileSync(composePath, "utf8");

  // Values may be inlined ("admin") or env-var references with a dev
  // fallback ("${OPERATOR_ID:-admin}"). Track A3 moved the latter form.
  function extract(key: string): string | undefined {
    const literal = compose.match(new RegExp(`^\\s+${key}:\\s+"([^$"][^"]*)"`, "m"))?.[1];
    if (literal) return literal;
    const fallback = compose.match(
      new RegExp(`^\\s+${key}:\\s+"\\$\\{[A-Z_]+:-([^}]+)\\}"`, "m"),
    )?.[1];
    return fallback;
  }

  const operatorId = extract("OPERATOR_ID");
  const rawHash = extract("OPERATOR_PASSWORD_HASH");

  if (!operatorId || !rawHash) {
    throw new Error("Could not extract gateway-api dev credentials from docker-compose.dev.yml");
  }

  return {
    operatorId,
    // Docker Compose escapes `$` as `$$` in YAML literals.
    operatorPasswordHash: rawHash.replace(/\$\$/g, "$"),
  };
}

describe("gateway-api dev credentials", () => {
  test("loadConfig fallback matches admin/changeme", async () => {
    const prevOperatorId = process.env.OPERATOR_ID;
    const prevOperatorPasswordHash = process.env.OPERATOR_PASSWORD_HASH;

    delete process.env.OPERATOR_ID;
    delete process.env.OPERATOR_PASSWORD_HASH;

    try {
      const config = loadConfig();

      expect(config.operatorId).toBe(DEV_OPERATOR_ID);
      await expect(
        bcrypt.compare(DEV_PASSWORD, config.operatorPasswordHash),
      ).resolves.toBe(true);
    } finally {
      if (prevOperatorId === undefined) {
        delete process.env.OPERATOR_ID;
      } else {
        process.env.OPERATOR_ID = prevOperatorId;
      }

      if (prevOperatorPasswordHash === undefined) {
        delete process.env.OPERATOR_PASSWORD_HASH;
      } else {
        process.env.OPERATOR_PASSWORD_HASH = prevOperatorPasswordHash;
      }
    }
  });

  test("docker-compose dev gateway credentials match admin/changeme", async () => {
    const creds = readComposeDevCredentials();

    expect(creds.operatorId).toBe(DEV_OPERATOR_ID);
    await expect(
      bcrypt.compare(DEV_PASSWORD, creds.operatorPasswordHash),
    ).resolves.toBe(true);
  });
});
