// Password verification for the single hardcoded operator (MVP).
// TODO (Phase 3): replace with OIDC/SSO + operator directory.

import bcrypt from "bcryptjs";

export async function verifyPassword(
  plain: string,
  hash: string,
): Promise<boolean> {
  return bcrypt.compare(plain, hash);
}

export async function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, 10);
}
