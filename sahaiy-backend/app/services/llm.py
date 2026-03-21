"""
app/services/llm.py
Async streaming LLM client for llama.cpp server (llama-server).
Yields sentence-boundary flushed text fragments to the caller so TTS starts early.
"""

import json
import logging
import re
from typing import AsyncGenerator, Optional

import httpx

from app.config import LLM_URL, LLM_N_PREDICT, LLM_TEMPERATURE

logger = logging.getLogger(__name__)

_FLUSH_RE = re.compile(r"[.!?,;:\n]")
_MAX_PENDING = 40  # flush after N tokens even without punctuation

# Phi-3 prompt tokens assembled via chr() to avoid XML-parser issues
_SYS_OPEN  = chr(60) + chr(124) + "system"    + chr(124) + chr(62)
_SYS_CLOSE = chr(60) + chr(124) + "end"       + chr(124) + chr(62)
_USR_OPEN  = chr(60) + chr(124) + "user"      + chr(124) + chr(62)
_USR_CLOSE = chr(60) + chr(124) + "end"       + chr(124) + chr(62)
_AST_OPEN  = chr(60) + chr(124) + "assistant" + chr(124) + chr(62)


def build_prompt(agent: dict, user_text: str, context: str = "") -> str:
    """Build a Phi-3 ChatML prompt from agent config + user text + optional RAG context."""
    system_prompt = agent.get("system_prompt") or "You are a helpful AI call agent."
    preferred_language = (agent.get("language") or agent.get("voice_lang") or "").strip()
    language_policy = "Mirror the user's language. If the user speaks Hindi or Hinglish, reply in Hindi/Hinglish."
    if preferred_language:
        language_policy += f" Preferred response language: {preferred_language}."

    identity_policy = "Introduce yourself consistently based on your configured persona and avoid generic disclaimers."
    ctx = ("\n\nRelevant context:\n" + context) if context else ""
    return (
        _SYS_OPEN + "\n" + system_prompt + "\n\n" + language_policy + "\n" + identity_policy + ctx + "\n" + _SYS_CLOSE + "\n"
        + _USR_OPEN + "\n" + user_text + "\n" + _USR_CLOSE + "\n"
        + _AST_OPEN + "\n"
    )


async def stream_llm(
    prompt: str,
    agent: dict,
    client: Optional[httpx.AsyncClient] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream LLM tokens from llama-server and yield sentence fragments.

    Fragments are flushed when:
    - A sentence-boundary punctuation character is encountered
    - _MAX_PENDING tokens have accumulated (safety valve)
    - The model signals stop

    Args:
        prompt: Full formatted prompt string.
        agent:  Supabase agent row (may override n_predict).
        client: Optional shared httpx.AsyncClient.

    Yields:
        Sentence fragment strings suitable for TTS.
    """
    payload = {
        "prompt": prompt,
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
        logger.error("[LLM] stream error: %s", exc)
        raise
    finally:
        if not client:
            await _client.aclose()
