# ops/README.md — Sahaiy demo hosting operations (ADR-0002)

Two-track demo hosting per `adr/0002-tts-demo-hosting-hf-spaces-and-local-mac.md`:

## Track A — Public TTS Space (HF Spaces, free CPU Basic)
- The TTS demo runs as a Hugging Face Space (`*.hf.space`). It sleeps after 48 h idle.
- Keep-alive: `.github/workflows/tts-space-keepalive.yml` pings `<space-url>/health`
  daily at 04:07 UTC with cold-start-aware retries (5×30 s).
- One-time setup (owner: Shreyash):
  ```
  gh secret set TTS_SPACE_URL -R celestialgeeks/AI-Call-Agent
  # paste the Space URL, e.g. https://celestialgeeks-sahaiy-tts.hf.space
  ```
- Verify: Actions → "TTS Space Keep-Alive" → run manually via workflow_dispatch.

## Track B — In-person demo on the MacBook Air M4 (offline-capable)
One command from the repo root:

```
./scripts/demo-local.sh
```

What it does:
1. Creates/uses `sahaiy-backend/.venv-demo` and installs pinned backend deps.
2. Copies `.env.example` → `.env` on first run if missing (fill `SARVAM_API_KEY`,
   optionally Supabase, for full voice output; server boots without them).
3. Starts the FastAPI backend at http://localhost:8000 (`/health`, `/docs`).
4. Waits for `/health` to return 200.
5. Starts the Vite frontend at http://localhost:5173 with `VITE_BACKEND_URL`
   pointing at the local backend.

Open **http://localhost:5173** — that's the demo. Ctrl+C stops everything.

Container mode (once Docker Desktop/OrbStack is installed):

```
./scripts/demo-local.sh --docker    # uses ops/docker-compose.demo.yml
```

### Offline behaviour
- STT/LLM run locally when whisper.cpp / llama-server are up on :8081/:8080;
  `/health` reports each component so a dead piece is visible, not silent.
- Only Sarvam TTS needs internet. For a fully offline voice demo, run the
  Kokoro/Piper local model per ADR-0002 (backend swap is behind `tts.py`).

## Runbook notes
- Backend logs stream in the same terminal as the runner script.
- Port conflicts: override with `DEMO_BACKEND_PORT=8001 DEMO_FRONTEND_PORT=5174`.
- Never commit `.env` — secrets stay local or in GitHub Actions secrets.
