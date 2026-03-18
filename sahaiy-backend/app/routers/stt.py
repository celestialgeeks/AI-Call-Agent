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
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import STT_URL, STT_TIMEOUT_SEC

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stt", tags=["STT"])


@router.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    """
    Accepts an audio file (WAV/WebM/OGG) and returns the transcribed text.
    Forwards the file to whisper.cpp HTTP inference server.

    Latency target: 150–250 ms (within STT_TIMEOUT_SEC limit).
    """
    t0 = time.monotonic()

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

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
    except Exception as exc:
        logger.error("[STT] whisper.cpp error: %s", exc)
        raise HTTPException(status_code=504, detail=f"STT service error: {exc}") from exc

    data = response.json()
    text = data.get("text", "").strip()
    duration_ms = int((time.monotonic() - t0) * 1000)

    logger.info("[STT] '%s' (%d ms)", text[:80], duration_ms)
    return JSONResponse({"text": text, "duration_ms": duration_ms})
