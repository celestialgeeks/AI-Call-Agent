"""
app/routers/stt.py
───────────────────
Speech-to-Text router — forwards audio to whisper.cpp server.

POST /stt/transcribe
    Body: multipart/form-data { file: audio/* }
    Response: { text: string, duration_ms: int }
"""

import time
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.auth import get_current_user_id
from app.config import STT_URL, STT_TIMEOUT_SEC
from app.errors import ApiError, new_request_id
from app.ratelimit import check_stt_rate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stt", tags=["STT"])


@router.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Accepts an audio file (WAV/WebM/OGG) and returns the transcribed text.
    Forwards the file to whisper.cpp HTTP inference server.

    Rate limited: RATE_LIMIT_STT_RPM per identity (default 20/min).
    Latency target: 150–250 ms (within STT_TIMEOUT_SEC limit).
    """
    await check_stt_rate(request, current_user_id)

    t0 = time.monotonic()

    audio_bytes = await file.read()
    if not audio_bytes:
        raise ApiError(400, "empty_file", "Empty audio file.")

    # Use the shared httpx client stored in app state (set up in main.py lifespan)
    http_client = getattr(request.app.state, "http_client", None)
    if http_client is None:
        import httpx
        http_client = httpx.AsyncClient(timeout=float(STT_TIMEOUT_SEC))

    try:
        response = await http_client.post(
            STT_URL,
            files={"file": (file.filename or "audio.wav", audio_bytes, file.content_type or "audio/wav")},
            timeout=float(STT_TIMEOUT_SEC),
        )
        response.raise_for_status()
    except ApiError:
        raise
    except Exception as exc:
        rid = getattr(request.state, "request_id", new_request_id())
        logger.error("[STT] whisper.cpp error (request_id=%s): %s", rid, exc)
        raise ApiError(504, "upstream_error", "Speech-to-text service is unavailable.") from exc

    data = response.json()
    text = data.get("text", "").strip()
    duration_ms = int((time.monotonic() - t0) * 1000)

    logger.info("[STT] '%s' (%d ms)", text[:80], duration_ms)
    return JSONResponse({"text": text, "duration_ms": duration_ms})
