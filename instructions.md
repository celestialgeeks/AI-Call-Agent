
Sahaiy AI Call Agent — Implementation Guide

Overview

This document defines the architecture and step-by-step plan to build a real-time AI voice calling system using:
	•	STT: whisper.cpp
	•	LLM: Phi-3 (llama.cpp)
	•	TTS: Piper
	•	Backend: FastAPI
	•	Transport: WebSocket → WebRTC

⸻

System Architecture
User Speech (Mic / Call)
    ↓
Audio Stream (WebRTC / WebSocket)
    ↓
Voice Activity Detection (VAD)
    ↓
Speech-to-Text (whisper.cpp)
    ↓
Backend (FastAPI)
    ↓
LLM (Phi-3 via llama-server)
    ↓
Text-to-Speech (Piper)
    ↓
Audio Stream Back
    ↓
User

Latency Targets
Component
Target
STT
150–250 ms
LLM
100–200 ms
TTS
100–150 ms
Total
500–700 ms

Core Principles

Do NOT
	•	Wait for full sentence completion
	•	Use blocking APIs
	•	Generate long responses

DO
	•	Stream audio chunks (20–50 ms)
	•	Stream LLM tokens
	•	Start TTS early
	•	Handle interruptions

⸻

Phase 1 — STT (Speech-to-Text)

Run whisper.cpp server
cd ~/whisper.cpp
./build/bin/whisper-server -m models/ggml-base.en.bin -p 8081

Backend STT call
def call_stt(audio_file):
    files = {"file": open(audio_file, "rb")}
    response = requests.post("http://localhost:8081/inference", files=files)
    return response.json()["text"]

Phase 2 — Microphone Input (Frontend)
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

const mediaRecorder = new MediaRecorder(stream);

mediaRecorder.ondataavailable = (e) => {
  ws.send(e.data);
};

mediaRecorder.start(50);

Phase 3 — Backend Audio Pipeline
@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket):
    await ws.accept()

    while True:
        audio_chunk = await ws.receive_bytes()

        with open("chunk.wav", "wb") as f:
            f.write(audio_chunk)

        text = call_stt("chunk.wav")
        ai_text = call_llm(build_prompt(text))

        speak(ai_text)

Phase 4 — TTS (Speech Output)

Install Piper
brew install piper

Backend TTS
def speak(text):
    subprocess.run(
        f'echo "{text}" | piper --model ~/piper_models/en_US-lessac-medium.onnx --output_file out.wav',
        shell=True
    )

Send audio back
with open("out.wav", "rb") as f:
    await ws.send_bytes(f.read())

Phase 5 — Frontend Audio Playback
ws.onmessage = async (event) => {
  const blob = new Blob([event.data], { type: "audio/wav" });
  const url = URL.createObjectURL(blob);

  const audio = new Audio(url);
  audio.play();
};

Phase 6 — Streaming Pipeline (Critical)
Audio Chunk → Partial STT → Partial LLM → Partial TTS → Playback

Tasks
	•	Use async queues
	•	Enable LLM streaming (llama-server)
	•	Generate TTS incrementally
	•	Stream audio continuously

⸻

Phase 7 — Interrupt Handling

Goal

Stop AI when user speaks

Tasks
	•	Detect speech (VAD)
	•	Stop TTS playback
	•	Clear queues
	•	Resume STT

⸻

Phase 8 — Agent System

Flow
agent_id → fetch config → build prompt → LLM

Backend
agent = get_agent(agent_id)
prompt = build_prompt(agent, user_text)

Phase 9 — Knowledge Base (RAG)

Flow
User Query → Vector Search → Context → LLM

Tasks
	•	Chunk documents
	•	Generate embeddings
	•	Store in FAISS
	•	Retrieve top-k context

⸻

Phase 10 — Call Infrastructure

Options

WebRTC (recommended)
	•	LiveKit
	•	simple-peer

SIP (advanced)
	•	Asterisk
	•	FreeSWITCH

Tasks
	•	Replace WebSocket audio with WebRTC
	•	Handle mic + speaker streams
	•	Manage call lifecycle

⸻

Performance Optimization
	•	Keep models loaded in RAM
	•	Use quantized GGUF models
	•	Reduce prompt size
	•	Avoid disk I/O (no temp files)
	•	Run all services on same machine

