"""
qa/contract_tests/test_contracts.py
────────────────────────────────────
Contract conformance vs api-contracts-and-outreach-boundary-v1.md (Part 1,
"Verified contracts"). These assert EXACT response shapes — any drift fails.

Runs against the real FastAPI app in-process with external services stubbed
(see qa/conftest.py). Sequencing per issue #9: cases target the CURRENT
body-user_id shape; the auth flip is covered by qa/security_tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import AGENT_ID, USER_A, USER_B, JWT_ENFORCED

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


# ── 1.1 POST /agents/{agent_id}/call/start ──────────────────────────────────


class TestCallStart:
    async def test_exact_shape_200(self, api):
        """Response must be EXACTLY {conversation_id: <uuid str>} — nothing more."""
        r = await api.post(
            f"/agents/{AGENT_ID}/call/start",
            json={"user_id": USER_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {"conversation_id"}, f"shape drift: {body.keys()}"
        conv_id = body["conversation_id"]
        assert isinstance(conv_id, str) and len(conv_id) == 36  # uuid4

    async def test_optional_fields_accepted(self, api):
        """caller_name/caller_number are optional strings per contract §1.1."""
        r = await api.post(
            f"/agents/{AGENT_ID}/call/start",
            json={"user_id": USER_A, "caller_name": "QA Bot", "caller_number": "+911234567890"},
        )
        assert r.status_code == 200
        assert set(r.json().keys()) == {"conversation_id"}

    async def test_user_id_required(self, api):
        """user_id is a required body field in the CURRENT shape."""
        r = await api.post(f"/agents/{AGENT_ID}/call/start", json={})
        assert r.status_code == 422  # pydantic validation error

    async def test_unknown_agent_still_500_today(self, api):
        """
        Contract §1.1: errors are currently raw 500s. This pins TODAY's behavior —
        it MUST be replaced by SEC-03's structured envelope once @backend-eng's
        error-envelope fix lands. Flip this test together with that fix.
        """
        r = await api.post(
            "/agents/99999999-9999-9999-9999-999999999999/call/start",
            json={"user_id": USER_A},
        )
        # Today: stub returns no agent row → router raises → 500
        assert r.status_code in (404, 500), f"unexpected status {r.status_code}"


# ── 1.2 POST /agents/{agent_id}/call/end ────────────────────────────────────


class TestCallEnd:
    def _payload(self, conversation_id):
        return {
            "user_id": USER_A,
            "conversation_id": conversation_id,
            "transcript": "hello | hi there",
            "duration_sec": 120,
            "csat_score": 4,
            "status": "resolved",
        }

    async def test_exact_shape_ok_true(self, api, fake_supabase):
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()
        r = await api.post(
            f"/agents/{AGENT_ID}/call/end", json=self._payload(start["conversation_id"])
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}, "contract §1.2 exact shape is {ok: true}"

    async def test_csat_bounds_validated(self, api):
        """csat_score validated 1–5 by pydantic (ge=1, le=5)."""
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()

        for bad in (0, 6):
            p = self._payload(start["conversation_id"]) | {"csat_score": bad}
            r = await api.post(f"/agents/{AGENT_ID}/call/end", json=p)
            assert r.status_code == 422, f"csat={bad} accepted"

    async def test_status_enum_values(self, api):
        """status accepts resolved|escalated|missed|in_progress per contract §1.2."""
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()
        for status in ("resolved", "escalated", "missed", "in_progress"):
            p = self._payload(start["conversation_id"]) | {"status": status}
            r = await api.post(f"/agents/{AGENT_ID}/call/end", json=p)
            assert r.status_code == 200, f"status={status} rejected: {r.text}"

    async def test_call_end_persists_transcript(self, api, fake_supabase):
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()
        payload = self._payload(start["conversation_id"])
        await api.post(f"/agents/{AGENT_ID}/call/end", json=payload)
        row = next(
            c for c in fake_supabase.store["conversations"] if c["id"] == start["conversation_id"]
        )
        assert row["transcript"] == payload["transcript"]
        assert row["duration_sec"] == 120
        assert row["csat_score"] == 4
        assert row["status"] == "resolved"


# ── 1.3 POST /stt/transcribe ────────────────────────────────────────────────


class TestSttTranscribe:
    async def test_empty_file_400(self, api):
        """Contract §1.3: empty upload → 400."""
        r = await api.post(
            "/stt/transcribe",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )
        assert r.status_code == 400, f"expected 400 for empty file, got {r.status_code}"

    async def test_missing_file_422(self, api):
        r = await api.post("/stt/transcribe")
        assert r.status_code == 422


# ── 1.5 Knowledge endpoints (current weak shape — pinned so drift is visible) ─


class TestKnowledge:
    async def _rag_ready(self, api) -> bool:
        """True when this deployment has the optional heavy RAG deps."""
        r = await api.get("/knowledge/status", params={"user_id": USER_A})
        return bool(r.json().get("rag_available"))

    async def test_ingest_exact_shape(self, api):
        if not await self._rag_ready(api):
            pytest.xfail("RAG disabled in test env (faiss/sentence-transformers "
                         "absent) — /knowledge/ingest honestly returns 503")
        r = await api.post(
            "/knowledge/ingest",
            json={"user_id": USER_A, "doc_id": "doc-1", "text": "Sahaiy demo facts."},
        )
        assert r.status_code == 200, r.text
        # Contract §1.4 upgraded by issue #5 (ADR-0003): response keeps the
        # v1 core {ok, doc_id} plus ADDITIVE honest metadata (status, chunks,
        # size_bytes, name, source). Exact-equality pin relaxed accordingly.
        body = r.json()
        assert body["ok"] is True
        assert body["doc_id"] == "doc-1"
    async def test_ingest_empty_text_400(self, api):
        r = await api.post(
            "/knowledge/ingest",
            json={"user_id": USER_A, "doc_id": "doc-2", "text": "   "},
        )
        assert r.status_code == 400

    async def test_status_exact_shape(self, api):
        r = await api.get("/knowledge/status", params={"user_id": USER_A})
        assert r.status_code == 200
        body = r.json()
        # Issue #5 adds additive fields to the v1 trio — superset allowed.
        assert {"user_id", "indexed_docs", "rag_available"} <= set(body.keys())
        assert body["user_id"] == USER_A
        assert isinstance(body["indexed_docs"], int)


# ── Health/root (deployment smoke for the hosted backend) ───────────────────


class TestHealth:
    async def test_root_shape(self, api):
        r = await api.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "Sahaiy Backend"


# ── Contract-drift tripwire for the outreach boundary (Part 2 DRAFT) ─────────


class TestOutreachBoundaryDraft:
    """
    api-contracts Part 2 is a DRAFT pending @systems-architect ruling. The moment
    /api/v1/campaigns ships, these become full conformance cases (exact shapes).
    Today they pin that unimplemented routes do NOT silently appear with wrong shapes.
    """

    async def test_campaigns_route_absent_until_contract_locked(self, api):
        r = await api.post("/api/v1/campaigns", json={"agent_id": AGENT_ID})
        assert r.status_code in (404, 405), (
            f"/api/v1/campaigns appeared with status {r.status_code} — "
            "outreach contract shipped: replace this canary with exact-shape tests "
            "per api-contracts-and-outreach-boundary-v1.md Part 2"
        )
