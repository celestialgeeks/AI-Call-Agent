"""
app/services/stt.py
────────────────────
Speech-to-Text service.

Primary: Sarvam AI ASR (saaras:v3) — works on any deployed backend (cloud or
local) as long as SARVAM_API_KEY is set. This is what makes the voice agent
hear the user on Render/Vercel, where the old whisper.cpp (localhost:8081)
server does not exist.

Fallback: if SARVAM_API_KEY is unset and STT_URL points at a reachable
whisper.cpp (localhost dev), it is used transparently.
"""

import base64
import logging
from typing import Optional

import httpx

from app.config import (
    SARVAM_API_KEY,
    SARVAM_STT_URL,
    STT_URL,
    STT_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)

# Map Whisper-style 2-letter hints to Sarvam BCP-47 codes (best-effort).
_LANG_MAP = {
    "hi": "hi-IN",
    "en": "en-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "mr": "mr-IN",
    "bn": "bn-IN",
    "kn": "kn-IN",
}


def _sarvam_lang(language_hint: Optional[str]) -> Optional[str]:
    if not language_hint:
        return None
    hint = language_hint.strip().lower()
    if hint in _LANG_MAP:
        return _LANG_MAP[hint]
    # Already a BCP-47 code (e.g. hi-IN) — pass through.
    if "-" in hint:
        return hint
    return None


async def transcribe_audio(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient] = None,
    file_name: str = "chunk.wav",
    content_type: str = "audio/wav",
    language_hint: Optional[str] = None,
) -> str:
    """
    Transcribe raw audio bytes to text.

    Uses Sarvam ASR when SARVAM_API_KEY is configured (cloud-safe), otherwise
    falls back to the configured STT_URL (whisper.cpp for local dev).

    Returns:
        Normalized transcript string, or "" on any failure.
    """
    if SARVAM_API_KEY:
        try:
            return await _transcribe_sarvam(
                audio_bytes, client, file_name, content_type, language_hint
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[STT/Sarvam] failed, falling back: %s", exc)

    # Fallback: legacy whisper.cpp endpoint.
    return await _transcribe_whisper(
        audio_bytes, client, file_name, content_type, language_hint
    )


async def _transcribe_sarvam(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient],
    file_name: str,
    content_type: str,
    language_hint: Optional[str],
) -> str:
    payload: dict = {
        "model": "saaras:v3",
        "mode": "transcribe",
    }
    sarvam_lang = _sarvam_lang(language_hint)
    if sarvam_lang:
        payload["language_code"] = sarvam_lang

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
    }

    _client = client or httpx.AsyncClient(timeout=float(STT_TIMEOUT_SEC))
    try:
        response = await _client.post(
            SARVAM_STT_URL,
            data=payload,
            files={"file": (file_name, audio_bytes, content_type)},
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        text = (data.get("transcript") or "").strip()
        return text
    finally:
        if not client:
            await _client.aclose()


async def _transcribe_whisper(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient],
    file_name: str,
    content_type: str,
    language_hint: Optional[str],
) -> str:
    _client = client or httpx.AsyncClient(timeout=float(STT_TIMEOUT_SEC))
    try:
        response = await _client.post(
            STT_URL,
            files={"file": (file_name, audio_bytes, content_type)},
            data={"language": language_hint} if language_hint else None,
            timeout=float(STT_TIMEOUT_SEC),
        )
        response.raise_for_status()
        return response.json().get("text", "").strip()
    finally:
        if not client:
            await _client.aclose()
