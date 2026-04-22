// PASETO v4.local token helpers.
//
// Uses `paseto-ts` (pure-JS XChaCha20-Poly1305 implementation). The library
// requires the 32-byte symmetric key to be wrapped in a 9-byte `k4.local.`
// magic prefix — we apply that once at startup and keep the wrapped key
// around for both encrypt and decrypt.

import { decrypt, encrypt } from "paseto-ts/v4";

const K4_LOCAL_MAGIC = new TextEncoder().encode("k4.local.");

export interface TokenClaims {
  sub: string;
  iat: string;
  exp: string;
  // exp / iat are ISO 8601 strings per PASETO spec.
}

function wrapKey(raw: Buffer): Uint8Array {
  const out = new Uint8Array(K4_LOCAL_MAGIC.length + raw.length);
  out.set(K4_LOCAL_MAGIC, 0);
  out.set(new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength), K4_LOCAL_MAGIC.length);
  return out;
}

export async function issueToken(
  key: Buffer,
  operatorId: string,
  ttlSeconds: number,
): Promise<{ token: string; claims: TokenClaims }> {
  const wrapped = wrapKey(key);
  const now = new Date();
  const exp = new Date(now.getTime() + ttlSeconds * 1000);
  const claims: TokenClaims = {
    sub: operatorId,
    iat: now.toISOString(),
    exp: exp.toISOString(),
  };
  const token = encrypt(wrapped, claims);
  return { token, claims };
}

export async function verifyToken(
  key: Buffer,
  token: string,
): Promise<{ sub: string; exp: number }> {
  const wrapped = wrapKey(key);
  const { payload } = decrypt<TokenClaims>(wrapped, token);
  if (typeof payload.sub !== "string" || typeof payload.exp !== "string") {
    throw new Error("invalid token claims");
  }
  const expMs = Date.parse(payload.exp);
  if (!Number.isFinite(expMs)) throw new Error("invalid exp");
  if (expMs < Date.now()) throw new Error("token expired");
  return { sub: payload.sub, exp: Math.floor(expMs / 1000) };
}
