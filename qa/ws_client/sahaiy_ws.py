"""
qa/ws_client/sahaiy_ws.py
─────────────────────────
G4 primary-path Python client: drives WS /ws/audio `text_input` turns
(headless-deterministic per test-plan-demo-gate-v1.md G4).

Protocol frames implemented per api-contracts-and-outreach-boundary-v1.md §1.4:
  client → server: {"type": "text_input", "message": str}, audio_meta,
                   agent_meta, interrupt
  server → client: {"type": "transcript"|"fragment"|"interrupted"|"error"} JSON
                   + binary WAV frames

Usage:
    async with SahaiyWSClient(url, agent_id, user_id) as ws:
        await ws.send_text_turn("hello")
        reply = await ws.collect_reply(timeout=10)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

import websockets


def _resolve_connect():
    """Newer websockets (>=14) renamed the legacy client API; prefer the modern one."""
    try:
        from websockets.asyncio.client import connect as modern_connect

        return modern_connect
    except ImportError:
        return websockets.connect


@dataclass
class TurnResult:
    transcript: Optional[str] = None
    fragments: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    audio_bytes: int = 0
    first_fragment_latency_ms: Optional[float] = None


class SahaiyWSClient:
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        user_id: str,
        open_timeout: float = 10.0,
    ):
        self.url = f"{base_url.rstrip('/')}/ws/audio?agent_id={agent_id}&user_id={user_id}"
        self.open_timeout = open_timeout
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connect = _resolve_connect()

    async def __aenter__(self):
        self._ws = await asyncio_wait_for(self._connect(self.url), self.open_timeout)
        return self

    async def __aexit__(self, *exc):
        if self._ws is not None:
            await self._ws.close()
        return False

    async def send_json(self, payload: dict) -> None:
        await self._ws.send(json.dumps(payload))

    async def send_text_turn(self, message: str) -> None:
        """Send a text_input turn — G4's deterministic mic-less path."""
        await self.send_json({"type": "text_input", "message": message})

    async def recv_raw(self, timeout: float):
        return await asyncio_wait_for(self._ws.recv(), timeout)

    async def collect_reply(
        self,
        timeout: float = 15.0,
        idle_gap: float = 2.0,
        expect_audio: bool = True,
    ) -> TurnResult:
        """
        Read frames until the turn goes quiet for `idle_gap` seconds.
        Returns transcripts, LLM fragments, error messages, and audio byte count.
        """
        result = TurnResult()
        deadline = time.monotonic() + timeout
        last_frame = time.monotonic()

        while time.monotonic() < deadline:
            remaining_idle = idle_gap - (time.monotonic() - last_frame)
            if remaining_idle <= 0:
                break
            try:
                frame = await self.recv_raw(timeout=max(remaining_idle, 0.05))
            except (TimeoutError, Exception) as exc:
                name = type(exc).__name__
                if name in ("TimeoutError", "asyncio.TimeoutError", "TimeoutError"):
                    break
                raise
            last_frame = time.monotonic()

            if isinstance(frame, (bytes, bytearray)):
                result.audio_bytes += len(frame)
                continue

            msg = json.loads(frame)
            mtype = msg.get("type")
            if mtype == "transcript":
                result.transcript = msg.get("text")
            elif mtype == "fragment":
                if result.first_fragment_latency_ms is None:
                    result.first_fragment_latency_ms = (
                        time.monotonic() - _turn_start
                        if (_turn_start := getattr(self, "_turn_started_at", None))
                        else None
                    )
                result.fragments.append(msg.get("text", ""))
            elif mtype == "error":
                result.errors.append(msg.get("message", ""))

        result.fragments_joined = "".join(result.fragments)
        return result

    async def start_timer(self) -> None:
        """Mark the moment a turn is triggered (for ≤60s gate measurement)."""
        self._turn_started_at = time.monotonic()


def asyncio_wait_for(coro, timeout):
    return asyncio.wait_for(coro, timeout)
