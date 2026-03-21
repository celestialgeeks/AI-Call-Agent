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
import re
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

_EMPTY_TRANSCRIPT_PATTERNS = [
    re.compile(r"^\s*$", re.IGNORECASE),
    re.compile(r"^[\[(]?blank[_\s-]?audio[\])]?$", re.IGNORECASE),
    re.compile(r"^[\[(]?silence[\])]?$", re.IGNORECASE),
    re.compile(r"^[\[(]?noise[\])]?$", re.IGNORECASE),
    re.compile(r"^[\[(]?inaudible[\])]?$", re.IGNORECASE),
    re.compile(r"^[\[(]?music[\])]?$", re.IGNORECASE),
]

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _lang_hint_from_label(label: str) -> Optional[str]:
    """Map human language labels to short Whisper language hints."""
    value = (label or "").lower()
    if not value:
        return None
    if "hindi" in value or "hinglish" in value:
        return "hi"
    if "english" in value:
        return "en"
    if "tamil" in value:
        return "ta"
    if "telugu" in value:
        return "te"
    if "marathi" in value:
        return "mr"
    if "bengali" in value:
        return "bn"
    if "kannada" in value:
        return "kn"
    return None


def _tts_lang_from_agent(agent: dict, user_text: str = "") -> Optional[str]:
    """Resolve Sarvam TTS language code from agent config and current user text."""
    label = str(agent.get("language") or agent.get("voice_lang") or "")
    combined = f"{label} {user_text}".lower()

    if _DEVANAGARI_RE.search(user_text) or "hindi" in combined or "hinglish" in combined:
        return "hi-IN"
    if "tamil" in combined:
        return "ta-IN"
    if "telugu" in combined:
        return "te-IN"
    if "marathi" in combined:
        return "mr-IN"
    if "bengali" in combined:
        return "bn-IN"
    if "kannada" in combined:
        return "kn-IN"
    if "english" in combined:
        return "en-IN"
    return None


def _merge_agent_meta(base_agent: dict, incoming: dict) -> dict:
    """Merge client-provided agent metadata into current agent config."""
    if not isinstance(incoming, dict):
        return base_agent
    merged = dict(base_agent or {})
    for key in ["id", "name", "system_prompt", "first_message", "voice_name", "language", "voice_lang"]:
        value = incoming.get(key)
        if value is not None and str(value).strip() != "":
            merged[key] = value
    return merged


def _normalize_transcript(text: str) -> str:
    """Normalize STT output and drop placeholder/no-audio transcripts."""
    normalized = (text or "").strip()
    if not normalized:
        return ""
    for pattern in _EMPTY_TRANSCRIPT_PATTERNS:
        if pattern.fullmatch(normalized):
            return ""
    return normalized


def _pcm16_to_wav(pcm_bytes: bytes, sample_rate: int, channels: int) -> bytes:
    """Wrap raw PCM16 bytes in a WAV container."""
    with io.BytesIO() as wav_buffer:
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(max(1, int(channels or 1)))
            wav_file.setsampwidth(2)  # PCM16
            wav_file.setframerate(max(8_000, int(sample_rate or 16_000)))
            wav_file.writeframes(pcm_bytes)
        return wav_buffer.getvalue()


async def _transcribe(
    audio_bytes: bytes,
    http_client: httpx.AsyncClient,
    file_name: str = "chunk.wav",
    content_type: str = "audio/wav",
    language_hint: Optional[str] = None,
) -> str:
    """Send audio bytes to whisper.cpp and return transcribed text."""
    try:
        resp = await http_client.post(
            STT_URL,
            files={"file": (file_name, audio_bytes, content_type)},
            data={"language": language_hint} if language_hint else None,
            timeout=float(STT_TIMEOUT_SEC),
        )
        resp.raise_for_status()
        raw_text = resp.json().get("text", "")
        return _normalize_transcript(raw_text)
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
        agent = {
            "name": "Sahaiy Assistant",
            "system_prompt": "You are a helpful AI call agent.",
            "voice_name": None,
            "language": "Hindi / English (Hinglish)",
        }
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
    audio_format = "wav"
    audio_mime_type = "audio/wav"
    audio_sample_rate = 16_000
    audio_channels = 1
    speech_chunks_in_buffer = 0
    stt_language_hint = _lang_hint_from_label(str(agent.get("language") or agent.get("voice_lang") or ""))
    greeting_sent = False

    async def _send_greeting_once() -> None:
        """Send optional first_message once per connection."""
        nonlocal greeting_sent
        if greeting_sent:
            return
        first_msg = str(agent.get("first_message") or "").strip()
        if not first_msg:
            greeting_sent = True
            return
        try:
            logger.info("[WS] Sending first_message: '%s'", first_msg)
            await _send_json(ws, {"type": "fragment", "text": first_msg})
            wav_bytes = await speak_to_bytes(
                first_msg,
                voice_name=agent.get("voice_name"),
                lang=_tts_lang_from_agent(agent, ""),
                client=http_client,
            )
            await _send_audio(ws, wav_bytes)
            greeting_sent = True
        except Exception as exc:
            logger.error("[WS/Greeting] %s", exc)

    async def _run_pipeline(user_text: str) -> None:
        """Run STT -> RAG -> LLM streaming -> TTS for one utterance."""
        nonlocal is_playing
        interrupt_event.clear()

        # RAG context retrieval
        context = await rag.retrieve_context(user_id, user_text) if user_id else ""

        # Build prompt
        prompt = build_prompt(agent, user_text, context)
        tts_lang = _tts_lang_from_agent(agent, user_text)

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
                        lang=tts_lang,
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

            if message.get("type") == "websocket.disconnect":
                break

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
                elif ctrl.get("type") == "text_input":
                    greeting_sent = True
                    user_text = str(ctrl.get("message") or "").strip()
                    if not user_text:
                        continue

                    await _send_json(ws, {"type": "transcript", "text": user_text})

                    if current_llm_task and not current_llm_task.done():
                        current_llm_task.cancel()

                    current_llm_task = asyncio.create_task(_run_pipeline(user_text))
                elif ctrl.get("type") == "agent_meta":
                    incoming_agent = ctrl.get("agent") if isinstance(ctrl.get("agent"), dict) else ctrl
                    agent = _merge_agent_meta(agent, incoming_agent)
                    stt_language_hint = _lang_hint_from_label(str(agent.get("language") or agent.get("voice_lang") or ""))
                elif ctrl.get("type") == "audio_meta":
                    incoming_format = str(ctrl.get("format") or "").strip().lower()
                    incoming_mime = str(ctrl.get("mime_type") or "").strip().lower()

                    if incoming_format:
                        audio_format = incoming_format
                    if incoming_mime:
                        audio_mime_type = incoming_mime
                    elif audio_format == "pcm16":
                        audio_mime_type = "audio/pcm"

                    try:
                        audio_sample_rate = int(ctrl.get("sample_rate") or audio_sample_rate)
                    except Exception:
                        pass
                    try:
                        audio_channels = int(ctrl.get("channels") or audio_channels)
                    except Exception:
                        pass

                    logger.info(
                        "[WS] audio_meta format=%s mime=%s sample_rate=%s channels=%s",
                        audio_format,
                        audio_mime_type,
                        audio_sample_rate,
                        audio_channels,
                    )

                if ctrl.get("type") in {"audio_meta", "agent_meta"}:
                    await _send_greeting_once()
                continue

            # --- Audio chunk ---
            chunk = message.get("bytes")
            if not chunk:
                continue

            if not greeting_sent:
                await _send_greeting_once()

            chunk_has_speech = audio_format == "pcm16" and is_speech(chunk)

            # VAD: if AI is playing and user starts speaking -> interrupt
            if is_playing:
                if chunk_has_speech:
                    interrupt_event.set()
                    if current_llm_task and not current_llm_task.done():
                        current_llm_task.cancel()
                    await _send_json(ws, {"type": "interrupted"})
                    audio_buffer.clear()
                    speech_chunks_in_buffer = 0
                continue

            audio_buffer.extend(chunk)
            if chunk_has_speech:
                speech_chunks_in_buffer += 1

            # Accumulate enough audio before sending to STT.
            if audio_format == "pcm16":
                stt_threshold_bytes = max(int(audio_sample_rate * max(audio_channels, 1) * 2 * 0.8), 12_800)
            else:
                stt_threshold_bytes = 64_000

            if len(audio_buffer) < stt_threshold_bytes:
                continue

            if audio_format == "pcm16" and speech_chunks_in_buffer == 0:
                audio_buffer.clear()
                continue

            # Capture and reset buffer
            raw_chunk = bytes(audio_buffer)
            audio_buffer.clear()
            speech_chunks_in_buffer = 0

            if audio_format == "pcm16":
                audio_payload = _pcm16_to_wav(raw_chunk, audio_sample_rate, audio_channels)
                payload_file_name = "chunk.wav"
                payload_content_type = "audio/wav"
            else:
                audio_payload = raw_chunk
                if "webm" in audio_mime_type or audio_format == "webm":
                    payload_file_name = "chunk.webm"
                    payload_content_type = "audio/webm"
                elif "ogg" in audio_mime_type or audio_format == "ogg":
                    payload_file_name = "chunk.ogg"
                    payload_content_type = "audio/ogg"
                else:
                    payload_file_name = "chunk.wav"
                    payload_content_type = "audio/wav"

            t0 = time.monotonic()
            user_text = await _transcribe(
                audio_payload,
                http_client,
                file_name=payload_file_name,
                content_type=payload_content_type,
                language_hint=stt_language_hint,
            )
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
