"""
app/main.py
────────────
FastAPI application factory for the Sahaiy AI Call Agent backend.

Startup:
    Creates a shared httpx.AsyncClient for all services.
Shutdown:
    Gracefully closes the shared client.

CORS is configured to allow the Vite frontend origin.
"""

import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import FRONTEND_ORIGINS, ALLOW_VERCEL_PREVIEW_ORIGINS, LOG_LEVEL
from app.errors import (
    ApiError,
    api_error_handler,
    http_exception_handler,
    new_request_id,
    unhandled_exception_handler,
    validation_exception_handler,
)
<<<<<<< HEAD
from app.routers import stt, audio_ws, calls, knowledge, health, phone_numbers, livekit, campaigns
=======
from app.routers import stt, audio_ws, calls, knowledge, health, phone_numbers, livekit
>>>>>>> origin/fix/issue-4-backend-hardening

logging.basicConfig(
    level=LOG_LEVEL.upper(),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create shared resources on startup; tear them down on shutdown."""
    logger.info("Sahaiy backend starting up …")
    # Shared async HTTP client — reused across requests for connection pooling
    app.state.http_client = httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )
    # Outreach campaign worker (Postgres SKIP LOCKED queue — issue #7, ruling B2).
    from app.services import campaign_worker

    campaign_worker.start_worker()
    yield
    logger.info("Sahaiy backend shutting down …")
    await campaign_worker.stop_worker()
    await app.state.http_client.aclose()


app = FastAPI(
    title="Sahaiy AI Call Agent API",
    version="1.0.0",
    description="Real-time voice AI call agent backend — STT + LLM + Sarvam TTS",
    lifespan=lifespan,
)

_allowed_origins = list(dict.fromkeys(FRONTEND_ORIGINS))
_vercel_origin_regex = r"https://.*\.vercel\.app" if ALLOW_VERCEL_PREVIEW_ORIGINS else None

# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=_vercel_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Uniform error envelope (SEC-03) ───────────────────────────────────────
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a correlation id to every request and echo it in responses."""
    rid = new_request_id()
    request.state.request_id = rid
    t0 = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    logger.info("%s %s -> %d (%d ms) request_id=%s", request.method,
                request.url.path, response.status_code,
                int((time.monotonic() - t0) * 1000), rid)
    return response


# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(stt.router)
app.include_router(audio_ws.router)
app.include_router(calls.router)
app.include_router(knowledge.router)
app.include_router(phone_numbers.router)
app.include_router(livekit.router)
app.include_router(campaigns.router)


@app.get("/")
async def root():
    return {"service": "Sahaiy Backend", "version": "1.0.0", "docs": "/docs"}
