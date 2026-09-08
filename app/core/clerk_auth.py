"""Clerk session JWT verification."""

from __future__ import annotations

from typing import Any, Optional

import jwt
from jwt import PyJWKClient

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(issuer: str) -> PyJWKClient:
    url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
    if url not in _jwks_clients:
        _jwks_clients[url] = PyJWKClient(url, cache_keys=True)
    return _jwks_clients[url]


def verify_clerk_token(token: str) -> Optional[dict[str, Any]]:
    """Verify a Clerk session JWT and return claims, or None if invalid."""
    try:
        unverified = jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
        )
        issuer = settings.clerk_issuer or unverified.get("iss")
        if not issuer or "clerk" not in str(issuer).lower():
            # Not a Clerk token — let legacy JWT auth handle it.
            return None

        key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        logger.warning("clerk_jwt_invalid", error=str(exc))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("clerk_jwt_verify_failed", error=str(exc))
        return None
