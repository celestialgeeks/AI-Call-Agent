#!/usr/bin/env zsh
# ╔══════════════════════════════════════════════════════════════╗
# ║         SAHAIY — Backend Services Launcher                  ║
# ║  Starts: STT (whisper) · LLM (llama) · FastAPI              ║
# ║  Frontend is deployed on Vercel — not started here.         ║
# ║                                                              ║
# ║  Usage:  ./start.sh          → start all backend services   ║
# ║          ./start.sh --stop   → stop all backend services    ║
# ╚══════════════════════════════════════════════════════════════╝

# ── Paths ─────────────────────────────────────────────────────────────
BACKEND_DIR="$(cd "$(dirname "$0")/sahaiy-backend" && pwd)"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python3"

WHISPER_BIN="/Users/shreyashsingh/whisper.cpp/build/bin/whisper-server"
WHISPER_MODEL="/Users/shreyashsingh/whisper.cpp/models/ggml-base.en.bin"

LLAMA_BIN="/Users/shreyashsingh/llama.cpp/build/bin/llama-server"
LLAMA_MODEL="/Users/shreyashsingh/llama.cpp/models/phi3.gguf"

LOG_DIR="$BACKEND_DIR/logs"

# ── Colors ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓  $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠  $*${NC}"; }
err()  { echo -e "${RED}  ✗  $*${NC}"; exit 1; }
log()  { echo -e "${BOLD}${CYAN}[sahaiy]${NC} $*"; }

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN &>/dev/null; }

wait_for_port() {
  local port=$1 name=$2 elapsed=0
  echo -n "      Waiting for $name on :$port "
  while ! port_in_use "$port"; do
    sleep 1; ((elapsed++)); echo -n "."
    [[ $elapsed -ge 40 ]] && { echo ""; err "$name did not start (40s timeout). Check: $LOG_DIR/"; }
  done
  echo ""; ok "$name → http://localhost:$port"
}

# ── Stop ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
  echo ""
  log "Stopping backend services..."
  pkill -f "whisper-server" 2>/dev/null && ok "Stopped STT (whisper-server)"   || warn "whisper-server not running"
  pkill -f "llama-server"   2>/dev/null && ok "Stopped LLM (llama-server)"     || warn "llama-server not running"
  pkill -f "server:app"     2>/dev/null && ok "Stopped FastAPI backend"         || warn "FastAPI backend not running"
  echo ""; log "Done."; echo ""; exit 0
fi

# ── Pre-flight checks ─────────────────────────────────────────────────
[[ ! -f "$WHISPER_BIN"  ]] && err "whisper-server not found: $WHISPER_BIN\n  Build: cd ~/whisper.cpp && cmake -B build && cmake --build build -j"
[[ ! -f "$WHISPER_MODEL"]] && err "Whisper model not found: $WHISPER_MODEL"
[[ ! -f "$LLAMA_BIN"    ]] && err "llama-server not found: $LLAMA_BIN\n  Build: cd ~/llama.cpp && cmake -B build -DLLAMA_METAL=ON && cmake --build build -j llama-server"
[[ ! -f "$LLAMA_MODEL"  ]] && err "LLM model not found: $LLAMA_MODEL"
[[ ! -f "$VENV_PYTHON"  ]] && err "Python venv missing.\n  Run: cd $BACKEND_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

mkdir -p "$LOG_DIR"

# ── Banner ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}  ┌─────────────────────────────────────────┐"
echo   "  │    Sahaiy AI Call Agent — Backend       │"
echo   "  │    STT · LLM · FastAPI                  │"
echo -e "  └─────────────────────────────────────────┘${NC}"
echo ""

# ── 1. STT — whisper.cpp ───────────────────────────────────────────────
log "1/3  Starting STT server (whisper.cpp)..."

if port_in_use 8081; then
  warn "Port 8081 busy — restarting whisper-server"
  pkill -f "whisper-server" 2>/dev/null; sleep 1
fi

nohup "$WHISPER_BIN" \
  -m "$WHISPER_MODEL" \
  --port 8081 \
  --no-gpu \
  > "$LOG_DIR/whisper.log" 2>&1 &

wait_for_port 8081 "Whisper STT"

# ── 2. LLM — llama.cpp ────────────────────────────────────────────────
log "2/3  Starting LLM server (llama.cpp · Phi-3)..."

if port_in_use 8080; then
  warn "Port 8080 busy — restarting llama-server"
  pkill -f "llama-server" 2>/dev/null; sleep 1
fi

nohup "$LLAMA_BIN" \
  -m "$LLAMA_MODEL" \
  --port 8080 \
  -ngl 100 \
  --ctx-size 4096 \
  > "$LOG_DIR/llama.log" 2>&1 &

wait_for_port 8080 "llama-server LLM"

# ── 3. FastAPI Backend ─────────────────────────────────────────────────
log "3/3  Starting FastAPI backend (port 8000)..."

if port_in_use 8000; then
  warn "Port 8000 busy — restarting backend"
  pkill -f "server:app" 2>/dev/null; sleep 1
fi

nohup "$VENV_PYTHON" -m uvicorn server:app \
  --host 0.0.0.0 \
  --port 8000 \
  > "$LOG_DIR/backend.log" 2>&1 &

wait_for_port 8000 "FastAPI backend"

# ── Done ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}  ✅  All 3 backend services running!${NC}"
echo ""
echo -e "  🎤  STT      → ${CYAN}http://localhost:8081${NC}"
echo -e "  🧠  LLM      → ${CYAN}http://localhost:8080${NC}"
echo -e "  🚀  API      → ${CYAN}http://localhost:8000/docs${NC}"
echo ""
echo -e "  📋  Logs     →  $LOG_DIR/"
echo -e "  🛑  Stop     →  ${BOLD}./start.sh --stop${NC}"
echo ""
