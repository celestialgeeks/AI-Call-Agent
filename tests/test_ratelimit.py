"""
tests/test_ratelimit.py
──────────────────────
Rate limits on /stt/transcribe (429 in the uniform envelope) and the WS
limiter's decision function.
"""
from unittest.mock import patch

import pytest

from app.ratelimit import SlidingWindowLimiter, check_ws_rate


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_sliding_window_rejects_over_limit():
    limiter = SlidingWindowLimiter(max_events=3, window_sec=60)
    allowed = [await limiter.check("user:u1") for _ in range(3)]
    assert all(allowed)
    assert not await limiter.check("user:u1")


@pytest.mark.asyncio
async def test_sliding_window_isolated_per_identity():
    limiter = SlidingWindowLimiter(max_events=1, window_sec=60)
    assert await limiter.check("user:a")
    assert await limiter.check("user:b")  # different identity → separate bucket
    assert not await limiter.check("user:a")


@pytest.mark.asyncio
async def test_ws_rate_gate():
    with patch("app.ratelimit._ws_limiter", SlidingWindowLimiter(2, 60)):
        assert await check_ws_rate("u1")
        assert await check_ws_rate("u1")
        assert not await check_ws_rate("u1")


def test_stt_transcribe_429_envelope(client):
    """Hitting /stt/transcribe past the limit returns 429 in the envelope."""
    from app import ratelimit

    with patch.object(ratelimit, "_stt_limiter", SlidingWindowLimiter(2, 60)):
        statuses = []
        for _ in range(3):
            resp = client.post(
                "/stt/transcribe",
                headers={"X-User-Id": "rl-user"},  # main's dev-mode identity (#21)
                files={"file": ("a.wav", b"RIFFfake-audio", "audio/wav")},
            )
            statuses.append(resp.status_code)
    assert statuses[2] == 429, f"third should be rate-limited: {statuses}"
    err = resp.json()["error"]
    assert set(err) >= {"code", "message", "request_id"}


def test_stt_rate_limit_zero_disables_gate(client):
    """RATE_LIMIT_STT_RPM=0 disables the gate entirely."""
    from app import ratelimit
    with patch.object(ratelimit, "RATE_LIMIT_STT_RPM", 0):
        statuses = []
        for _ in range(4):
            resp = client.post("/stt/transcribe",
                               headers={"X-User-Id": "rl-user"},
                               files={"file": ("a.wav", b"x", "audio/wav")})
            statuses.append(resp.status_code)
        assert all(s != 429 for s in statuses), statuses
