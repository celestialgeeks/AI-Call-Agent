# sahaiy-tts — Public TTS demo Space

FastAPI + Kokoro-82M (FP16 ONNX) serving sentence-chunked streaming WAV.
Runs free on Hugging Face Spaces (CPU Basic, Docker SDK). ADR-0002 Track A.

## Public URL

https://huggingface.co/spaces/celestialgeeks/sahaiy-tts
→ live endpoint: https://celestialgeeks-sahaiy-tts.hf.space

## API

- `GET /health` → `{"status":"ok","model":"kokoro-fp16-onnx"}`
- `POST /tts` body `{"text": "...", "voice": "af_heart"}` → `audio/wav` streamed,
  first audio bytes sent as soon as the first sentence is synthesized.
- `GET /` → minimal HTML demo page with a play button.

## Local run

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 7860
```

## Engine switch

`TTS_ENGINE=kokoro` (default) or `TTS_ENGINE=piper`.
