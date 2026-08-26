"""
qa/ws_client/test_g4_text_input.py
──────────────────────────────────
G4 (TTV-04) primary path: simulated call starts ≤60s of trigger; live
transcript streams. Drives the REAL WS endpoint over a real socket with the
text_input control frame — headless-deterministic.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import AGENT_ID, USER_A

from sahaiy_ws import SahaiyWSClient  # noqa: E402  (path set above)

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


GATE_START_SECONDS = 60.0


class TestG4TextInputPath:
    async def test_connection_accepted(self, ws_server):
        async with SahaiyWSClient(ws_server, AGENT_ID, USER_A) as ws:
            assert ws._ws is not None

    async def test_transcript_streams_within_gate(self, ws_server):
        """Gate G4 part 1: transcript echo arrives after text_input trigger."""
        async with SahaiyWSClient(ws_server, AGENT_ID, USER_A) as ws:
            t0 = monotonic()
            await ws.start_timer()
            await ws.send_text_turn("What are your pricing plans?")
            result = await ws.collect_reply(timeout=GATE_START_SECONDS, idle_gap=1.5)
            elapsed = monotonic() - t0

        assert result.transcript == "What are your pricing plans?", (
            f"server must echo transcript frame for text_input; got {result.transcript!r}"
        )
        assert elapsed < GATE_START_SECONDS, f"turn took {elapsed:.1f}s (> {GATE_START_SECONDS}s gate)"

    async def test_llm_fragments_stream(self, ws_server):
        """Live reply fragments stream back (pipeline runs end-to-end)."""
        async with SahaiyWSClient(ws_server, AGENT_ID, USER_A) as ws:
            await ws.send_text_turn("hello")
            result = await ws.collect_reply(timeout=15, idle_gap=1.5)

        assert not result.errors, f"server errors: {result.errors}"
        joined = getattr(result, "fragments_joined", "") or "".join(result.fragments)
        assert len(joined) > 0, "expected ≥1 LLM fragment for a text turn"

    async def test_greeting_fragment_after_agent_meta(self, ws_server):
        """
        Contract §1.4: greeting sent automatically after first agent_meta frame
        when agent has first_message.
        """
        async with SahaiyWSClient(ws_server, "", "") as ws:
            await ws.send_json(
                {
                    "type": "agent_meta",
                    "agent": {
                        "name": "QA Agent",
                        "first_message": "Hello, I am the demo agent!",
                    },
                }
            )
            result = await ws.collect_reply(timeout=10, idle_gap=1.0)

        assert any("demo agent" in f for f in result.fragments), (
            f"expected greeting fragment, got {result.fragments}"
        )

    async def test_empty_text_input_ignored(self, ws_server):
        """Blank message must not crash the connection or emit transcript."""
        async with SahaiyWSClient(ws_server, AGENT_ID, USER_A) as ws:
            await ws.send_json({"type": "text_input", "message": "   "})
            result = await ws.collect_reply(timeout=5, idle_gap=0.8)
        assert result.transcript is None


def monotonic():
    import time

    return time.monotonic()
