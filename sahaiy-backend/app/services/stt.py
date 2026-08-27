"""
app/services/stt.py
───────────────────
Speech-to-text with provider selection:

  PRIMARY : Sarvam AI speech-to-text REST API (model saaras:v3) — active when
            SARVAM_API_KEY is configured (same key as TTS).
  FALLBACK: whisper.cpp HTTP inference server at STT_URL — used only when
            Sarvam is not configured, or as an explicit fallback.

Both paths return a plain transcript string and raise on failure so callers
can produce actionable error frames instead of silent empty transcripts.
"""

import logging
from typing import Optional

import httpx

from app.config import (
    SARVAM_API_KEY,
    SARVAM_STT_MODEL,
    SARVAM_STT_URL,
    STT_TIMEOUT_SEC,
    STT_URL,
)

logger = logging.getLogger(__name__)


def stt_provider() -> str:
    """Name of the STT provider that will be used for the next call."""
    return "sarvam" if SARVAM_API_KEY else "whisper"


async def transcribe_sarvam(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient] = None,
    file_name: str = "chunk.wav",
    content_type: str = "audio/wav",
    language_hint: Optional[str] = None,
) -> str:
    """
    Transcribe audio via the Sarvam speech-to-text REST API.

    POST {SARVAM_STT_URL} multipart:
        file        : audio blob
        model       : saaras:v3
        language_code : optional BCP-47-ish hint (e.g. en-IN, hi-IN)

    Response JSON: {"transcript": "...", ...}

    Raises RuntimeError on transport/HTTP errors so callers can surface
    an `stt_failed` error frame.
    """
    data = {"model": SARVAM_STT_MODEL}
    if language_hint:
        # Sarvam expects codes like "en-IN"; whisper-style "en" hints are
        # mapped to their Indian-locale equivalents where known.
        mapping = {
            "hi": "hi-IN", "en": "en-IN", "ta": "ta-IN", "te": "te-IN",
            "mr": "mr-IN", "bn": "bn-IN", "kn": "kn-IN",
        }
        data["language_code"] = mapping.get(language_hint.lower(), language_hint)

    headers = {"api-subscription-key": SARVAM_API_KEY}

    async def _post(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(
            SARVAM_STT_URL,
            headers=headers,
            files={"file": (file_name, audio_bytes, content_type)},
            data=data,
            timeout=float(max(STT_TIMEOUT_SEC, 15)),
        )

    try:
        if client is not None:
            resp = await _post(client)
        else:
            async with httpx.AsyncClient() as c:
                resp = await _post(c)
        if resp.status_code != 200:
            logger.error(
                "[STT/sarvam] HTTP %s: %s", resp.status_code, resp.text[:300]
            )
            raise RuntimeError(
                f"Sarvam STT returned HTTP {resp.status_code}"
            )
        transcript = str(resp.json().get("transcript") or "").strip()
        return transcript
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("[STT/sarvam] request failed: %s", exc)
        raise RuntimeError(f"Sarvam STT request failed: {exc}") from exc


async def transcribe_whisper(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient] = None,
    file_name: str = "chunk.wav",
    content_type: str = "audio/wav",
    language_hint: Optional[str] = None,
) -> str:
    """
    Transcribe audio via the local whisper.cpp server (STT_URL).

    Raises RuntimeError on failure so callers can surface an `stt_failed`
    error frame instead of silently returning "".
    """

    async def _post(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(
            STT_URL,
            files={"file": (file_name, audio_bytes, content_type)},
            data={"language": language_hint} if language_hint else None,
            timeout=float(STT_TIMEOUT_SEC),
        )

    try:
        if client is not None:
            resp = await _post(client)
        else:
            async with httpx.AsyncClient() as c:
                resp = await _post(c)
        if resp.status_code != 200:
            logger.error("[STT/whisper] HTTP %s from %s", resp.status_code, STT_URL)
            raise RuntimeError(f"whisper.cpp returned HTTP {resp.status_code}")
        return str(resp.json().get("text") or "").strip()
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("[STT/whisper] request failed (%s): %s", STT_URL, exc)
        raise RuntimeError(f"whisper.cpp STT request failed: {exc}") from exc


async def transcribe(
    audio_bytes: bytes,
    client: Optional[httpx.AsyncClient] = None,
    file_name: str = "chunk.wav",
    content_type: str = "audio/wav",
    language_hint: Optional[str] = None,
) -> str:
    """
    Provider-selected transcription.

    - Sarvam configured → try Sarvam; on failure fall back to whisper.cpp if
      reachable, else re-raise the Sarvam error.
    - Sarvam not configured → whisper.cpp only.
    """
    if SARVAM_API_KEY:
        try:
            text = await transcribe_sarvam(
                audio_bytes, client=client, file_name=file_name,
                content_type=content_type, language_hint=language_hint,
            )
            if text:
                return text
            # Empty transcript on success: could be silence. Return as-is;
            # caller's normalization handles placeholder filtering.
            return ""
        except Exception as sarvam_exc:
            logger.warning(
                "[STT] Sarvam failed (%s) — trying whisper.cpp fallback",
                sarvam_exc,
            )
            try:
                return await transcribe_whisper(
                    audio_bytes, client=client, file_name=file_name,
                    content_type=content_type, language_hint=language_hint,
                )
            except Exception as whisper_exc:
                logger.error(
                    "[STT] whisper.cpp fallback also failed: %s", whisper_exc
                )
                raise RuntimeError(str(sarvam_exc)) from whisper_exc

    return await transcribe_whisper(
        audio_bytes, client=client, file_name=file_name,
        content_type=content_type, language_hint=language_hint,
    )
