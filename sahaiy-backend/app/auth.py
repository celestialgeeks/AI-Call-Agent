"""
app/auth.py
───────────
JWT authentication dependency — Ruling B1.

Verification: Supabase-issued JWTs signed with HS256 via SUPABASE_JWT_SECRET.
Identity:     user_id is derived from the verified token ONLY (`sub` claim) —
              NEVER from the request body or query string (SEC-01/SEC-02).

Feature flag: when AUTH_ENFORCED is False (default until @qa-engineer flips it
after QA green), `get_current_user_id` returns None and routers keep their
legacy client-declared user_id behaviour. When True, requests without a valid
Bearer token receive 401 in the uniform error envelope.

Dev fallback: when the flag is OFF and no SUPABASE_JWT_SECRET is configured,
an `X-User-Id` header is accepted so local flows (and main's campaign
endpoints, which expect a non-None str) stay testable without a Supabase
secret. The fallback is hard-disabled whenever AUTH_ENFORCED=true OR a JWT
secret exists.
"""

import logging
from typing import Optional

import jwt as pyjwt
from fastapi import Header, Request

from app.config import AUTH_ENFORCED, SUPABASE_JWT_SECRET
from app.errors import ApiError, new_request_id

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "


def verify_supabase_jwt(token: str) -> dict:
    """
    Verify a Supabase HS256 JWT and return its claims.

    Raises ApiError(401) on missing/invalid/expired tokens or server misconfig.
    """
    if not SUPABASE_JWT_SECRET:
        logger.error("[Auth] SUPABASE_JWT_SECRET not configured while AUTH_ENFORCED=true")
        raise ApiError(500, "auth_misconfigured", "Authentication is not configured.")

    try:
        claims = pyjwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={
                "require": ["exp", "sub"],
                # Supabase stamps `aud: "authenticated"` on its JWTs; we don't
                # couple verification to that value (anon vs authenticated is
                # enforced by RLS, not by this layer). Signature + expiry are
                # what matters here.
                "verify_aud": False,
            },
        )
    except pyjwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "Access token has expired.")
    except pyjwt.InvalidTokenError:
        raise ApiError(401, "invalid_token", "Invalid access token.")
    return claims


def extract_bearer_token(request: Request) -> Optional[str]:
    """Return the raw JWT from `Authorization: Bearer *** else None."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):].strip() or None
    return None


def _dev_fallback_user_id(x_user_id: Optional[str]) -> Optional[str]:
    """
    X-User-Id dev fallback — ONLY when auth enforcement is fully off
    (AUTH_ENFORCED=false AND no SUPABASE_JWT_SECRET configured).
    """
    if AUTH_ENFORCED or SUPABASE_JWT_SECRET:
        raise ApiError(
            401, "missing_token",
            "Authorization: Bearer <token> required "
            "(X-User-Id fallback only works with auth unconfigured).",
        )
    if x_user_id:
        return x_user_id.strip()
    raise ApiError(
        401, "missing_token",
        "Provide Authorization: Bearer <token> (or X-User-Id in unenforced dev mode).",
    )


async def get_current_user_id(
    request: Request,
    x_user_id: Optional[str] = Header(default=None),
) -> Optional[str]:
    """
    FastAPI dependency: resolve the caller's user_id.

    AUTH_ENFORCED=False → None when no X-User-Id dev fallback applies; routers
                          use their legacy client-declared id behaviour.
    AUTH_ENFORCED=True  → token-derived `sub`; raises 401 envelope on failure.
    """
    token = extract_bearer_token(request)
    if token:
        claims = verify_supabase_jwt(token)
        user_id = str(claims.get("sub") or "").strip()
        if not user_id:
            raise ApiError(401, "invalid_token", "Token has no subject claim.")
        return user_id

    if AUTH_ENFORCED:
        raise ApiError(401, "missing_token", "Authorization: Bearer <token> required.")

    # Fully-unconfigured dev mode: allow the X-User-Id fallback.
    if not SUPABASE_JWT_SECRET and x_user_id:
        return x_user_id.strip()
    return None


async def get_websocket_user_id(websocket) -> Optional[str]:
    """
    WebSocket counterpart of get_current_user_id.

    AUTH_ENFORCED=False → None (legacy query-param user_id still accepted).
    AUTH_ENFORCED=True  → identity MUST come from the authenticated connection:
                          Bearer token on the upgrade request. The WS handshake
                          query-param `user_id` is ignored once the flag is on.
    """
    if not AUTH_ENFORCED:
        return None

    token = extract_bearer_token_from_headers(websocket.headers)
    if not token:
        raise PermissionError("missing bearer token")
    claims = verify_supabase_jwt(token)
    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise PermissionError("token has no subject claim")
    return user_id


def extract_bearer_token_from_headers(headers) -> Optional[str]:
    header = headers.get("authorization") or ""
    if header.lower().startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):].strip() or None
    return None


__all__ = [
    "get_current_user_id",
    "get_websocket_user_id",
    "verify_supabase_jwt",
    "extract_bearer_token",
    "new_request_id",
]
