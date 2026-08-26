"""sahaiy-tts: FastAPI TTS demo Space — Kokoro FP16 ONNX, sentence-chunked streaming WAV.

ADR-0002 Track A. Free HF Spaces CPU Basic (2 vCPU).
Pipeline (verified locally, RTF ~0.3 on M-class, target <0.8 on 2 vCPU):
  text -> espeak-ng phonemes -> tokenizer.json vocab ids -> Kokoro FP16 ONNX -> 24 kHz PCM

Piper fallback stays behind TTS_ENGINE=piper if 2-vCPU RTF measures > ~0.8x.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import wave
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("sahaiy-tts")

SAMPLE_RATE = 24000
ENGINE = os.environ.get("TTS_ENGINE", "kokoro").lower()
MAX_CHARS = 1200
MODEL_REPO = "onnx-community/Kokoro-82M-v1.0-ONNX"
VOICES = ("af_heart", "af_bella", "am_michael")
DEFAULT_VOICE = "af_heart"

app = FastAPI(title="sahaiy-tts", version="1.1.0")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class KokoroEngine:
    def __init__(self) -> None:
        self.ready = False
        self.session: Optional[ort.InferenceSession] = None
        self.vocab: dict[str, int] = {}
        self.voices: dict[str, np.ndarray] = {}
        self._phonemizer = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if self.ready:
            return
        with self._lock:
            if self.ready:
                return
            t0 = time.time()
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = int(os.environ.get("ORT_THREADS", "2"))
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            model_path = hf_hub_download(MODEL_REPO, "onnx/model_fp16.onnx")
            self.session = ort.InferenceSession(
                model_path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            tok = json.loads(Path(hf_hub_download(MODEL_REPO, "tokenizer.json")).read_text())
            self.vocab = tok["model"]["vocab"]
            for v in VOICES:
                p = hf_hub_download(MODEL_REPO, f"voices/{v}.bin")
                # [510, 1, 256]; row i is the style vector for chunk length bucket i
                arr = np.fromfile(p, dtype=np.float32).reshape(510, 1, 256)
                self.voices[v] = arr[:, 0, :]
            from phonemizer.backend import EspeakBackend

            self._phonemizer = EspeakBackend("en-us", language_switch="remove-flags")
            self.ready = True
            log.info("kokoro fp16 loaded in %.2fs", time.time() - t0)

    def synthesize(self, text: str, voice: str) -> np.ndarray:
        style_row = min(len(text) // 30 + 1, 509)  # longer text -> later style rows
        style = self.voices.get(voice, self.voices[DEFAULT_VOICE])[style_row]
        phonemes = self._phonemizer.phonemize([text])[0].strip()
        tokens = ["$"] + [c for c in phonemes if c in self.vocab and c not in " ;:"] + ["$"]
        if len(tokens) < 3:
            return np.zeros(SAMPLE_RATE // 10, dtype=np.float32)
        ids = np.array([self.vocab[c] for c in tokens], dtype=np.int64)
        result = self.session.run(
            None,
            {
                "input_ids": ids[None, :],
                "style": style[None, :],
                "speed": np.array([1.0], dtype=np.float32),
            },
        )
        return np.clip(np.squeeze(result[0]).astype(np.float32), -1.0, 1.0)


_engine = KokoroEngine()


def get_engine() -> KokoroEngine:
    if ENGINE != "kokoro":
        raise HTTPException(status_code=503, detail=f"engine {ENGINE!r} not configured; use TTS_ENGINE=kokoro")
    try:
        _engine.load()
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("engine load failed")
        raise HTTPException(status_code=503, detail=f"model unavailable: {exc}")
    return _engine


def safe_warm() -> None:
    try:
        get_engine()
    except Exception as exc:
        log.warning("warmup failed: %s", exc)


@app.on_event("startup")
def warm() -> None:
    threading.Thread(target=safe_warm, daemon=True).start()


# ---------------------------------------------------------------------------
# Text splitting + streaming WAV
# ---------------------------------------------------------------------------

def split_sentences(text: str) -> list[str]:
    import re

    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = [p.strip() for p in parts if p.strip()]
    if not out and text.strip():
        out = [text.strip()]
    # merge very short fragments so we don't emit sub-100ms chunks
    merged: list[str] = []
    for s in out:
        if merged and len(s) < 25:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged


def wav_header(num_samples: int) -> bytes:
    buf = io_bytes = __import__("io").BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * num_samples)
    return buf.getvalue()[:44]


class StreamingWav:
    """Yields a WAV header plus PCM per sentence as soon as each is synthesized.

    RIFF size fields are patched on the fully-buffered copy for seekable clients;
    streaming players already received the bytes.
    """

    def __init__(self, text: str, voice: str) -> None:
        self.text = text[:MAX_CHARS]
        self.voice = voice
        self.total_samples = 0
        self.header_sent = False
        self.first_audio_s: Optional[float] = None

    def chunks(self) -> Iterator[bytes]:
        eng = get_engine()
        sentences = split_sentences(self.text)
        t_request = time.time()
        for i, sent in enumerate(sentences):
            t0 = time.time()
            audio = eng.synthesize(sent, self.voice)
            dt = time.time() - t0
            pcm = (audio * 32767.0).astype("<i2").tobytes()
            self.total_samples += len(pcm) // 2
            rtf_note = ""
            dur = len(audio) / SAMPLE_RATE
            if dur > 0:
                rtf_note = f" rtf={dt / dur:.2f}"
            log.info(
                "chunk %d/%d: %.2fs synth for %.2fs audio%s (%d chars)",
                i + 1, len(sentences), dt, dur, rtf_note, len(sent),
            )
            if not self.header_sent:
                self.header_sent = True
                self.first_audio_s = time.time() - t_request
                log.info("first audio after %.2fs", self.first_audio_s)
                yield wav_header(self.total_samples) + pcm
            else:
                yield pcm

    def response(self) -> StreamingResponse:
        collected = bytearray()

        def gen() -> Iterator[bytes]:
            import struct

            for piece in self.chunks():
                collected.extend(piece)
                yield piece
            data_len = max(len(collected) - 44, 0)
            struct.pack_into("<I", collected, 4, data_len + 36)
            struct.pack_into("<I", collected, 40, data_len)

        return StreamingResponse(gen(), media_type="audio/wav", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_CHARS)
    voice: Optional[str] = DEFAULT_VOICE


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "model": "kokoro-fp16-onnx", "ready": bool(_engine.ready)})


@app.post("/tts")
def tts(req: TTSRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    voice = req.voice if req.voice in _engine.voices or req.voice == DEFAULT_VOICE else DEFAULT_VOICE
    return StreamingWav(text, voice).response()


DEMO_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Sahaiy AI Call Agent — TTS demo</title></head>
<body style="font-family:-apple-system,sans-serif;max-width:640px;margin:4rem auto;padding:0 1rem">
<h1>Sahaiy AI Call Agent</h1>
<p>Type something and hear the agent speak.</p>
<textarea id="t" rows="4" style="width:100%">Hello! This is the Sahaiy AI call agent speaking. I can answer questions about your business around the clock.</textarea><br><br>
<button onclick="go()" id="b">Speak</button>
<audio id="a" controls style="display:block;margin-top:1rem;width:100%"></audio>
<script>
async function go(){
  const b=document.getElementById('b'); b.disabled=true; b.textContent='Synthesizing...';
  try{
    const r=await fetch('/tts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:document.getElementById('t').value})});
    const bl=await r.blob();
    document.getElementById('a').src=URL.createObjectURL(bl);
  } finally { b.disabled=false; b.textContent='Speak'; }
}
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return DEMO_HTML
