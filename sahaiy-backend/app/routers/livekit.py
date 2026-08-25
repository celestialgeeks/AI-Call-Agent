"""
app/routers/livekit.py
───────────────────────
LiveKit WebRTC token management.
Generates access tokens for clients to join voice rooms.

POST /livekit/token  → returns { token, server_url }
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
from app.errors import ApiError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/livekit", tags=["LiveKit"])

# Optional import — only needed if LiveKit is configured
try:
    from livekit import api
    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False


class TokenRequest(BaseModel):
    room_name: str
    identity: str
    metadata: Optional[str] = None


class TokenResponse(BaseModel):
    token: str
    server_url: str


@router.post("/token", response_model=TokenResponse)
async def get_token(body: TokenRequest):
    """
    Generate an access token for a LiveKit room.
    The client uses this token to connect to the WebRTC room.
    """
    if not _LIVEKIT_AVAILABLE:
        raise ApiError(501, "not_implemented", "LiveKit SDK not installed on server.")

    if not all([LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL]):
        raise ApiError(501, "not_configured", "LiveKit credentials not configured.")

    try:
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(body.identity) \
            .with_name(body.identity) \
            .with_grants(api.VideoGrants(
                room_join=True,
                room=body.room_name,
            )) \
            .with_metadata(body.metadata or "") \
            .to_jwt()

        logger.info("[LiveKit] Generated token for room=%s identity=%s", body.room_name, body.identity)
        return TokenResponse(token=token, server_url=LIVEKIT_URL)

    except ApiError:
        raise
    except Exception as exc:
        logger.error("[LiveKit] Token generation failed: %s", exc)
        raise ApiError(500, "internal_error", "Failed to generate LiveKit token.") from exc
