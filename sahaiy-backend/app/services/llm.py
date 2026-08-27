"""
app/services/llm.py
───────────────────
Async streaming LLM client with provider selection:

  PRIMARY : NVIDIA NIM — OpenAI-compatible `{NIM_BASE_URL}/chat/completions`
            (Bearer NVIDIA_API_KEY, stream=true). Active when NVIDIA_API_KEY
            is set.
  FALLBACK: local llama.cpp server ChatML completion (LLM_URL).

Both paths yield sentence-boundary flushed text fragments so TTS starts early,
and raise LLMProviderError on failure so callers can emit an `llm_failed`
error frame naming the actual failing component.
"""

import json
import logging
import re
from typing import AsyncGenerator, Optional

import httpx

from app.config import (
    LLM_N_PREDICT,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SEC,
    LLM_URL,
    NIM_BASE_URL,
    NIM_MODEL,
    NVIDIA_API_KEY,
)

logger = logging.getLogger(__name__)

_FLUSH_RE = re.compile(r"[.!?,;:\n]")
_MAX_PENDING = 40  # flush after N tokens even without punctuation


class LLMProviderError(RuntimeError):
    """Raised when the LLM provider fails; message names the provider."""

    def __init__(self, provider: str, detail: str):
        self.provider = provider
        super().__init__(f"{provider} LLM error: {detail}")


def llm_provider() -> str:
    """Name of the LLM provider that will be used for the next call."""
    return "nim" if NVIDIA_API_KEY else "llama"


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


def build_messages(agent: dict, user_text: str, context: str = "") -> list[dict]:
    """
    Build OpenAI-style chat messages from agent config + user text + RAG context.

    Used by the NVIDIA NIM (chat/completions) path. The system prompt carries
    the same language/identity policy as the ChatML prompt builder so both
    providers behave identically.
    """
    system_prompt = agent.get("system_prompt") or "You are a helpful AI call agent."
    preferred_language = (agent.get("language") or agent.get("voice_lang") or "").strip()
    language_policy = "Mirror the user's language. If the user speaks Hindi or Hinglish, reply in Hindi/Hinglish."
    if preferred_language:
        language_policy += f" Preferred response language: {preferred_language}."
    identity_policy = "Introduce yourself consistently based on your configured persona and avoid generic disclaimers."
    if context:
        system_prompt += "\n\nRelevant context:\n" + context

    return [
        {"role": "system", "content": f"{system_prompt}\n\n{language_policy}\n{identity_policy}"},
        {"role": "user", "content": user_text},
    ]


async def stream_nim(
    agent: dict,
    user_text: str,
    context: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream LLM fragments from NVIDIA NIM's OpenAI-compatible chat completions.

    Yields sentence-boundary fragments. Raises LLMProviderError on failure.
    """
    payload = {
        "model": agent.get("nim_model") or NIM_MODEL,
        "messages": build_messages(agent, user_text, context),
        "temperature": LLM_TEMPERATURE,
        "top_p": 0.9,
        "max_tokens": agent.get("llm_n_predict", LLM_N_PREDICT),
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "text/event-stream",
    }
    url = f"{NIM_BASE_URL}/chat/completions"

    pending = ""
    pending_count = 0

    async def _run(c: httpx.AsyncClient) -> AsyncGenerator[str, None]:
        nonlocal pending, pending_count
        async with c.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                logger.error("[LLM/nim] HTTP %s: %s", resp.status_code, body[:300])
                raise LLMProviderError(
                    "NVIDIA NIM", f"HTTP {resp.status_code}"
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                delta = (choices[0].get("delta") or {}) if choices else {}
                token = delta.get("content") or ""
                finish = choices[0].get("finish_reason") if choices else None
                pending += token
                pending_count += 1
                if _FLUSH_RE.search(token) or pending_count >= _MAX_PENDING:
                    fragment = pending.strip()
                    if fragment:
                        yield fragment
                    pending = ""
                    pending_count = 0
                if finish:
                    break
        if pending.strip():
            yield pending.strip()
            pending = ""

    try:
        if client is not None:
            async for frag in _run(client):
                yield frag
        else:
            async with httpx.AsyncClient(timeout=float(LLM_TIMEOUT_SEC)) as c:
                async for frag in _run(c):
                    yield frag
    except LLMProviderError:
        raise
    except Exception as exc:
        logger.error("[LLM/nim] stream error: %s", exc)
        raise LLMProviderError("NVIDIA NIM", str(exc)) from exc


async def stream_llamacpp(
    prompt: str,
    agent: dict,
    client: Optional[httpx.AsyncClient] = None,
) -> AsyncGenerator[str, None]:
    """
    Stream LLM tokens from local llama-server and yield sentence fragments.

    Raises LLMProviderError on failure.
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

    pending = ""
    pending_count = 0

    async def _run(c: httpx.AsyncClient) -> AsyncGenerator[str, None]:
        nonlocal pending, pending_count
        async with c.stream("POST", LLM_URL, json=payload) as resp:
            if resp.status_code != 200:
                logger.error(
                    "[LLM/llama] HTTP %s from %s", resp.status_code, LLM_URL
                )
                raise LLMProviderError(
                    "llama.cpp", f"HTTP {resp.status_code}"
                )
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
                    break
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
            pending = ""

    try:
        if client is not None:
            async for frag in _run(client):
                yield frag
        else:
            async with httpx.AsyncClient(timeout=float(LLM_TIMEOUT_SEC)) as c:
                async for frag in _run(c):
                    yield frag
    except LLMProviderError:
        raise
    except Exception as exc:
        logger.error("[LLM/llama] stream error: %s", exc)
        raise LLMProviderError("llama.cpp", str(exc)) from exc


async def stream_llm(
    prompt: str,
    agent: dict,
    client: Optional[httpx.AsyncClient] = None,
    *,
    user_text: str = "",
    context: str = "",
) -> AsyncGenerator[str, None]:
    """
    Provider-selected streaming.

    - NVIDIA NIM configured → stream from NIM; on failure fall back to the
      local llama.cpp server if one answers, else re-raise the NIM error.
    - NIM not configured → llama.cpp only.
    """
    if NVIDIA_API_KEY:
        try:
            async for frag in stream_nim(agent, user_text or prompt, context, client=client):
                yield frag
            return
        except LLMProviderError as nim_exc:
            # If the caller could not provide chat-shaped input (legacy
            # call-sites that pass a prebuilt prompt), still attempt fallback.
            logger.warning("[LLM] %s — trying llama.cpp fallback", nim_exc)
            try:
                async for frag in stream_llamacpp(prompt, agent, client=client):
                    yield frag
                return
            except LLMProviderError as llama_exc:
                logger.error("[LLM] fallback also failed: %s", llama_exc)
                raise nim_exc from llama_exc

    async for frag in stream_llamacpp(prompt, agent, client=client):
        yield frag
