"""
modal_app.py — Modal deployment entrypoint for the Sahaiy AI Call Agent backend.

Serves the FastAPI app (sahaiy-backend/app/main.py) as a public HTTPS + WSS
endpoint via @modal.asgi_app(). Voice stack runs entirely on cloud APIs:
  - LLM:  NVIDIA NIM (OpenAI-compatible) — needs NVIDIA_API_KEY
  - STT:  Sarvam saaras:v3 — needs SARVAM_API_KEY
  - TTS:  Sarvam bulbul:v2 — needs SARVAM_API_KEY
Local whisper.cpp / llama.cpp fallbacks stay config-dormant (no local servers
exist inside the container; code paths degrade gracefully when keys are set).

Secrets come from Modal Secrets — NEVER hardcoded here.

Deploy:  modal deploy modal_app.py
Local:   modal serve modal_app.py
"""

import os
from pathlib import Path

import modal

# ── Image ────────────────────────────────────────────────────────────────────
# python slim + requirements. espeak-ng included in case TTS post-processing
# or audio utilities ever need a system phoneme engine (cheap, ~2MB).
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("espeak-ng", "ffmpeg")
    .pip_install_from_requirements(str(Path(__file__).parent / "requirements.modal.txt"))
    .add_local_python_source("app")           # backend package: sahaiy-backend/app/
    .add_local_file(
        str(Path(__file__).parent / "app" / "config.py"),
        remote_path="/root/app/config.py",
    )
)

# ── Secrets (Modal Secrets, created out-of-band) ─────────────────────────────
# modal secret create sahaiy-cloud \
#   SARVAM_API_KEY=... NVIDIA_API_KEY=... \
#   SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
secrets = [modal.Secret.from_name("sahaiy-cloud")]

app = modal.App("sahaiy-backend", secrets=secrets)


@app.function(
    image=image,
    # Keep one warm container during business hours so recruiters don't hit
    # cold starts; scales to zero otherwise (Starter credits cover this easily).
    min_containers=0,
    scaledown_window=60 * 5,      # stay warm 5 min after last request
    timeout=300,
    # WebSockets need long-lived connections — no hard idle cut below.
    max_containers=1,             # single FastAPI app; SQLite/Supabase friendly
)
@modal.concurrent(max_inputs=100)  # WS + REST share one container
@modal.asgi_app()
def api():
    # Import inside the function so Modal picks up runtime env from Secrets.
    import sys

    sys.path.insert(0, "/root")

    # CORS: allowlist is env-driven; Vercel prod origin injected at deploy time.
    if not os.environ.get("FRONTEND_ORIGINS"):
        raise RuntimeError(
            "FRONTEND_ORIGINS not set — add it to the 'sahaiy-runtime' Modal "
            "secret (comma-separated, incl. https://sahaiy.vercel.app)."
        )

    from app.main import app as fastapi_app
    return fastapi_app


# ── Keep-alive ping (optional cron) ─────────────────────────────────────────
# Cold start after scale-to-zero is ~10-30 s. A daily ping keeps logs alive;
# uncomment to enable. Cost: negligible on $30/mo Starter credits.
#
# @app.function(image=image, schedule=modal.Cron("0 * * * *"))
# def keepalive():
#     import httpx
#     url = os.environ["SAHAIY_MODAL_URL"].rstrip("/")
#     httpx.get(url + "/health", timeout=30)
