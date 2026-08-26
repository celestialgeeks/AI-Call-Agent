"""
app/routers/health.py
─────────────────────
Health check endpoint — reports per-dependency readiness.

GET /health  →  {
    status: "ok" | "degraded",
    stt:  { provider, ok },
    llm:  { provider, ok },
    tts:  { configured },
    supabase: { ok },
}

Cloud primaries (Sarvam STT/TTS, NVIDIA NIM) are considered "configured"
when their API key is present; a lightweight reachability probe is attempted
only for local fallback services. The endpoint NEVER requires local
fallbacks to be up.
"""

import logging

import httpx
from fastapi import APIRouter

from app.config import (
    LLM_URL,
    NVIDIA_API_KEY,
    SARVAM_API_KEY,
    STT_URL,
    SUPABASE_URL,
)
from app.services.llm import llm_provider
from app.services.stt import stt_provider

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


def _base(url: str) -> str:
    return url.rsplit("/", 1)[0]


@router.get("/health")
async def health_check():
    """
    Returns per-dependency readiness. Always returns 200 with status flags —
    never raises. `status` is "ok" when every *configured primary* is usable,
    "degraded" otherwise; local fallbacks being down never degrades health.
    """
    async with httpx.AsyncClient(timeout=2.0) as client:
        # ── STT ────────────────────────────────────────────────────────────
        if SARVAM_API_KEY:
            stt = {"provider": "sarvam", "ok": True}
        else:
            stt_ok = False
            try:
                r = await client.get(f"{_base(STT_URL)}/health")
                stt_ok = r.status_code == 200
            except Exception:
                stt_ok = False
            stt = {"provider": "whisper", "ok": stt_ok}

        # ── LLM ────────────────────────────────────────────────────────────
        if NVIDIA_API_KEY:
            llm = {"provider": "nim", "ok": True}
        else:
            llm_ok = False
            try:
                r = await client.get(f"{_base(LLM_URL)}/health")
                llm_ok = r.status_code == 200
            except Exception:
                llm_ok = False
            llm = {"provider": "llama", "ok": llm_ok}

        # ── TTS (Sarvam only) ──────────────────────────────────────────────
        tts = {"configured": bool(SARVAM_API_KEY)}

        # ── Supabase (configured check only — no secrets probed) ───────────
        supabase = {"ok": bool(SUPABASE_URL)}

    degraded = not (stt["ok"] and llm["ok"])
    return {
        "status": "degraded" if degraded else "ok",
        "stt": stt,
        "llm": llm,
        "tts": tts,
        "supabase": supabase,
    }
