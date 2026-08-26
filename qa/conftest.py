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
        self._single = False
        self._limit = None
        self._range = None  # (start, end_inclusive) — postgrest .range() semantics
        self._order = None  # (col, desc)
        self._lt = {}       # col -> value (exclusive upper bound)
        self._op = None     # None | "delete"
        self._select = "*"

    def select(self, *_a, **_k):
        # count="exact" etc. swallowed here; execute() always reports .count
        self._select = _a[0] if _a else "*"
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def update(self, payload):
        self._payload = ("update", payload)
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def lt(self, col, val):
        self._lt[col] = val
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def single(self):
        self._single = True
        return self

    @staticmethod
    def _result(data):
        return type("R", (), {"data": data, "count": len(data) if isinstance(data, list) else None})()

    def _matching(self, rows):
        return [
            r for r in rows
            if all(r.get(c) == v for c, v in self._filters.items())
            and all(r.get(c) is not None and r.get(c) < v for c, v in self._lt.items())
        ]

    def execute(self):
        rows = self._store.setdefault(self._name, [])
        if self._op == "delete":
            matched = self._matching(rows)
            for r in matched:
                rows.remove(r)
            return self._result(matched)
        if isinstance(self._payload, tuple) and self._payload[0] == "update":
            _, fields = self._payload
            matched = self._matching(rows)
            for row in matched:
                row.update(fields)
            data = matched[0] if (self._single and len(matched) == 1) else matched
            return self._result(data)
        if self._payload is not None:  # insert
            row = dict(self._payload)
            if self._name in ("conversations", "campaigns", "contacts", "campaign_contacts"):
                import uuid as _uuid

                row["id"] = str(_uuid.uuid4())
            else:
                row.setdefault("id", f"{self._name}-row-{len(rows) + 1}")
            # Table DEFAULTs (migrations/0002 + 0007)
            if self._name == "campaign_contacts":
                row.setdefault("status", "queued")
                row.setdefault("attempts", 0)
                row.setdefault("outcome", None)
                # Postgrest returns every column; NULLs stay present as null keys
                row.setdefault("last_attempted_at", None)
                row.setdefault("outcome_notes", None)
            if self._name == "contacts":
                row.setdefault("dnd", False)
            if self._name == "campaigns":
                # migration 0007 column defaults
                row.setdefault("calling_hours", {"start": "09:00", "end": "18:00"})
                row.setdefault("timezone", "Asia/Kolkata")
                row.setdefault("retry_max_attempts", 3)
                row.setdefault("retry_after_min", 60)
            # Monotonic timestamps so keyset/cursor pagination is deterministic
            if self._name in ("campaigns", "contacts", "campaign_contacts"):
                self._store["_clock"] = self._store.get("_clock", 0) + 1
                ts = f"2026-08-26T00:00:{self._store['_clock']:05d}.000000+00:00"
                row.setdefault("created_at", ts)
                row.setdefault("updated_at", ts)
            rows.append(row)
            return self._result(row)
        data = self._matching(rows)
        if self._order is not None:
            col, desc = self._order
            data = sorted(
                [r for r in data if r.get(col) is not None],
                key=lambda r: r[col],
                reverse=desc,
            )
        if self._range is not None:
            start, end = self._range
            data = data[start : end + 1]
        elif self._limit is not None:
            data = data[: self._limit]
        # Postgrest embedded resource: select "…,contacts(id,phone,name,dnd)" on
        # campaign_contacts → nest the joined contact row under key "contacts".
        if self._name == "campaign_contacts" and "contacts(" in self._select:
            import re as _re

            m = _re.search(r"contacts\(([^)]*)\)", self._select)
            cols = [c.strip() for c in m.group(1).split(",")] if m else []
            contacts_by_id = {c["id"]: c for c in self._store.get("contacts", [])}
            embedded = []
            for r in data:
                contact = contacts_by_id.get(r.get("contact_id"), {})
                embedded.append({**r, "contacts": {c: contact.get(c) for c in cols}})
            data = embedded
        if getattr(self, "_single", False):
            # Faithful to supabase-py: .single() raises when 0 or >1 rows match
            if len(data) != 1:
                from postgrest.exceptions import APIError

                raise APIError(f"JSON object requested, multiple (or no) rows returned: {len(data)}")
            return self._result(data[0])
        return self._result(data)


class _FakeSupabase:
    """In-memory stand-in for supabase-py. Tracks call_end invocations for SEC-04."""

    def __init__(self):
        self.store = {
            "agents": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    # campaigns create requires the agent to belong to the caller
                    "user_id": "aaaaaaaa-0000-0000-0000-00000000000a",
                    "name": "QA Demo Agent",
                    "status": "published",  # validate_for_start requires published
                    "call_count": 0,
                    "language": "English",
                }
            ],
            "conversations": [],
            "campaigns": [],
            "contacts": [],
            "campaign_contacts": [],
        }
        self.rpc_calls = []

    def table(self, name):
        return _FakeTable(self.store, name)

    def rpc(self, fn, params=None):
        self.rpc_calls.append((fn, params))
        # SEC-04: emulate the real finalize_conversation Postgres function
        # (migrations/0002_atomic_call_count.sql): guarded transition + atomic
        # call_count increment, exactly-once per conversation.
        if fn == "finalize_conversation" and params:
            p = params
            if p.get("p_status") not in ("resolved", "escalated", "missed", "in_progress"):
                raise ValueError(f"invalid status: {p.get('p_status')}")
            convs = self.store["conversations"]
            for c in convs:
                if c["id"] == p.get("p_conversation_id") and c.get("status") == "in_progress":
                    c["status"] = p["p_status"]
                    c["duration_sec"] = p.get("p_duration_sec")
                    c["transcript"] = p.get("p_transcript")
                    if p.get("p_csat_score") is not None:
                        c["csat_score"] = p["p_csat_score"]
                    # Increment only on terminal statuses (SEC-04).
                    if p["p_status"] != "in_progress":
                        for a in self.store["agents"]:
                            if a["id"] == c.get("agent_id"):
                                a["call_count"] = a.get("call_count", 0) + 1
                    return type("R", (), {"execute": lambda self: type("D", (), {"data": {"updated": True}})()})()
            return type("R", (), {"execute": lambda self: type("D", (), {"data": {"updated": False}})()})()
        # ── Outreach queue primitives (migrations/0007_outreach_campaigns.sql) ──
        if fn == "enqueue_campaign" and params:
            cid = params.get("p_campaign_id")
            affected = 0
            for cc in self.store.get("campaign_contacts", []):
                if cc["campaign_id"] == cid and cc.get("status") in ("failed", "skipped"):
                    cc["status"] = "queued"
                    affected += 1
            return self._rpc_result(affected)
        if fn == "dequeue_campaign_contact" and params:
            cid = params.get("p_campaign_id")
            contacts_by_id = {c["id"]: c for c in self.store.get("contacts", [])}
            candidates = [
                cc
                for cc in self.store.get("campaign_contacts", [])
                if cc["campaign_id"] == cid
                and cc.get("status") == "queued"
                # hard DND guard at dequeue time (migration line 145)
                and contacts_by_id.get(cc.get("contact_id"), {}).get("dnd", False) is False
            ]
            if not candidates:
                return self._rpc_result(None)
            candidates.sort(key=lambda cc: (cc.get("attempts", 0), cc["id"]))
            picked = candidates[0]
            picked["status"] = "dialing"
            picked["attempts"] = picked.get("attempts", 0) + 1
            contact = contacts_by_id.get(picked["contact_id"], {})
            row = {
                "cc_id": picked["id"],
                "contact_id": picked["contact_id"],
                "phone": contact.get("phone"),
                "contact_name": contact.get("name"),
                "attempts": picked["attempts"],
            }
            return self._rpc_result([row])
        if fn == "complete_campaign_if_drained" and params:
            cid = params.get("p_campaign_id")
            remaining = sum(
                1
                for cc in self.store.get("campaign_contacts", [])
                if cc["campaign_id"] == cid and cc.get("status") in ("queued", "dialing")
            )
            if remaining == 0:
                camp = next((c for c in self.store.get("campaigns", []) if c["id"] == cid), None)
                if camp and camp.get("status") in ("running", "paused"):
                    camp["status"] = "completed"
                return self._rpc_result(True)
            return self._rpc_result(False)
        return type("R", (), {"execute": lambda self: None})()

    @staticmethod
    def _rpc_result(data):
        return type(
            "R", (), {"execute": lambda self: type("D", (), {"data": data})()}
        )()


@pytest.fixture()
def fake_supabase(monkeypatch):
    """Patch get_supabase() used across routers/services BEFORE app import."""
    fs = _FakeSupabase()
    import app.services.supabase_client as sbc
    import app.services.agent_service as ags
    import app.services.campaign_service as campaign_service_mod

    monkeypatch.setattr(sbc, "get_supabase", lambda: fs)
    # routers/calls.py imported get_supabase by name → patch there too
    import app.routers.calls as calls_mod

    monkeypatch.setattr(calls_mod, "get_supabase", lambda: fs)
    monkeypatch.setattr(ags, "get_supabase", lambda: fs)
    # campaigns: service layer binds get_supabase by name at import → patch it too
    monkeypatch.setattr(campaign_service_mod, "get_supabase", lambda: fs)
    return fs


# (campaigns auth helpers live near the bottom, after USER_A/USER_B are defined)


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

# Campaigns auth: X-User-Id dev fallback (AUTH_ENFORCED=false default).
# When the JWT flag flips, these headers must become Bearer tokens — see
# TestCampaignAuthFlag below and qa/security_tests SEC-01..03 gating.
AUTH_HEADERS: dict = {"X-User-Id": USER_A} if not JWT_ENFORCED else {}


def _bearer_headers(token_sub: str) -> dict:
    """Mint an HS256 token the way Supabase would, for AUTH_ENFORCED=1 runs."""
    import jwt as _jwt

    from app.config import SUPABASE_JWT_SECRET

    token = _jwt.encode({"sub": token_sub}, SUPABASE_JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def auth_headers(user_id: str = USER_A) -> dict:
    """Headers that authenticate as `user_id` under EITHER flag state."""
    if JWT_ENFORCED:
        return _bearer_headers(user_id)
    return {"X-User-Id": user_id}


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
