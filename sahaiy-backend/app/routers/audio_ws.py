"""
app/routers/audio_ws.py
────────────────────────
WebSocket audio pipeline: audio in -> STT -> LLM (streaming) -> TTS (Sarvam) -> audio out.

WS /ws/audio?agent_id=<uuid>&user_id=<uuid>

Protocol:
  Client sends: raw audio bytes (WAV chunks, 20-50 ms)
  Server sends: interleaved JSON frames OR binary WAV audio frames
    JSON:  { "type": "transcript", "text": "..." }
    JSON:  { "type": "fragment",   "text": "..." }
    JSON:  { "type": "interrupted" }
    JSON:  { "type": "error",      "message": "..." }
    bytes: WAV audio data for playback

Interrupt handling:
  Client sends a JSON frame { "type": "interrupt" } OR energy VAD detects speech
  during AI playback -> cancels LLM stream + TTS, returns to listening.
"""

import asyncio
import io
import json
import logging
import time
import wave
from typing import Optional

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import STT_URL, STT_TIMEOUT_SEC
from app.services import agent_service, rag
from app.services.llm import build_prompt, stream_llm
from app.services.tts import speak_to_bytes
from app.services.vad import is_speech

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Audio WebSocket"])


async def _transcribe(audio_bytes: bytes, http_client: httpx.AsyncClient) -> str:
    """Send audio bytes to whisper.cpp and return transcribed text."""
    try:
        resp = await http_client.post(
            STT_URL,
            files={"file": ("chunk.wav", audio_bytes, "audio/wav")},
            timeout=float(STT_TIMEOUT_SEC),
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
    except Exception as exc:
        logger.error("[WS/STT] %s", exc)
        return ""


async def _send_json(ws: WebSocket, payload: dict) -> None:
    """Send a JSON control frame to the client."""
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _send_audio(ws: WebSocket, wav_bytes: bytes) -> None:
    """Send a WAV binary frame to the client for playback."""
    try:
        await ws.send_bytes(wav_bytes)
    except Exception:
        pass


@router.websocket("/ws/audio")
async def audio_ws(ws: WebSocket, agent_id: str = "", user_id: str = ""):
    """
    Full voice pipeline WebSocket endpoint.
    Accepts audio chunks, returns transcripts and TTS audio.
    """
    await ws.accept()
    logger.info("[WS] Connected — agent=%s user=%s", agent_id, user_id)

    # Fetch agent config
    agent = await agent_service.get_agent(agent_id) if agent_id else {}
    if not agent:
        agent = {"system_prompt": "You are a helpful AI call agent.", "voice_name": None}
        logger.warning("[WS] Agent not found — using defaults")

    # Increment call count
    if agent_id:
        await agent_service.increment_call_count(agent_id)

    # Shared HTTP client for this connection
    http_client = httpx.AsyncClient(timeout=30.0)

    # --- State ---
    audio_buffer = bytearray()
    is_playing = False          # True while AI audio is being sent
    interrupt_event = asyncio.Event()
    current_llm_task: Optional[asyncio.Task] = None

    # Optional: Send initial greeting if configured
    first_msg = agent.get("first_message")
    if first_msg:
        try:
            logger.info("[WS] Sending first_message: '%s'", first_msg)
            await _send_json(ws, {"type": "fragment", "text": first_msg})
            wav_bytes = await speak_to_bytes(
                first_msg,
                voice_name=agent.get("voice_name"),
                client=http_client,
            )
            await _send_audio(ws, wav_bytes)
        except Exception as e:
            logger.error("[WS/Greeting] %s", e)

    async def _run_pipeline(user_text: str) -> None:
        """Run STT -> RAG -> LLM streaming -> TTS for one utterance."""
        nonlocal is_playing
        interrupt_event.clear()

        # RAG context retrieval
        context = await rag.retrieve_context(user_id, user_text) if user_id else ""

        # Build prompt
        prompt = build_prompt(agent, user_text, context)

        is_playing = True
        try:
            async for fragment in stream_llm(prompt, agent, client=http_client):
                if interrupt_event.is_set():
                    logger.info("[WS] LLM stream interrupted")
                    break
                await _send_json(ws, {"type": "fragment", "text": fragment})
                try:
                    wav_bytes = await speak_to_bytes(
                        fragment,
                        voice_name=agent.get("voice_name"),
                        client=http_client,
                    )
                    if interrupt_event.is_set():
                        break
                    await _send_audio(ws, wav_bytes)
                except Exception as tts_exc:
                    logger.error("[WS/TTS] %s", tts_exc)
        except Exception as llm_exc:
            logger.error("[WS/LLM] %s", llm_exc)
            await _send_json(ws, {"type": "error", "message": str(llm_exc)})
        finally:
            is_playing = False

    try:
        while True:
            # Receive next WebSocket message (binary audio or JSON control)
            message = await ws.receive()

            # --- Control frame ---
            if message.get("text"):
                try:
                    ctrl = json.loads(message["text"])
                except Exception:
                    ctrl = {}
                if ctrl.get("type") == "interrupt":
                    interrupt_event.set()
                    if current_llm_task and not current_llm_task.done():
                        current_llm_task.cancel()
                    await _send_json(ws, {"type": "interrupted"})
                    audio_buffer.clear()
                continue

            # --- Audio chunk ---
            chunk = message.get("bytes")
            if not chunk:
                continue

            # VAD: if AI is playing and user starts speaking -> interrupt
            if is_playing and is_speech(chunk):
                interrupt_event.set()
                if current_llm_task and not current_llm_task.done():
                    current_llm_task.cancel()
                await _send_json(ws, {"type": "interrupted"})
                audio_buffer.clear()
                continue

            audio_buffer.extend(chunk)

            # Accumulate enough audio before sending to STT (~1 second @ 16kHz 16-bit mono = 32000 bytes)
            if len(audio_buffer) < 32_000:
                continue

            # Capture and reset buffer
            pcm_data = bytes(audio_buffer)
            audio_buffer.clear()

            t0 = time.monotonic()
            user_text = await _transcribe(pcm_data, http_client)
            stt_ms = int((time.monotonic() - t0) * 1000)

            if not user_text:
                continue

            logger.info("[WS] STT (%d ms): '%s'", stt_ms, user_text[:80])
            await _send_json(ws, {"type": "transcript", "text": user_text})

            # Cancel any in-flight pipeline, start fresh
            if current_llm_task and not current_llm_task.done():
                current_llm_task.cancel()

            current_llm_task = asyncio.create_task(_run_pipeline(user_text))

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected — agent=%s", agent_id)
    except Exception as exc:
        logger.error("[WS] Unexpected error: %s", exc)
    finally:
        if current_llm_task and not current_llm_task.done():
            current_llm_task.cancel()
        await http_client.aclose()
        logger.info("[WS] Connection closed — agent=%s", agent_id)
