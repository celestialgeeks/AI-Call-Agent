"""
app/services/stt.py
───────────────────
Speech-to-Text abstraction shared by routers and services.

Primary:  Sarvam AI saaras:v3 — POST https://api.sarvam.ai/speech-to-text
          (multipart: file, model, language_code, mode) — used when
          SARVAM_API_KEY is set.
Fallback: local whisper.cpp server — POST STT_URL (/inference).

Both providers return plain transcript text via the same transcribe()
interface; callers never fork on provider.
"""

import logging
from typing import Optional

import httpx

from app.config import (
    SARVAM_API_KEY,
    SARVAM_STT_LANG,
    SARVAM_STT_MODEL,
    SARVAM_STT_TIMEOUT_SEC,
    SARVAM_STT_URL,
    STT_TIMEOUT_SEC,
    STT_URL,
)

logger = logging.getLogger(__name__)

# Whisper short hints → Sarvam BCP-47 codes (docs.sarvam.ai speech-to-text).
_WHISPER_TO_SARVAM_LANG = {
    "hi": "hi-IN",
    "en": "en-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "mr": "mr-IN",
    "bn": "bn-IN",
    "kn": "kn-IN",
}


async def _transcribe_sarvam(
    audio_bytes: bytes,
    file_name: str,
    content_type: str,
    language_hint: Optional[str],
    client: Optional[httpx.AsyncClient],
) -> str:
    """POST audio to Sarvam saaras:v3 and return the transcript text."""
    lang_code = _WHISPER_TO_SARVAM_LANG.get(language_hint or "", SARVAM_STT_LANG)
    data = {
        "model": SARVAM_STT_MODEL,
        "language_code": lang_code,
        "mode": "transcribe",
    }
    headers = {"api-subscription-key": SARVAM_API_KEY}

    _client = client or httpx.AsyncClient(timeout=float(SARVAM_STT_TIMEOUT_SEC))
    try:
        resp = await _client.post(
            SARVAM_STT_URL,
            files={"file": (file_name, audio_bytes, content_type)},
            data=data,
            headers=headers,
            timeout=float(SARVAM_STT_TIMEOUT_SEC),
        )
        resp.raise_for_status()
        text = resp.json().get("transcript", "")
        logger.debug("[STT/Sarvam] %d bytes -> %d chars (%s)", len(audio_bytes), len(text), data["language_code"])
        return text
    except Exception as exc:
        logger.warning("[STT/Sarvam] failed, will fall back to whisper.cpp: %s", exc)
        raise
    finally:
        if not client:
            await _client.aclose()


async def _transcribe_whisper(
    audio_bytes: bytes,
    file_name: str,
    content_type: str,
    language_hint: Optional[str],
    client: Optional[httpx.AsyncClient],
) -> str:
    """POST audio to the whisper.cpp server and return the transcript text."""
    _client = client or httpx.AsyncClient(timeout=float(STT_TIMEOUT_SEC))
    try:
        resp = await _client.post(
            STT_URL,
            files={"file": (file_name, audio_bytes, content_type)},
            data={"language": language_hint} if language_hint else None,
            timeout=float(STT_TIMEOUT_SEC),
        )
        resp.raise_for_status()
        return resp.json().get("text", "")
    finally:
        if not client:
            await _client.aclose()


async def transcribe(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient] = None,
    file_name: str = "chunk.wav",
    content_type: str = "audio/wav",
    language_hint: Optional[str] = None,
) -> str:
    """
    Transcribe audio bytes. Sarvam saaras:v3 primary when SARVAM_API_KEY is
    set; whisper.cpp fallback when Sarvam is absent or fails.

    Returns raw transcript text (callers normalize).
    """
    if SARVAM_API_KEY:
        try:
            return await _transcribe_sarvam(
                audio_bytes, file_name, content_type, language_hint, client
            )
        except Exception:
            pass  # logged in _transcribe_sarvam — fall through to whisper
    return await _transcribe_whisper(
        audio_bytes, file_name, content_type, language_hint, client
    )
