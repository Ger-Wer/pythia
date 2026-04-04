from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from itsdangerous import URLSafeSerializer


@dataclass(frozen=True)
class DashboardAuthConfig:
    session_secret: str
    dashboard_username: str
    dashboard_password_hash: str


def verify_password(stored_hash: str, password: str) -> bool:
    if stored_hash.startswith("plain:"):
        return hmac.compare_digest(stored_hash[6:], password)
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(stored_hash, digest)


def build_serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt="pythia-dashboard")
