"""
app/auth.py
───────────
JWT verification dependency.

Per ruling B1 (issue #4) and the issue #7 contract: every campaign endpoint
derives user_id from a Supabase HS256 access token in the
`Authorization: Bearer <jwt>` header — NEVER from body/query params.

Verification uses SUPABASE_JWT_SECRET (HS256). When SUPABASE_JWT_SECRET is not
configured (local dev without auth), AUTH_ENFORCED=false lets requests fall back
to a client-declared X-User-Id header so the rest of the stack stays testable;
the flag must be flipped to true in any shared/prod deployment.
"""

import logging
from typing import Optional

import jwt  # PyJWT
from fastapi import Header, HTTPException, status

from app.config import AUTH_ENFORCED, SUPABASE_JWT_SECRET

logger = logging.getLogger(__name__)

ALGORITHMS = ["HS256"]
_missing = object()


def _unauthorized(detail: str = "Missing or invalid Authorization header") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _verify_token(token: str) -> str:
    """Decode a Supabase HS256 JWT and return the authenticated user id (sub)."""
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=ALGORITHMS,
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid token") from exc

    sub = payload.get("sub")
    if not sub:
        raise _unauthorized("Token missing sub claim")
    return sub


async def get_current_user_id(
    authorization: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Optional[str]:
    """
    FastAPI dependency: resolve the caller's user_id.

    - Enforced mode: requires `Authorization: Bearer <supabase_jwt>`; returns token `sub`.
    - Unenforced dev mode (AUTH_ENFORCED=false and no secret configured):
      falls back to `X-User-Id` header so local flows keep working.
    """
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized("Authorization header must be 'Bearer <token>'")
        if SUPABASE_JWT_SECRET:
            return _verify_token(token.strip())
        # Bearer presented but no secret configured → cannot verify.
        raise _unauthorized("Server missing SUPABASE_JWT_SECRET — cannot verify token")

    if AUTH_ENFORCED or SUPABASE_JWT_SECRET:
        raise _unauthorized()

    # Dev fallback only.
    if x_user_id:
        return x_user_id.strip()
    # Legacy mode (flag off, no secret): no identity from headers → None so
    # routers apply their own client-declared user_id resolution (e.g.
    # knowledge ingest form/body fields). Raising here would break the
    # documented AUTH_ENFORCED flag contract for legacy routers.
    return None


async def get_websocket_user_id(websocket) -> Optional[str]:
    """
    WebSocket counterpart of get_current_user_id.

    AUTH_ENFORCED=False → None (legacy query-param user_id still accepted).
    AUTH_ENFORCED=True  → identity MUST come from the authenticated connection:
                          Bearer token on the upgrade request. The WS handshake
                          query-param `user_id` is ignored once the flag is on.

    Raises PermissionError when enforcement is on and no valid identity can
    be derived — callers should reject the connection before accept().
    """
    if not AUTH_ENFORCED:
        return None

    header = websocket.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise PermissionError("missing bearer token on WebSocket upgrade")
    try:
        return _verify_token(token.strip())
    except HTTPException as exc:
        raise PermissionError(str(exc.detail)) from exc
