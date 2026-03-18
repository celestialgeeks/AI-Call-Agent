"""
app/services/tts.py
────────────────────
Text-to-Speech using Sarvam AI's Bulbul API.
Returns raw WAV bytes — no disk I/O.

Endpoint: POST https://api.sarvam.ai/text-to-speech
Docs: https://docs.sarvam.ai/api-reference/endpoints/text-to-speech
"""

import base64
import logging
from typing import Optional

import httpx

from app.config import (
    SARVAM_API_KEY,
    SARVAM_TTS_URL,
    SARVAM_TTS_MODEL,
    SARVAM_TTS_LANG,
    SARVAM_TTS_SPEAKER,
)

logger = logging.getLogger(__name__)

# Speaker name mapping: Supabase voice_name → Sarvam speaker identifier
# Full list: https://docs.sarvam.ai/api-reference/endpoints/text-to-speech#speakers
_SPEAKER_MAP: dict[str, str] = {
    "Priya": "anushka",
    "Rahul": "abhilash",
    "Anushka": "anushka",
    "Abhilash": "abhilash",
    "Manisha": "manisha",
    "Vidya": "vidya",
    "Arjun": "arjun",
    "Maya": "maya",
    "Neel": "neel",
    "Maitreyi": "maitreyi",
    "Amartya": "amartya",
}


def _resolve_speaker(voice_name: Optional[str]) -> str:
    """Map an agent's voice_name from Supabase to a Sarvam speaker ID."""
    if not voice_name:
        return SARVAM_TTS_SPEAKER
    return _SPEAKER_MAP.get(voice_name, SARVAM_TTS_SPEAKER)


async def speak_to_bytes(
    text: str,
    voice_name: Optional[str] = None,
    lang: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> bytes:
    """
    Convert text to speech via Sarvam AI and return raw WAV bytes.

    Args:
        text:       The text to synthesise (max 500 chars for v2).
        voice_name: Agent voice_name from Supabase (mapped to Sarvam speaker).
        lang:       BCP-47 language code (default: en-IN).
        client:     Optional shared httpx.AsyncClient (preferred for perf).

    Returns:
        WAV audio bytes ready to be sent over WebSocket.

    Raises:
        RuntimeError if the Sarvam API call fails.
    """
    if not SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not set — TTS unavailable.")

    speaker = _resolve_speaker(voice_name)
    target_lang = lang or SARVAM_TTS_LANG

    payload = {
        "inputs": [text],
        "target_language_code": target_lang,
        "speaker": speaker,
        "model": SARVAM_TTS_MODEL,
        "enable_preprocessing": True,
    }

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }

    _client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await _client.post(SARVAM_TTS_URL, json=payload, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("[TTS] Sarvam API error %s: %s", exc.response.status_code, exc.response.text)
        raise RuntimeError(f"Sarvam TTS failed: {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.error("[TTS] Network error: %s", exc)
        raise RuntimeError("Sarvam TTS network error") from exc
    finally:
        if not client:
            await _client.aclose()

    data = response.json()
    # Sarvam returns: { "audios": ["<base64-wav>", ...] }
    audios = data.get("audios") or []
    if not audios:
        logger.error("[TTS] Empty audios in Sarvam response: %s", data)
        raise RuntimeError("No audio returned from Sarvam TTS")

    wav_bytes = base64.b64decode(audios[0])
    logger.debug("[TTS] Generated %d bytes for %d chars", len(wav_bytes), len(text))
    return wav_bytes
