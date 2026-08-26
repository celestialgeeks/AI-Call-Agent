"""
app/services/llm.py
Async streaming LLM client.
Primary:  NVIDIA NIM — OpenAI-compatible chat completions over httpx SSE
          (model: nvidia/nvidia-nemotron-nano-9b-v2, config-driven via NIM_MODEL).
Fallback: local llama.cpp server (/completion) when NIM_API_KEY is absent,
          so the voice pipeline keeps working offline.

Both paths yield sentence-boundary flushed text fragments to the caller so
TTS starts early. The contract consumed by audio_ws.py is unchanged:
    async for fragment in stream_llm(prompt, agent, client=http_client): ...
"""

import json
import logging
import re
from typing import AsyncGenerator, Optional

import httpx

from app.config import (
    LLM_N_PREDICT,
    LLM_TEMPERATURE,
    LLM_URL,
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_MODEL,
)

logger = logging.getLogger(__name__)

_FLUSH_RE = re.compile(r"[.!?,;:\n।]")  # incl. Hindi danda — Hindi replies must flush at sentence end
_MAX_PENDING = 40  # flush after N tokens even without punctuation

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)

# Phi-3 prompt tokens assembled via chr() to avoid XML-parser issues
_SYS_OPEN  = chr(60) + chr(124) + "system"    + chr(124) + chr(62)
_SYS_CLOSE = chr(60) + chr(124) + "end"       + chr(124) + chr(62)
_USR_OPEN  = chr(60) + chr(124) + "user"      + chr(124) + chr(62)
_USR_CLOSE = chr(60) + chr(124) + "end"       + chr(124) + chr(62)
_AST_OPEN  = chr(60) + chr(124) + "assistant" + chr(124) + chr(62)


def _strip_think(text: str) -> str:
    """Defensively strip Nemotron <think> reasoning traces from a fragment."""
    text = _THINK_RE.sub("", text)
    # An unclosed <think> means we're still inside the reasoning block — emit nothing.
    if _THINK_UNCLOSED_RE.search(text):
        return ""
    return text


def build_prompt(agent: dict, user_text: str, context: str = "") -> dict:
    """
    Build the prompt sent to the LLM.

    - NIM path: rendered as chat messages; /no_think appended to the system
      prompt as Nemotron's reasoning control so replies start instantly.
    - llama.cpp fallback path: rendered as a Phi-3 ChatML string (unchanged).
    """
    system_prompt = agent.get("system_prompt") or "You are a helpful AI call agent."
    preferred_language = (agent.get("language") or agent.get("voice_lang") or "").strip()
    language_policy = "Mirror the user's language. If the user speaks Hindi or Hinglish, reply in Hindi/Hinglish."
    if preferred_language:
        language_policy += f" Preferred response language: {preferred_language}."

    identity_policy = "Introduce yourself consistently based on your configured persona and avoid generic disclaimers."
    ctx = ("\n\nRelevant context:\n" + context) if context else ""
    nim_system_prompt = f"{system_prompt}\n\n{language_policy}\n{identity_policy}{ctx}\n\n/no_think"

    prompt_out: dict = {
        "messages": [
            {"role": "system", "content": nim_system_prompt},
            {"role": "user", "content": user_text},
        ],
        # Raw ChatML prompt kept for the llama.cpp fallback path.
        "chatml": (
            _SYS_OPEN + "\n" + system_prompt + "\n\n" + language_policy + "\n" + identity_policy + ctx + "\n" + _SYS_CLOSE + "\n"
            + _USR_OPEN + "\n" + user_text + "\n" + _USR_CLOSE + "\n"
            + _AST_OPEN + "\n"
        ),
    }
    return prompt_out


async def _stream_nim(
    messages: list,
    agent: dict,
    client: Optional[httpx.AsyncClient],
) -> AsyncGenerator[str, None]:
    """Stream sentence fragments from NVIDIA NIM chat completions (SSE)."""
    payload = {
        "model": NIM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "top_p": 0.9,
        "max_tokens": agent.get("llm_n_predict", LLM_N_PREDICT),
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {NIM_API_KEY}",
        "Accept": "text/event-stream",
    }

    _client = client or httpx.AsyncClient(timeout=30.0)
    pending = ""
    pending_count = 0

    try:
        async with _client.stream(
            "POST",
            f"{NIM_BASE_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                token = ((choices[0].get("delta") or {}).get("content")) if choices else ""
                if not token:
                    continue
                token = _strip_think(token)
                if not token:
                    continue
                pending += token
                pending_count += 1
                if _FLUSH_RE.search(token) or pending_count >= _MAX_PENDING:
                    fragment = pending.strip()
                    if fragment:
                        yield fragment
                    pending = ""
                    pending_count = 0
            if pending.strip():
                yield pending.strip()
    except Exception as exc:
        logger.error("[LLM/NIM] stream error: %s", exc)
        raise
    finally:
        if not client:
            await _client.aclose()


async def _stream_llamacpp(
    chatml_prompt: str,
    agent: dict,
    client: Optional[httpx.AsyncClient] = None,
) -> AsyncGenerator[str, None]:
    """Fallback: stream sentence fragments from the local llama.cpp server."""
    payload = {
        "prompt": chatml_prompt,
        "n_predict": agent.get("llm_n_predict", LLM_N_PREDICT),
        "temperature": LLM_TEMPERATURE,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "stop": ["User:", _USR_OPEN, "\n\n"],
        "stream": True,
    }

    _client = client or httpx.AsyncClient(timeout=30.0)
    pending = ""
    pending_count = 0

    try:
        async with _client.stream("POST", LLM_URL, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                token = obj.get("content", "")
                if obj.get("stop"):
                    if pending.strip():
                        yield pending.strip()
                    pending = ""
                    break
                pending += token
                pending_count += 1
                if _FLUSH_RE.search(token) or pending_count >= _MAX_PENDING:
                    fragment = pending.strip()
                    if fragment:
                        yield fragment
                    pending = ""
                    pending_count = 0
    except Exception as exc:
        logger.error("[LLM/llama.cpp] stream error: %s", exc)
        raise
    finally:
        if not client:
            await _client.aclose()


async def stream_llm(
    prompt,
    agent: dict,
    client: Optional[httpx.AsyncClient] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream sentence-boundary text fragments for early TTS.

    Uses NVIDIA NIM when NIM_API_KEY is set; otherwise falls back to the
    local llama.cpp server with a warning (no crash). Accepts both the new
    dict prompt ({"messages", "chatml"}) and legacy plain-string prompts
    (routed straight to llama.cpp).

    Args:
        prompt: Dict from build_prompt() (or legacy ChatML string).
        agent:  Supabase agent row (may override max_tokens).
        client: Optional shared httpx.AsyncClient.

    Yields:
        Sentence fragment strings suitable for TTS.
    """
    if isinstance(prompt, dict):
        messages, chatml_prompt = prompt["messages"], prompt["chatml"]
    else:  # legacy caller passing a raw string
        messages, chatml_prompt = None, prompt

    if NIM_API_KEY and messages is not None:
        logger.info("[LLM] provider=NIM model=%s", NIM_MODEL)
        async for fragment in _stream_nim(messages, agent, client):
            yield fragment
    else:
        if not NIM_API_KEY:
            logger.warning("[LLM] NVIDIA_API_KEY not set — falling back to llama.cpp (%s)", LLM_URL)
        else:
            logger.warning("[LLM] legacy string prompt — using llama.cpp fallback")
        async for fragment in _stream_llamacpp(chatml_prompt, agent, client):
            yield fragment
