"""
app/routers/health.py
──────────────────────
Health check endpoint — verifies connectivity to whisper.cpp and llama-server.

GET /health  →  { status, stt, llm, sarvam_configured }
"""

import logging
import httpx
from fastapi import APIRouter
from app.config import STT_URL, LLM_URL, SARVAM_API_KEY

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """
    Returns connectivity status for each downstream service.
    Does NOT raise on failure — always returns 200 with status flags.
    """
    stt_ok = False
    llm_ok = False

    async with httpx.AsyncClient(timeout=2.0) as client:
        # whisper.cpp — try /health endpoint (it exposes one)
        try:
            base = STT_URL.rstrip("/inference").rstrip("/transcribe")
            r = await client.get(f"{base}/health")
            stt_ok = r.status_code == 200
        except Exception:
            stt_ok = False

        # llama-server — try /health endpoint
        try:
            base = LLM_URL.rstrip("/completion")
            r = await client.get(f"{base}/health")
            llm_ok = r.status_code == 200
        except Exception:
            llm_ok = False

    return {
        "status": "ok",
        "stt": stt_ok,
        "llm": llm_ok,
        "sarvam_configured": bool(SARVAM_API_KEY),
    }
