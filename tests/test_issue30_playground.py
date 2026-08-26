"""
tests/test_issue30_playground.py
────────────────────────────────
Issue #30: playground always-connects.

Covers:
- Provider selection (Sarvam/NIM primary when keys present, local fallbacks otherwise)
- Sarvam saaras:v3 transcription happy path + failure raising (no silent "")
- NIM chat-completions streaming parse + LLMProviderError
- /health per-dependency readiness shape
- WS error frames use the single taxonomy (stt_failed) instead of silent closes
- WS accepts connections without any local fallback running
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llm import LLMProviderError, llm_provider, stream_llm
from app.services.stt import stt_provider, transcribe


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 1. Provider selection ────────────────────────────────────────────────


def test_stt_provider_selection():
    from app.services import stt as stt_mod

    assert stt_provider() == "whisper"  # no key in test env
    with patch.object(stt_mod, "SARVAM_API_KEY", "test-key"):
        assert stt_provider() == "sarvam"


def test_llm_provider_selection():
    from app.services import llm as llm_mod

    assert llm_provider() == "llama"  # no key in test env
    with patch.object(llm_mod, "NVIDIA_API_KEY", "nvapi-test"):
        assert llm_provider() == "nim"


# ── 2. Sarvam STT ────────────────────────────────────────────────────────


def _fake_http_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


@pytest.mark.asyncio
async def test_transcribe_sarvam_success():
    from app.services import stt as stt_mod

    client = MagicMock()
    client.post = AsyncMock(return_value=_fake_http_response(200, {"transcript": "नमस्ते जी"}))

    with patch.object(stt_mod, "SARVAM_API_KEY", "k"):
        text = await stt_mod.transcribe_sarvam(b"audio", client=client)

    assert text == "नमस्ते जी"
    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"] == {"api-subscription-key": "k"}
    assert kwargs["data"]["model"] == "saaras:v3"


@pytest.mark.asyncio
async def test_transcribe_sarvam_failure_raises():
    from app.services import stt as stt_mod

    client = MagicMock()
    client.post = AsyncMock(return_value=_fake_http_response(403, {"error": "bad key"}))

    with patch.object(stt_mod, "SARVAM_API_KEY", "k"):
        with pytest.raises(RuntimeError) as excinfo:
            await stt_mod.transcribe_sarvam(b"audio", client=client)
    assert "403" in str(excinfo.value)


@pytest.mark.asyncio
async def test_transcribe_sarvam_primary_whisper_fallback():
    """Sarvam failure falls back to whisper.cpp when reachable."""
    from app.services import stt as stt_mod

    async def _post(url, **kwargs):
        if "sarvam.ai" in url:
            raise httpx.ConnectError("no net")
        return _fake_http_response(200, {"text": "hello there"})

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)

    with patch.object(stt_mod, "SARVAM_API_KEY", "k"):
        text = await stt_mod.transcribe(b"audio", client=client)
    assert text == "hello there"


@pytest.mark.asyncio
async def test_transcribe_both_fail_raises_not_silent():
    """When every STT path fails the caller must see an exception, never ''."""
    from app.services import stt as stt_mod

    async def _post(url, **kwargs):
        raise httpx.ConnectError("down")

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)

    with patch.object(stt_mod, "SARVAM_API_KEY", "k"):
        with pytest.raises(RuntimeError):
            await stt_mod.transcribe(b"audio", client=client)


# ── 3. NVIDIA NIM streaming ──────────────────────────────────────────────


class _FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"{}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_stream_nim_parses_openai_sse():
    from app.services import llm as llm_mod

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world."}}]}',
        "data: [DONE]",
    ]

    client = MagicMock()
    client.stream = MagicMock(
        return_value=_FakeStreamResponse(sse_lines)
    )

    fragments = []
    with patch.object(llm_mod, "NVIDIA_API_KEY", "nvapi-x"):
        async for frag in llm_mod.stream_nim({}, "hi", client=client):
            fragments.append(frag)
    assert fragments == ["Hello world."]


@pytest.mark.asyncio
async def test_stream_llm_falls_back_to_llamacpp_on_nim_error():
    """NIM failure falls back to llama.cpp; result is still streamed."""
    from app.services import llm as llm_mod

    class FailingStream:
        status_code = 500

        async def aread(self):
            return b"boom"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    nim_client = MagicMock()
    nim_client.stream = MagicMock(return_value=FailingStream())

    ok_lines = ['data: {"content":"local reply."}', "data: [DONE]"]
    llama_client = MagicMock()
    llama_client.stream = MagicMock(return_value=_FakeStreamResponse(ok_lines))

    with patch.object(llm_mod, "NVIDIA_API_KEY", "nvapi-x"), \
         patch.object(llm_mod, "stream_llamacpp") as mock_llama:
        async def _gen(*a, **kw):
            yield "local reply."
        mock_llama.side_effect = lambda *a, **kw: _gen()
        fragments = []
        async for frag in llm_mod.stream_llm("prompt", {}, client=nim_client, user_text="hi"):
            fragments.append(frag)
    assert fragments == ["local reply."]
    assert mock_llama.called


# ── 4. /health per-dependency readiness ──────────────────────────────────


def test_health_reports_per_dependency(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # New contract: dict per dependency with provider + ok
    assert body["stt"]["provider"] in {"sarvam", "whisper"}
    assert isinstance(body["stt"]["ok"], bool)
    assert body["llm"]["provider"] in {"nim", "llama"}
    assert isinstance(body["llm"]["ok"], bool)
    assert isinstance(body["tts"]["configured"], bool)
    assert isinstance(body["supabase"]["ok"], bool)
    assert body["status"] in {"ok", "degraded"}


def test_health_ok_when_primaries_configured(client):
    """With cloud primaries keyed in, local fallbacks being down never degrades."""
    from app.routers import health as health_mod

    with patch.object(health_mod, "SARVAM_API_KEY", "sk"), \
         patch.object(health_mod, "NVIDIA_API_KEY", "nvapi-x"):
        resp = client.get("/health")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["stt"] == {"provider": "sarvam", "ok": True}
    assert body["llm"] == {"provider": "nim", "ok": True}


# ── 5. WS error taxonomy ─────────────────────────────────────────────────


@pytest.fixture()
def ws_agent_mock():
    """Mock agent_service.get_agent to return a row without touching Supabase."""
    row = {
        "id": "a1", "name": "Test", "system_prompt": "sp",
        "first_message": "", "voice_name": None,
        "language": "English",
    }
    with patch("app.services.agent_service.get_agent", new=AsyncMock(return_value=row)), \
         patch("app.services.agent_service.increment_call_count", new=AsyncMock()):
        yield row


@pytest.mark.asyncio
async def test_ws_stt_failure_sends_taxonomy_frame(ws_agent_mock):
    """
    Browser mic flow: pcm16 chunks arrive, every STT provider is down ->
    client receives ONE actionable {type:'error', code:'stt_failed'} frame
    instead of silence.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.services.stt.SARVAM_API_KEY", "k"):
        with patch(
            "app.routers.audio_ws.transcribe",
            new=AsyncMock(side_effect=RuntimeError("Sarvam STT request failed: boom")),
        ):
            with TestClient(app) as tc:
                with tc.websocket_connect("/ws/audio?agent_id=a1&user_id=u1") as ws:
                    # Switch to pcm16 BEFORE audio so the tiny threshold applies.
                    ws.send_text(json.dumps({
                        "type": "audio_meta",
                        "format": "pcm16",
                        "sample_rate": 16000,
                        "channels": 1,
                    }))
                    # 12.8KB chunks with energy -> crosses threshold after 1-2.
                    for _ in range(3):
                        ws.send_bytes(b"\x10\x20" * 6400)
                    msg = ws.receive_json()
    assert msg["type"] == "error"
    assert msg["code"] == "stt_failed"
    assert "Sarvam" in msg["message"]


@pytest.mark.asyncio
async def test_ws_accepts_without_local_fallbacks(ws_agent_mock):
    """WS must connect even when no local llama.cpp / whisper.cpp is running."""
    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.routers.audio_ws.transcribe", new=AsyncMock(return_value="")):
        with TestClient(app) as tc:
            with tc.websocket_connect("/ws/audio?agent_id=a1&user_id=u1") as ws:
                ws.send_text(json.dumps({"type": "text_input", "message": "ping"}))
                first = ws.receive_json()
    # Either an echo transcript or an llm_failed error frame — but NOT a
    # connection refusal: the socket itself accepted and speaks the protocol.
    assert first["type"] in {"transcript", "error"}
