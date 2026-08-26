"""
tests/test_outbound_calls.py
────────────────────────────
Outbound PSTN calling contract (issue #24):
  • unconfigured-mode 501-with-reason for phone endpoints (no LiveKit creds)
  • E.164 validation rejects garbage before any dialing
  • configured happy path: room + dial + agent_token via mocked telephony

Mirrors the qa/conftest.py style: real app in-process, upstreams stubbed.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import make_supabase, FakeResult


AGENT_ID = "11111111-1111-1111-1111-111111111111"
USER_A = "aaaaaaaa-0000-0000-0000-00000000000a"


def _patch_supabase():
    """Patch get_supabase everywhere calls.py imported/uses it."""
    sb = make_supabase({
        "agents": [{"id": AGENT_ID, "name": "QA Agent", "language": "English"}],
    })
    # make_supabase builds a FRESH builder per .table() call; wrap its
    # original side_effect function so conversations inserts read back
    # a concrete row (wrapping sb.table itself would recurse).
    _orig_effect = sb.table.side_effect

    def table(name):
        builder = _orig_effect(name)
        if name == "conversations":
            builder.execute.return_value = FakeResult({"id": "conv-123"})
        return builder

    sb.table.side_effect = table
    return patch("app.routers.calls.get_supabase", return_value=sb)


class TestOutboundDormantMode:
    """Without LIVEKIT_* env the endpoints answer 501-with-reason — never fake success."""

    def test_outbound_start_501_without_livekit_creds(self, client):
        r = client.post(f"/agents/{AGENT_ID}/call/outbound",
                        json={"user_id": USER_A, "to_number": "+919876543210"})
        assert r.status_code == 501, r.text
        assert "LIVEKIT" in r.json()["error"]["message"]

    def test_outbound_status_501_without_livekit_creds(self, client):
        r = client.get("/agents/outbound/some-conv-id/status")
        assert r.status_code == 501, r.text
        assert "LIVEKIT" in r.json()["error"]["message"]

    def test_outbound_end_501_without_livekit_creds(self, client):
        r = client.post("/agents/outbound/some-conv-id/end",
                        json={"user_id": USER_A, "conversation_id": "x"})
        assert r.status_code == 501, r.text
        assert "LIVEKIT" in r.json()["error"]["message"]

    def test_invalid_phone_number_rejected_by_validation(self, client):
        # Pydantic pattern guard fires BEFORE the dormancy gate — no dialing ever.
        r = client.post(f"/agents/{AGENT_ID}/call/outbound",
                        json={"user_id": USER_A, "to_number": "not-a-phone"})
        assert r.status_code == 422, r.text


class TestOutboundConfiguredPath:
    def test_outbound_start_dials_and_returns_token(self, client, monkeypatch):
        import app.services.telephony as tel
        import app.routers.calls as calls_mod

        # Simulate configured LiveKit
        monkeypatch.setattr(tel, "_LIVEKIT_AVAILABLE", True)
        monkeypatch.setattr(calls_mod.telephony, "livekit_ready", lambda: (True, ""))
        monkeypatch.setattr(tel, "livekit_ready", lambda: (True, ""))

        async def fake_create_room(room_name):
            fake_create_room.called_with = room_name
        fake_create_room.called_with = None

        async def fake_dial(room_name, to_number, participant_identity,
                            participant_name="Caller", trunk_id=None, ring_timeout_s=45):
            return {"participant_id": "PA-123", "sip_call_id": "SC-456"}

        monkeypatch.setattr(tel, "create_room", fake_create_room)
        monkeypatch.setattr(tel, "dial_sip_participant", fake_dial)
        monkeypatch.setattr(tel, "mint_agent_token",
                            lambda room, identity, ttl_minutes=60: "fake.jwt.token")

        with _patch_supabase() as patched_sb:
            r = client.post(f"/agents/{AGENT_ID}/call/outbound",
                            json={"user_id": USER_A, "to_number": "+919876543210"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["conversation_id"] == "conv-123"
        assert body["room_name"] == "outbound-conv-123"
        assert body["status"] == "ringing"
        assert body["participant_id"] == "PA-123"
        assert body["sip_call_id"] == "SC-456"
        assert body["agent_token"] == "fake.jwt.token"

    def test_outbound_status_reports_room_state(self, client, monkeypatch):
        import app.services.telephony as tel
        import app.routers.calls as calls_mod

        monkeypatch.setattr(tel, "_LIVEKIT_AVAILABLE", True)
        monkeypatch.setattr(calls_mod.telephony, "livekit_ready", lambda: (True, ""))

        async def fake_room_status(room_name):
            return {"exists": True, "num_participants": 2,
                    "identities": ["ai-agent-x", "callee-y"], "active": True}

        monkeypatch.setattr(tel, "room_status", fake_room_status)

        r = client.get("/agents/outbound/conv-abc/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["conversation_id"] == "conv-abc"
        assert body["exists"] is True and body["active"] is True

    def test_outbound_end_tears_down_room(self, client, monkeypatch):
        import app.services.telephony as tel
        import app.routers.calls as calls_mod

        monkeypatch.setattr(tel, "_LIVEKIT_AVAILABLE", True)
        monkeypatch.setattr(calls_mod.telephony, "livekit_ready", lambda: (True, ""))

        ended = {}

        async def fake_end_room(room_name):
            ended["room"] = room_name

        monkeypatch.setattr(tel, "end_room", fake_end_room)

        with _patch_supabase():
            r = client.post("/agents/outbound/conv-end/end",
                            json={"user_id": USER_A, "conversation_id": "conv-end",
                                  "duration_sec": 120, "status": "resolved"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert ended["room"] == "outbound-conv-end"


class TestTelephonyService:
    def test_livekit_ready_reasons_when_unconfigured(self):
        import app.services.telephony as tel
        ready, reason = tel.livekit_ready()
        # In CI the SDK may or may not be installed; either way it must be honest.
        if ready:
            assert reason == ""
        else:
            assert "LiveKit" in reason and reason

    def test_ensure_ready_or_501_raises_http_exception(self):
        from fastapi import HTTPException
        import app.services.telephony as tel
        ready, _ = tel.livekit_ready()
        if not ready:
            with pytest.raises(HTTPException) as exc_info:
                tel.ensure_ready_or_501()
            assert exc_info.value.status_code == 501
