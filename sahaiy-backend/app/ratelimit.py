"""
app/ratelimit.py
────────────────
In-process rate limiting for expensive endpoints (issue #4, item 4).

Scope decision: single-box demo deploy → an asyncio token-bucket keyed by
client identity is sufficient and adds zero new services. When we scale out
horizontally this should move to Redis (tracked separately).

Limits:
    POST /stt/transcribe   → RATE_LIMIT_STT_RPM requests/min per identity
    WS   /ws/audio         → RATE_LIMIT_WS_PER_MIN connections/min per identity

Identity: token-derived user_id when AUTH_ENFORCED=true, else client IP.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Request

from app.config import AUTH_ENFORCED, RATE_LIMIT_STT_RPM, RATE_LIMIT_WS_PER_MIN
from app.errors import ApiError

logger = logging.getLogger(__name__)


def _window_key(identity: str, window_start: float) -> str:
    return f"{identity}:{int(window_start)}"


class SlidingWindowLimiter:
    """Simple sliding-window counter. Not distributed — per-process only."""

    def __init__(self, max_events: int, window_sec: float = 60.0):
        self.max_events = max_events
        self.window_sec = window_sec
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, identity: str) -> bool:
        """Return True if allowed, False if rate-limited."""
        now = time.monotonic()
        async with self._lock:
            bucket = self._events[identity]
            cutoff = now - self.window_sec
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_events:
                return False
            bucket.append(now)
            return True


_stt_limiter = SlidingWindowLimiter(RATE_LIMIT_STT_RPM)
_ws_limiter = SlidingWindowLimiter(RATE_LIMIT_WS_PER_MIN)


async def client_identity(request: Optional[Request] = None) -> str:
    """Best-effort caller identity for legacy mode: client host."""
    if request is not None:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        client = request.client
        if client and client.host:
            return client.host
    return "unknown"


async def check_stt_rate(request: Optional[Request], user_id: Optional[str]) -> None:
    """
    Rate-limit gate for POST /stt/transcribe.
    Raises ApiError(429) in the uniform envelope when exceeded.
    """
    if RATE_LIMIT_STT_RPM <= 0:
        return
    identity = user_id or await client_identity(request)
    if not AUTH_ENFORCED and user_id is None:
        identity = f"ip:{identity}"
    else:
        identity = f"user:{identity}"
    allowed = await _stt_limiter.check(identity)
    if not allowed:
        logger.warning("[RateLimit] STT limit exceeded for %s", identity)
        raise ApiError(429, "rate_limited",
                       f"Too many transcription requests. Limit is {RATE_LIMIT_STT_RPM}/min.")


async def check_ws_rate(user_id: Optional[str]) -> bool:
    """
    Rate-limit gate for new /ws/audio connections.
    Returns False when the connection should be rejected.
    """
    if RATE_LIMIT_WS_PER_MIN <= 0:
        return True
    identity = f"user:{user_id}" if user_id else "anon"
    allowed = await _ws_limiter.check(identity)
    if not allowed:
        logger.warning("[RateLimit] WS connect limit exceeded for %s", identity)
    return allowed
