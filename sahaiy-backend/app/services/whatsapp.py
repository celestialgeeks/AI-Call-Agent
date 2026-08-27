"""
app/services/whatsapp.py
────────────────────────
WhatsApp Cloud API (Meta official) sender + inbound media helpers.

All HTTP goes through graph.facebook.com/v21.0. Configuration lives in env:
  WHATSAPP_ACCESS_TOKEN     — permanent System-User token from Meta App Dashboard
  WHATSAPP_PHONE_NUMBER_ID  — WhatsApp Business phone number ID

Dormant-by-design: when credentials are absent every send helper raises
WhatsAppNotConfiguredError so routers can answer 501-with-reason instead of
faking success.
"""

import logging
from typing import Optional

import httpx

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_PHONE_NUMBER_ID,
)

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com"


class WhatsAppNotConfiguredError(RuntimeError):
    """Raised when WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID are missing."""


def _base_url() -> str:
    return f"{GRAPH_BASE}/{WHATSAPP_API_VERSION}"


def is_configured() -> bool:
    return bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID)


def _require_config() -> None:
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        raise WhatsAppNotConfiguredError(
            "WhatsApp Cloud API not configured — set WHATSAPP_ACCESS_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID in sahaiy-backend/.env"
        )


async def send_text(
    to: str,
    body: str,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """
    Send a plain text message via /{phone_number_id}/messages.
    Returns the Graph API JSON response ({messaging_product, contacts, messages}).
    """
    return await _send_message(
        {"type": "text", "text": {"body": body[:4096]}}, to, client
    )


async def send_audio(
    to: str,
    media_url: str,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """
    Send a voice-note style audio message. Meta fetches media_url itself,
    so callers must host the audio at a publicly reachable HTTPS URL
    (PUBLIC_BASE_URL must be set — otherwise this raises NotConfigured).
    """
    if not media_url.startswith("https://"):
        raise WhatsAppNotConfiguredError(
            "Audio replies need PUBLIC_BASE_URL set to a public HTTPS URL in "
            "sahaiy-backend/.env — Meta fetches the file from it directly."
        )
    return await _send_message(
        {"type": "audio", "audio": {"link": media_url}}, to, client
    )


async def download_media(
    media_id: str,
    client: Optional[httpx.AsyncClient] = None,
) -> bytes:
    """
    Download inbound media (e.g. a voice note) by its Media ID.
    Two-step per Meta docs: resolve the temporary download URL, then fetch bytes.
    """
    _require_config()
    _client = client or httpx.AsyncClient(timeout=30.0)
    own = client is None
    try:
        meta_res = await _client.get(
            f"{_base_url()}/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"},
        )
        meta_res.raise_for_status()
        url = meta_res.json().get("url")
        if not url:
            raise RuntimeError(f"Graph API returned no URL for media {media_id}")

        media_res = await _client.get(
            url, headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        )
        media_res.raise_for_status()
        return media_res.content
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[WhatsApp] media download failed %s: %s",
            exc.response.status_code, exc.response.text[:300],
        )
        raise RuntimeError(f"WhatsApp media download failed: {exc.response.status_code}") from exc
    finally:
        if own:
            await _client.aclose()


async def _send_message(
    message: dict,
    to: str,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """POST one message payload to /{phone_number_id}/messages."""
    _require_config()
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        **message,
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    _client = client or httpx.AsyncClient(timeout=15.0)
    own = client is None
    try:
        response = await _client.post(
            f"{_base_url()}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("[WhatsApp] sent type=%s to=%s", message.get("type"), to)
        return data
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[WhatsApp] send failed %s: %s",
            exc.response.status_code, exc.response.text[:500],
        )
        raise RuntimeError(f"WhatsApp send failed: {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.error("[WhatsApp] network error: %s", exc)
        raise RuntimeError(f"WhatsApp network error: {exc}") from exc
    finally:
        if own:
            await _client.aclose()
