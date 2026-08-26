#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
#  scripts/demo-local.sh — Sahaiy in-person demo runner (ADR-0002 Track B)
#
#  ONE command to start the full voice-demo stack on this Mac:
#      ./scripts/demo-local.sh
#  then open http://localhost:5173 (frontend) — backend API on :8000.
#
#  - Runs the SAME FastAPI serving code as production (sahaiy-backend/).
#  - Offline-friendly: fails gracefully when LLM/STT/Sarvam are absent,
#    /health always reports which pieces are up.
#  - Apple Silicon: set TTS/LLM device knobs via .env (MPS is picked up
#    by llama-server Metal builds automatically; CPU fallback works too).
#
#  Optional container mode (when Docker Desktop/OrbStack is installed):
#      ./scripts/demo-local.sh --docker   # uses ops/docker-compose.demo.yml
# ════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT/sahaiy-backend"
FRONTEND_DIR="$ROOT"           # Vite app lives at repo root
PORT_BACKEND="${DEMO_BACKEND_PORT:-8000}"
PORT_FRONTEND="${DEMO_FRONTEND_PORT:-5173}"

if [ "${1:-}" = "--docker" ]; then
  echo "▶ Container mode: docker compose -f ops/docker-compose.demo.yml up --build"
  exec docker compose -f "$ROOT/ops/docker-compose.demo.yml" up --build
fi

echo "── Sahaiy local demo ──────────────────────────────────────────"

# 1. Python venv for the backend ---------------------------------------------
VENV="$BACKEND_DIR/.venv-demo"
if [ ! -x "$VENV/bin/python" ]; then
  echo "① Creating backend venv ($VENV)…"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "② Installing/updating backend deps…"
pip install --quiet --upgrade pip
pip install --quiet -r "$BACKEND_DIR/requirements.txt"

# 2. Env file -----------------------------------------------------------------
if [ ! -f "$BACKEND_DIR/.env" ] && [ ! -f "$BACKEND_DIR/.env.local" ]; then
  echo "⚠  No $BACKEND_DIR/.env found. Copying from .env.example."
  echo "   Fill SARVAM_API_KEY (+ Supabase) there for full voice output;"
  echo "   the server still boots and /health reports what's missing."
  cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
fi

# 3. Start backend ------------------------------------------------------------
echo "③ Starting backend → http://localhost:$PORT_BACKEND  (Ctrl+C stops everything)"
cleanup() {
  echo ""
  echo "◼ Shutting down…"
  [ -n "${BACKEND_PID:-}" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

( cd "$BACKEND_DIR" && uvicorn server:app --host 127.0.0.1 --port "$PORT_BACKEND" ) &
BACKEND_PID=$!

# 4. Wait for backend health --------------------------------------------------
echo "④ Waiting for /health…"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT_BACKEND/health" >/dev/null 2>&1; then
    echo "   ✓ backend healthy: http://127.0.0.1:$PORT_BACKEND/health"
    break
  fi
  sleep 1
done

# 5. Frontend dev server (serves index.html + dashboard on localhost) --------
echo "⑤ Starting frontend → http://localhost:$PORT_FRONTEND"
npm install --prefix "$FRONTEND_DIR" --silent || echo "⚠ npm install failed — open $BACKEND_URL docs manually"
( cd "$FRONTEND_DIR" && VITE_BACKEND_URL="http://localhost:$PORT_BACKEND" npx vite --port "$PORT_FRONTEND" --strictPort ) &
FRONTEND_PID=$!

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Demo ready:"
echo "    • Frontend : http://localhost:$PORT_FRONTEND"
echo "    • Backend  : http://localhost:$PORT_BACKEND  (/health, /docs)"
echo "  Works offline except Sarvam TTS calls (needs internet)."
echo "═══════════════════════════════════════════════════════════════"

wait
