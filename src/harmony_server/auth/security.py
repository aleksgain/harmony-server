"""Password hashing and JWT issue/verify. Deliberately simple: we control both ends."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from harmony_server.config import Settings

TOKEN_AUDIENCE = "harmony-server"


def _hash_sync(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("ascii")


def _verify_sync(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("ascii"))
    except ValueError:
        return False


async def hash_password(password: str) -> str:
    # bcrypt is CPU-bound; keep the event loop free.
    return await asyncio.to_thread(_hash_sync, password)


async def verify_password(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(_verify_sync, password, hashed)


def issue_token(account_id: int, settings: Settings, scope: str = "user") -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(account_id),
        "aud": TOKEN_AUDIENCE,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


#: Token scopes, mirroring Logitech's three-step chain:
#: ``logi`` (Logi ID access_token) -> ``harmony`` (Security.svc AuthToken) -> ``hub``
#: (GetAuthenticatedUserAuthToken, written into the hub's provisionInfo.authToken).
#: ``user`` is our own REST API token.
SCOPE_USER = "user"
SCOPE_LOGI = "logi"
SCOPE_HARMONY = "harmony"
SCOPE_HUB = "hub"


def decode_token(
    token: str, settings: Settings, scopes: tuple[str, ...] | None = None
) -> int | None:
    """Return the account id, or None. ``scopes`` restricts which token kinds are accepted."""
    token = token.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=TOKEN_AUDIENCE,
        )
        if scopes is not None and payload.get("scope") not in scopes:
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
