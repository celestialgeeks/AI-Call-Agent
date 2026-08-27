"""
app/routers/whatsapp.py
───────────────────────
WhatsApp Cloud API webhook (Meta official).

GET  /whatsapp/webhook  → hub.mode/hub.verify_token/hub.challenge handshake
POST /whatsapp/webhook  → inbound messages:
    • text  → LLM (build_prompt + stream_llm) → text reply
    • audio → download media → STT → LLM → text reply
              (+ TTS voice-note attachment when PUBLIC_BASE_URL is set)

Configuration (sahaiy-backend/.env):
  WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID — sending
  WHATSAPP_VERIFY_TOKEN   — GET handshake token (also entered in Meta console)
  WHATSAPP_APP_SECRET     — enables X-Hub-Signature-256 verification when set

Dormant-by-design: without credentials the router answers 501 with the exact
missing-variable reason instead of faking success.
"""

import hashlib
import hmac
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import PUBLIC_BASE_URL, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN
from app.services.llm import build_prompt, stream_llm
from app.services.supabase_client import get_supabase
from app.services import whatsapp as wa_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


# ── Signature verification ───────────────────────────────────────────────

def _signature_valid(request: Request, raw_body: bytes) -> bool:
    """Validate X-Hub-Signature-256 when WHATSAPP_APP_SECRET is configured."""
    if not WHATSAPP_APP_SECRET:
        return True  # verification disabled until the secret is provided
    header = request.headers.get("x-hub-signature-256", "")
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


# ── Webhook handshake ─────────────────────────────────────────────────────

@router.get("/webhook")
async def verify_webhook(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    """Meta's subscription handshake. Echoes hub.challenge only on token match."""
    if not WHATSAPP_VERIFY_TOKEN:
        raise HTTPException(
            status_code=501,
            detail="WHATSAPP_VERIFY_TOKEN not configured in sahaiy-backend/.env",
        )
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("[WhatsApp] webhook verified")
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Inbound messages ──────────────────────────────────────────────────────

@router.post("/webhook")
async def receive_webhook(request: Request):
    """
    Inbound WhatsApp events. Answers 200 quickly (Meta retries on error);
    per-message handler failures are logged, never surfaced as webhook errors.
    """
    raw_body = await request.body()

    if not _signature_valid(request, raw_body):
        raise HTTPException(status_code=403, detail="Invalid signature")

    if not wa_service.is_configured():
        raise HTTPException(
            status_code=501,
            detail=(
                "WhatsApp Cloud API not configured — set WHATSAPP_ACCESS_TOKEN and "
                "WHATSAPP_PHONE_NUMBER_ID in sahaiy-backend/.env"
            ),
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []) or []:
                # Process inline; Meta tolerates brief latency before ACK.
                try:
                    await _handle_message(message)
                except Exception as exc:
                    # Never fail the webhook — log and continue with other messages.
                    logger.error("[WhatsApp] handler error: %s", exc)

    return {"ok": True}


async def _handle_message(message: dict[str, Any]) -> None:
    """Route one inbound message through (STT) → LLM → reply."""
    msg_type = message.get("type")
    sender = message.get("from")  # E.164 digits without '+'
    if not sender or msg_type == "system":
        return

    user_text = ""
    if msg_type == "text":
        user_text = (message.get("text") or {}).get("body", "").strip()
    elif msg_type in ("audio", "voice"):
        media_id = (message.get(msg_type) or {}).get("id")
        if not media_id:
            logger.warning("[WhatsApp] %s message without media id — ignoring", msg_type)
            return
        audio_bytes = await wa_service.download_media(media_id)
        user_text = await _transcribe_stt(
            audio_bytes, file_name=f"{media_id}.ogg", content_type="audio/ogg"
        )
    else:
        logger.info("[WhatsApp] unsupported message type=%s — replying with fallback", msg_type)
        await wa_service.send_text(
            sender,
            "Sorry, I can currently understand text and voice messages only.",
        )
        return

    if not user_text:
        await wa_service.send_text(
            sender, "Sorry, I couldn't hear that clearly. Could you repeat?"
        )
        return

    agent = _agent_for_sender(sender)
    client = httpx.AsyncClient(timeout=30.0)
    try:
        prompt = build_prompt(agent, user_text, context="")
        reply_parts: list[str] = []
        async for fragment in stream_llm(prompt, agent, client=client):
            reply_parts.append(fragment)
        reply = "".join(reply_parts).strip() or "I'm sorry, could you say that again?"
    finally:
        await client.aclose()

    await wa_service.send_text(sender, reply[:4000])

    # Optional voice-note echo of the reply — needs a public HTTPS host for Meta
    # to fetch from; silently skipped when PUBLIC_BASE_URL is unset.
    if PUBLIC_BASE_URL and msg_type in ("audio", "voice"):
        try:
            from app.services.tts import speak_to_bytes

            wav = await speak_to_bytes(reply[:500], voice_name=agent.get("voice_name"))
            media_url = await _host_audio(wav, sender)
            if media_url:
                await wa_service.send_audio(sender, media_url)
        except Exception as exc:
            logger.warning("[WhatsApp] optional audio reply skipped: %s", exc)


def _agent_for_sender(sender: str) -> dict:
    """
    Resolve which agent should answer this sender.
    Looks up a linked phone_numbers row by number; falls back to the first
    published agent of any user (single-workspace default).
    """
    try:
        supabase = get_supabase()
        normalized = f"+{sender}" if not sender.startswith("+") else sender

        link_res = (
            supabase.table("phone_numbers")
            .select("agent_id")
            .eq("number", normalized)
            .limit(1)
            .execute()
        )
        agent_id = (link_res.data or [{}])[0].get("agent_id")

        query = supabase.table("agents").select("*").limit(1)
        if agent_id:
            query = supabase.table("agents").select("*").eq("id", agent_id).limit(1)
        agents = query.execute().data or []
        if not agents:
            return {"name": "Sahaiy Assistant", "language": "English"}
        return agents[0]
    except Exception as exc:
        logger.warning("[WhatsApp] agent lookup failed (%s) — using default persona", exc)
        return {"name": "Sahaiy Assistant", "language": "English"}


async def _host_audio(wav_bytes: bytes, sender: str) -> Optional[str]:
    """
    Host WAV bytes so Meta can fetch them. Requires a writable public bucket;
    returns None (skip audio reply) when no storage is available.
    """
    try:
        supabase = get_supabase()
        path = f"wa-replies/{sender}/{__import__('uuid').uuid4()}.wav"
        supabase.storage.from_("media").upload(path, wav_bytes, {"content-type": "audio/wav"})
        base = PUBLIC_BASE_URL.rstrip("/")
        return f"{base}/storage/v1/object/public/media/{path}"
    except Exception as exc:
        logger.warning("[WhatsApp] audio hosting unavailable: %s", exc)
        return None


# Imported late so tests can monkeypatch either module attribute cleanly.
from app.services.stt import transcribe as _transcribe_stt  # noqa: E402
