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
