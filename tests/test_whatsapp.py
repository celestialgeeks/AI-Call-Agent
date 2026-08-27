"""
tests/test_whatsapp.py
──────────────────────
WhatsApp Cloud API webhook contract (issue #24):
  • verify-token rejection (bad/missing token → 403; unconfigured → 501-with-reason)
  • inbound text → LLM mock path → reply sent via Graph API mock
  • inbound audio → STT(mocked transcribe) → reply
  • signature verification when WHATSAPP_APP_SECRET is set

Mirrors the qa/conftest.py style: real app in-process, upstreams stubbed.
"""
import hashlib
import hmac

import pytest

from tests.helpers import make_supabase


AGENT_ID = "11111111-1111-1111-1111-111111111111"
SENDER = "919999999999"


def _wa_message_payload(msg: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "ENTRY-ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "contacts": [{"profile": {"name": "QA"}, "wa_id": SENDER}],
                    "messages": [msg],
                },
            }],
        }],
    }


@pytest.fixture()
def wa_env(monkeypatch):
    """Configure the WhatsApp service for happy-path handler tests."""
    import app.services.whatsapp as wa
    monkeypatch.setattr(wa, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(wa, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")


@pytest.fixture()
def graph_spy(monkeypatch, wa_env):
    """Capture outgoing replies so tests assert on what would hit Meta."""
    calls = []

    async def fake_send_text(to, body, client=None):
        calls.append({"to": to, "body": body})
        return {"messages": [{"id": "wamid.fake"}]}

    async def fake_send_audio(to, media_url, client=None):
        calls.append({"to": to, "audio": media_url})
        return {"messages": [{"id": "wamid.fake-audio"}]}

    import app.services.whatsapp as wa
    import app.routers.whatsapp as router_mod
    monkeypatch.setattr(wa, "send_text", fake_send_text)
    monkeypatch.setattr(wa, "send_audio", fake_send_audio)
    return calls


@pytest.fixture()
def sb_agent(monkeypatch):
    """Supabase stub returning one QA agent for any lookup."""
    from unittest.mock import patch
    agent = {"id": AGENT_ID, "name": "QA Agent", "language": "English"}
    with patch("app.routers.whatsapp.get_supabase", return_value=make_supabase({
        "phone_numbers": [],
        "agents": [agent],
    })):
        yield


# ── GET /whatsapp/webhook — Meta subscription handshake ──────────────────

class TestWebhookVerifyHandshake:
    def test_verify_token_mismatch_rejected(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_VERIFY_TOKEN", "expected-token")
        r = client.get("/whatsapp/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG-token",
            "hub.challenge": "chall-123",
        })
        assert r.status_code == 403, r.text
        assert "Verification failed" in r.json()["error"]["message"]

    def test_verify_missing_token_rejected(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_VERIFY_TOKEN", "expected-token")
        r = client.get("/whatsapp/webhook", params={
            "hub.mode": "subscribe",
            "hub.challenge": "chall-123",
        })
        assert r.status_code == 403, r.text

    def test_verify_success_echoes_challenge(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_VERIFY_TOKEN", "expected-token")
        r = client.get("/whatsapp/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "expected-token",
            "hub.challenge": "chall-456",
        })
        assert r.status_code == 200
        assert r.text == "chall-456"

    def test_verify_unconfigured_returns_501_with_reason(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_VERIFY_TOKEN", "")
        r = client.get("/whatsapp/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "c",
        })
        assert r.status_code == 501
        assert "WHATSAPP_VERIFY_TOKEN" in r.json()["error"]["message"]

    def test_verify_wrong_mode_rejected(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_VERIFY_TOKEN", "expected-token")
        r = client.get("/whatsapp/webhook", params={
            "hub.mode": "denied",
            "hub.verify_token": "expected-token",
            "hub.challenge": "c",
        })
        assert r.status_code == 403


# ── POST /whatsapp/webhook — signature + config gating ───────────────────

def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestWebhookSignatureAndConfig:
    def test_invalid_signature_rejected_when_secret_set(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_APP_SECRET", "app-secret")
        body = b'{"entry": []}'
        r = client.post(
            "/whatsapp/webhook",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=" + "0" * 64,
                     "Content-Type": "application/json"},
        )
        assert r.status_code == 403, r.text

    def test_valid_signature_passes_gate_then_501_unconfigured(self, client, monkeypatch):
        import app.services.whatsapp as wa
        import app.routers.whatsapp as mod
        secret = "app-secret"
        monkeypatch.setattr(mod, "WHATSAPP_APP_SECRET", secret)
        monkeypatch.setattr(wa, "WHATSAPP_ACCESS_TOKEN", "")
        body = b'{"entry": []}'
        r = client.post(
            "/whatsapp/webhook",
            content=body,
            headers={"X-Hub-Signature-256": _sign(secret, body),
                     "Content-Type": "application/json"},
        )
        assert r.status_code == 501  # honest dormant answer
        assert "WHATSAPP_ACCESS_TOKEN" in r.json()["error"]["message"]

    def test_unconfigured_no_secret_still_501_with_reason(self, client, monkeypatch):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_APP_SECRET", "")  # sig check disabled
        r = client.post("/whatsapp/webhook", json={"entry": []})
        assert r.status_code == 501
        detail = r.json()["error"]["message"]
        assert "WHATSAPP_ACCESS_TOKEN" in detail and "WHATSAPP_PHONE_NUMBER_ID" in detail

    def test_invalid_json_400_when_configured(self, client, monkeypatch, wa_env):
        import app.routers.whatsapp as mod
        monkeypatch.setattr(mod, "WHATSAPP_APP_SECRET", "")
        r = client.post("/whatsapp/webhook", content=b"not-json{",
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400


# ── Inbound text → LLM reply (Graph API mocked) ──────────────────────────

class TestInboundTextReplyPath:
    def test_text_message_gets_llm_reply(self, client, monkeypatch, graph_spy, sb_agent):
        import app.routers.whatsapp as mod

        async def fake_stream_llm(prompt, agent, client=None, **kw):
            yield "Hello from "
            yield "the agent."

        monkeypatch.setattr(mod, "stream_llm", fake_stream_llm)

        payload = _wa_message_payload({"from": SENDER, "type": "text", "text": {"body": "Hi"}})
        r = client.post("/whatsapp/webhook", json=payload)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        assert len(graph_spy) == 1
        assert graph_spy[0]["to"] == SENDER
        assert "Hello from the agent." in graph_spy[0]["body"]

    def test_unsupported_type_gets_polite_fallback(self, client, monkeypatch, graph_spy, sb_agent):
        payload = _wa_message_payload({"from": SENDER, "type": "sticker"})
        r = client.post("/whatsapp/webhook", json=payload)
        assert r.status_code == 200
        assert len(graph_spy) == 1
        assert "voice messages only" in graph_spy[0]["body"] or "understand" in graph_spy[0]["body"]

    def test_empty_text_gets_repeat_request(self, client, monkeypatch, graph_spy, sb_agent):
        payload = _wa_message_payload({"from": SENDER, "type": "text", "text": {"body": "   "}})
        r = client.post("/whatsapp/webhook", json=payload)
        assert r.status_code == 200
        assert len(graph_spy) == 1
        assert "repeat" in graph_spy[0]["body"].lower()

    def test_handler_error_never_fails_webhook(self, client, monkeypatch, wa_env, sb_agent):
        import app.routers.whatsapp as mod

        async def exploding_stream(*a, **kw):
            raise RuntimeError("LLM down")
            yield  # pragma: no cover

        monkeypatch.setattr(mod, "stream_llm", exploding_stream)
        payload = _wa_message_payload({"from": SENDER, "type": "text", "text": {"body": "Hi"}})
        r = client.post("/whatsapp/webhook", json=payload)
        assert r.status_code == 200  # Meta contract: always ACK
        assert r.json() == {"ok": True}


# ── Inbound audio → media download → STT → LLM → reply ───────────────────

class TestInboundAudioPath:
    def test_audio_transcribed_then_replied(self, client, monkeypatch, graph_spy, sb_agent):
        import app.routers.whatsapp as mod
        import app.services.whatsapp as wa

        stt_calls = []

        async def fake_download(media_id, client=None):
            return b"FAKE-OGBYTES"

        async def fake_transcribe(audio_bytes, client=None, file_name="chunk.wav",
                                  content_type="audio/wav", language_hint=None):
            stt_calls.append({"bytes_len": len(audio_bytes), "file_name": file_name})
            return "What are your prices?"

        async def fake_stream_llm(prompt, agent, client=None, **kw):
            yield "Our plans start at Rs 999."

        monkeypatch.setattr(wa, "download_media", fake_download)
        monkeypatch.setattr(mod, "_transcribe_stt", fake_transcribe)
        monkeypatch.setattr(mod, "stream_llm", fake_stream_llm)

        payload = _wa_message_payload({"from": SENDER, "type": "audio", "audio": {"id": "MEDIA123"}})
        r = client.post("/whatsapp/webhook", json=payload)
        assert r.status_code == 200, r.text
        assert stt_calls and stt_calls[0]["file_name"].startswith("MEDIA123")
        assert stt_calls[0]["bytes_len"] == len(b"FAKE-OGBYTES")
        assert len(graph_spy) >= 1
        assert "Rs 999" in graph_spy[0]["body"]

    def test_voice_message_without_media_id_skips_reply(self, client, monkeypatch, graph_spy, sb_agent):
        payload = _wa_message_payload({"from": SENDER, "type": "voice"})
        r = client.post("/whatsapp/webhook", json=payload)
        assert r.status_code == 200
        assert graph_spy == []
