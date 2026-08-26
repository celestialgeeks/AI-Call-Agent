"""
app/config.py
─────────────
Centralised configuration loaded from environment variables.
All service URLs and secrets live here — never hardcoded elsewhere.
"""

import os
from dotenv import load_dotenv

# Load .env from the sahaiy-backend directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


def _parse_csv_env(var_name: str, fallback: list[str]) -> list[str]:
	raw = os.getenv(var_name, "").strip()
	if not raw:
		return fallback
	values = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
	return values or fallback


def _parse_bool_env(var_name: str, default: bool = False) -> bool:
	raw = os.getenv(var_name, str(default)).strip().lower()
	return raw in {"1", "true", "yes", "on"}


# ── LLM (llama.cpp server) ─────────────────────────────────────────────────
LLM_URL: str = os.getenv("LLM_URL", "http://localhost:8080/completion")
LLM_N_PREDICT: int = int(os.getenv("LLM_N_PREDICT", "80"))
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.6"))

# ── STT (whisper.cpp server) ───────────────────────────────────────────────
STT_URL: str = os.getenv("STT_URL", "http://localhost:8081/inference")
STT_TIMEOUT_SEC: int = int(os.getenv("STT_TIMEOUT_SEC", "5"))

# ── TTS (Sarvam AI) ────────────────────────────────────────────────────────
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
SARVAM_TTS_URL: str = "https://api.sarvam.ai/text-to-speech"
SARVAM_TTS_MODEL: str = os.getenv("SARVAM_TTS_MODEL", "bulbul:v2")
SARVAM_TTS_LANG: str = os.getenv("SARVAM_TTS_LANG", "en-IN")
SARVAM_TTS_SPEAKER: str = os.getenv("SARVAM_TTS_SPEAKER", "anushka")

# ── Supabase (server-side service role) ────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
# HS256 secret used to verify Supabase-issued JWTs (Ruling B1).
# Found in: Supabase Dashboard → Settings → API → JWT Secret
SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")

# ── Auth / Security ───────────────────────────────────────────────────────
# Feature flag: when False (default), endpoints keep the legacy client-declared
# user_id behaviour. When True, every protected endpoint requires a valid
# `Authorization: Bearer <jwt>` and user identity is derived from the token ONLY.
AUTH_ENFORCED: bool = _parse_bool_env("AUTH_ENFORCED", False)

# Rate limits (issue #4 item 4): requests/minute on expensive endpoints.
# 0 disables the limiter.
RATE_LIMIT_STT_RPM: int = int(os.getenv("RATE_LIMIT_STT_RPM", "20"))
RATE_LIMIT_WS_PER_MIN: int = int(os.getenv("RATE_LIMIT_WS_PER_MIN", "10"))

# ── Auth (JWT HS256 — ruling B1, issues #4/#7) ───────────────────────────
SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")
AUTH_ENFORCED: bool = _parse_bool_env("AUTH_ENFORCED", False)

# ── Campaigns (outreach v1 — issue #7) ───────────────────────────────────
CAMPAIGN_CSV_MAX_BYTES: int = int(os.getenv("CAMPAIGN_CSV_MAX_BYTES", str(5 * 1024 * 1024)))
CAMPAIGN_WORKER_POLL_SEC: float = float(os.getenv("CAMPAIGN_WORKER_POLL_SEC", "2"))
CAMPAIGN_MAX_CONCURRENT_CALLS: int = int(os.getenv("CAMPAIGN_MAX_CONCURRENT_CALLS", "3"))
CAMPAIGN_WS_TEXT_INPUT_TIMEOUT_SEC: float = float(
    os.getenv("CAMPAIGN_WS_TEXT_INPUT_TIMEOUT_SEC", "20")
)
CAMPAIGN_SIM_CONVERSATION_TURNS: int = int(os.getenv("CAMPAIGN_SIM_CONVERSATION_TURNS", "2"))

# ── Rate limits (issue #4 item 4) ──────────────────────────────────────────
# Requests/minute on expensive endpoints, per caller identity. 0 disables.
RATE_LIMIT_STT_RPM: int = int(os.getenv("RATE_LIMIT_STT_RPM", "20"))
RATE_LIMIT_WS_PER_MIN: int = int(os.getenv("RATE_LIMIT_WS_PER_MIN", "10"))

# ── LiveKit ────────────────────────────────────────────────────────────────
LIVEKIT_URL: str = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY: str = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET: str = os.getenv("LIVEKIT_API_SECRET", "")

# ── App ────────────────────────────────────────────────────────────────────
FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
FRONTEND_ORIGINS: list[str] = _parse_csv_env(
	"FRONTEND_ORIGINS",
	[
		FRONTEND_ORIGIN,
		"http://localhost:5173",
		"http://127.0.0.1:5173",
		"http://localhost:3000",
		"http://127.0.0.1:3000",
		"https://sahaiy.vercel.app",
	],
)
ALLOW_VERCEL_PREVIEW_ORIGINS: bool = _parse_bool_env("ALLOW_VERCEL_PREVIEW_ORIGINS", True)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")
