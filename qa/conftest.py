"""
qa/conftest.py
──────────────
Shared pytest fixtures for the demo-gate suite.

Strategy (per test-plan-demo-gate-v1.md risk #2 — "cases vs current shape now"):
  * The REAL FastAPI app from sahaiy-backend is started in-process via ASGITransport
    (httpx) and WSTransport (websockets via uvicorn on a random port) so contract
    conformance runs against actual router code, not mocks of the client.
  * External dependencies (Supabase, whisper.cpp, LLM server, Sarvam TTS) are
    stubbed at the service boundary. The routers/handlers under test are real.
  * SEC-01/02/03 are gated behind the SAHAIY_JWT_ENFORCED env flag: they activate
    the moment @backend-eng's JWT verification dependency lands (see
    api-contracts-and-outreach-boundary-v1.md "Auth model").

Run:
    .venv/bin/python -m pytest qa/contract_tests qa/security_tests -v
"""

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "sahaiy-backend"

# Import path for the backend app package
import sys

sys.path.insert(0, str(BACKEND_DIR))

JWT_ENFORCED = os.getenv("SAHAIY_JWT_ENFORCED", "").strip().lower() in {"1", "true", "yes", "on"}


# ────────────────────────────────────────────────────────────────────────────
# Stub layer: swap Supabase / LLM / TTS / STT before app import
# ────────────────────────────────────────────────────────────────────────────

class _FakeTable:
    """Minimal supabase-py table shim recording writes; serves canned agent rows."""

    def __init__(self, store, name):
        self._store = store
        self._name = name
        self._filters = {}
        self._payload = None

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def update(self, payload):
        self._payload = ("update", payload)
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        rows = self._store.setdefault(self._name, [])
        if isinstance(self._payload, tuple) and self._payload[0] == "update":
            _, fields = self._payload
            for row in rows:
                if all(row.get(c) == v for c, v in self._filters.items()):
                    row.update(fields)
            return type("R", (), {"data": [r for r in rows if all(r.get(c) == v for c, v in self._filters.items())]})()
        if self._payload is not None:  # insert
            row = dict(self._payload)
            row.setdefault("id", f"{self._name}-row-{len(rows) + 1}")
            if self._name == "conversations":
                import uuid as _uuid

                row["id"] = str(_uuid.uuid4())
            rows.append(row)
            return type("R", (), {"data": row})()
        data = [r for r in rows if all(r.get(c) == v for c, v in self._filters.items())]
        if getattr(self, "_single", False):
            # Faithful to supabase-py: .single() raises when 0 or >1 rows match
            if len(data) != 1:
                from postgrest.exceptions import APIError

                raise APIError(f"JSON object requested, multiple (or no) rows returned: {len(data)}")
            return type("R", (), {"data": data[0]})()
        return type("R", (), {"data": data})()


class _FakeSupabase:
    """In-memory stand-in for supabase-py. Tracks call_end invocations for SEC-04."""

    def __init__(self):
        self.store = {
            "agents": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "name": "QA Demo Agent",
                    "call_count": 0,
                    "language": "English",
                }
            ],
            "conversations": [],
        }
        self.rpc_calls = []

    def table(self, name):
        return _FakeTable(self.store, name)

    def rpc(self, fn, params=None):
        self.rpc_calls.append((fn, params))
        if fn == "finalize_conversation":
            # Emulate migrations/0002_atomic_call_count.sql semantics:
            # guarded UPDATE of the in_progress conversation + atomic agent
            # call_count increment on terminal transitions only. Returns
            # {"updated": bool} like the real SECURITY DEFINER function.
            p = params or {}
            updated = False
            for row in self.store["conversations"]:
                if (row.get("id") == p.get("p_conversation_id")
                        and row.get("status") == "in_progress"):
                    row["status"] = p.get("p_status", row["status"])
                    if "p_duration_sec" in p:
                        row["duration_sec"] = p["p_duration_sec"]
                    if "p_transcript" in p:
                        row["transcript"] = p["p_transcript"]
                    if p.get("p_csat_score") is not None:
                        row["csat_score"] = p["p_csat_score"]
                    updated = True
                    if p.get("p_status") != "in_progress":
                        for agent in self.store["agents"]:
                            if agent.get("id") == row.get("agent_id"):
                                agent["call_count"] = agent.get("call_count", 0) + 1
            result = {"updated": updated}
            return type("R", (), {"execute": lambda self: type("R2", (), {"data": result})()})()
        return type("R", (), {"execute": lambda self: None})()


@pytest.fixture()
def fake_supabase(monkeypatch):
    """Patch get_supabase() used across routers/services BEFORE app import."""
    fs = _FakeSupabase()
    import app.services.supabase_client as sbc
    import app.services.agent_service as ags

    monkeypatch.setattr(sbc, "get_supabase", lambda: fs)
    # routers/calls.py imported get_supabase by name → patch there too
    import app.routers.calls as calls_mod

    monkeypatch.setattr(calls_mod, "get_supabase", lambda: fs)
    monkeypatch.setattr(ags, "get_supabase", lambda: fs)
    return fs


@pytest.fixture(scope="session")
def llm_tts_stt_stubs(monkeypatch_session):
    """Stub LLM streaming + TTS + STT upstreams so the pipeline is deterministic."""
    import app.services.llm as llm
    import app.services.tts as tts

    async def fake_stream_llm(prompt, agent, client=None, **kw):
        for frag in ["QA-REPLY:", " pipeline ", "ok."]:
            yield frag

    async def fake_speak_to_bytes(*a, **kw):
        # Minimal valid WAV (44-byte header + 16 zero samples, 8 kHz mono 16-bit)
        import io
        import struct
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 16)
        return buf.getvalue()

    monkeypatch_session.setattr(llm, "stream_llm", fake_stream_llm)
    monkeypatch_session.setattr(tts, "speak_to_bytes", fake_speak_to_bytes)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "jwt_required: activates when SAHAIY_JWT_ENFORCED=1 (@backend-eng JWT fix)"
    )


@pytest.fixture(scope="session")
def monkeypatch_session():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


# ────────────────────────────────────────────────────────────────────────────
# HTTP client against the in-process real app
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api(fake_supabase, llm_tts_stt_stubs):
    """httpx.AsyncClient bound to the REAL FastAPI app (ASGI transport)."""
    from app.main import app as fastapi_app

    transport = httpx.ASGITransport(app=fastapi_app)

    class _Client(httpx.AsyncClient):
        async def aclose(self):  # ASGITransport needs no close
            pass

    return _Client(transport=transport, base_url="http://testserver")


AGENT_ID = "11111111-1111-1111-1111-111111111111"
USER_A = "aaaaaaaa-0000-0000-0000-00000000000a"
USER_B = "bbbbbbbb-0000-0000-0000-00000000000b"


# ────────────────────────────────────────────────────────────────────────────
# Live WS server (real protocol over a socket, deterministic stubbed pipeline)
# ────────────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def ws_server(llm_tts_stt_stubs, monkeypatch_session):
    """
    Boots the real FastAPI app under uvicorn on 127.0.0.1:<random> so the G4
    Python WS client exercises true WebSocket framing end-to-end.
    Supabase is stubbed session-wide here (WS tests don't need DB realism).
    """
    fs = _FakeSupabase()
    import app.services.supabase_client as sbc
    import app.services.agent_service as ags

    monkeypatch_session.setattr(sbc, "get_supabase", lambda: fs)
    monkeypatch_session.setattr(ags, "get_supabase", lambda: fs)

    import uvicorn
    from app.main import app as fastapi_app

    port = _free_port()
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not getattr(server, "started", False) and time.time() < deadline:
        time.sleep(0.05)
    if not getattr(server, "started", False):
        pytest.fail("uvicorn test server failed to start")

    yield f"ws://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


# Re-exported so tests can share constants
pytest.AGENT_ID = AGENT_ID
