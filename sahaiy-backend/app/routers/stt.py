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

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import STT_URL, STT_TIMEOUT_SEC
from app.errors import ApiError
from app.ratelimit import check_stt_rate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stt", tags=["STT"])


@router.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    """
    Accepts an audio file (WAV/WebM/OGG) and returns the transcribed text.
    Forwards the file to whisper.cpp HTTP inference server.

    Rate limited (issue #4 item 4): RATE_LIMIT_STT_RPM per client IP
    (default 20/min) — 429 in the uniform envelope when exceeded.
    Latency target: 150–250 ms (within STT_TIMEOUT_SEC limit).
    """
    await check_stt_rate(request, None)

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
        logger.error("[STT] whisper.cpp error: %s", exc)
        raise HTTPException(status_code=504, detail="STT service error") from exc

    data = response.json()
    text = data.get("text", "").strip()
    duration_ms = int((time.monotonic() - t0) * 1000)

    logger.info("[STT] '%s' (%d ms)", text[:80], duration_ms)
    return JSONResponse({"text": text, "duration_ms": duration_ms})
