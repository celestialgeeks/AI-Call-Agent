/**
 * src/services/demoCallService.js
 * ───────────────────────────────
 * Browser client for the simulated demo call (issue #6).
 *
 * Primary deterministic path (no mic needed):
 *   WS  /ws/audio?agent_id=<uuid>
 *   → send {"type":"text_input","message":"…"} turns
 *
 * Secondary mic path:
 *   declare {"type":"audio_meta","format":"pcm16","mime_type":"audio/pcm",
 *            "sample_rate":16000,"channels":1} FIRST,
 *   then stream raw 16 kHz mono PCM16 bytes.
 *
 * Server → client JSON frames: transcript | fragment | interrupted | error
 * Server → client binary frames: WAV audio ready for <audio>.
 *
 * Lifecycle REST (mounted at /agents, NOT /calls):
 *   POST /agents/{agent_id}/call/start → {conversation_id}
 *   POST /agents/{agent_id}/call/end
 */

import env from '@/config/env.js';

const DEMO_USER_NAMESPACE = 'demo-web-';

/** @returns {string} stable-per-tab pseudo user id for the anonymous demo */
function demoUserId() {
    let id = sessionStorage.getItem('sahaiy_demo_user');
    if (!id) {
        id = DEMO_USER_NAMESPACE +
            (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + '-anon');
        sessionStorage.setItem('sahaiy_demo_user', id);
    }
    return id;
}

/** Convert an http(s) base URL into a ws(s) URL. */
function backendWsBase() {
    return env.backendUrl.replace(/^http/i, 'ws');
}

export class DemoCallClient {
    /**
     * @param {object} handlers
     * @param {(text: string) => void} handlers.onUserTranscript   — echoed user turn
     * @param {(text: string) => void} handlers.onAgentFragment    — streamed agent text
     * @param {() => void}             handlers.onAgentSpeechStart — binary audio frame arrived
     * @param {() => void}             handlers.onPlaybackIdle     — audio queue drained
     * @param {() => void}             handlers.onInterrupted      — server confirmed interrupt
     * @param {(message: string) => void} handlers.onError        — server/connection error
     * @param {() => void}             handlers.onOpen             — socket ready
     * @param {() => void}             handlers.onClose            — socket closed (after open)
     */
    constructor(handlers) {
        this._h = handlers;
        this._ws = null;
        this._conversationId = null;
        this._startedAt = 0;
        this._closedByUs = false;
        this._everOpened = false;

        // Playback queue: sequential WAV blobs through one <audio> element.
        this._audioEl = typeof Audio !== 'undefined' ? new Audio() : null;
        /** @type {string[]} */ this._blobUrls = [];
        this._playing = false;

        // Mic state
        this._micStream = null;
        this._audioCtx = null;
        this._workletNode = null;
        this._sourceNode = null;
        this._micActive = false;
        this._micDeclared = false;
        this._pendingPcm = [];
    }

    /* ── Connection ─────────────────────────────────────────── */

    /**
     * Opens the WebSocket and starts lifecycle tracking.
     * @param {string} agentId — may be '' (server applies default agent config)
     */
    connect(agentId) {
        const qs = new URLSearchParams({ agent_id: agentId || '', user_id: demoUserId() });
        const url = `${backendWsBase()}/ws/audio?${qs.toString()}`;

        this._closedByUs = false;
        try {
            this._ws = new WebSocket(url);
        } catch (err) {
            this._h.onError('Could not open a connection to the demo service.');
            return;
        }
        this._ws.binaryType = 'arraybuffer';

        this._ws.onopen = () => {
            this._everOpened = true;
            this._startedAt = Date.now();
            this._startCallRecord().catch(() => {});
            this._h.onOpen();
        };
        this._ws.onmessage = (event) => this._onMessage(event);
        this._ws.onerror = () => {
            if (!this._everOpened) {
                this._h.onError(
                    'Demo backend unreachable right now. Please try again in a moment.'
                );
            }
        };
        this._ws.onclose = () => {
            this._teardownMic();
            this._endCallRecord().catch(() => {});
            if (!this._closedByUs) this._h.onClose();
        };
    }

    get isOpen() {
        return Boolean(this._ws && this._ws.readyState === WebSocket.OPEN);
    }

    /** @param {string} message */
    sendText(message) {
        if (!this.isOpen) {
            this._h.onError('Not connected — start the call again.');
            return;
        }
        this._sendJson({ type: 'text_input', message });
    }

    /** Ask the server to cancel LLM/TTS playback mid-stream. */
    sendInterrupt() {
        if (!this.isOpen) return;
        this._sendJson({ type: 'interrupt' });
        this._stopAudioNow();
    }

    /** Closes the socket and releases media resources. */
    close() {
        this._closedByUs = true;
        this.sendMicOff();
        this._stopAudioNow();
        if (this._ws) {
            try { this._ws.close(); } catch { /* already closed */ }
            this._ws = null;
        }
        this._revokeBlobUrls();
    }

    _sendJson(payload) {
        try { this._ws.send(JSON.stringify(payload)); } catch { /* socket gone */ }
    }

    /* ── Server frames ──────────────────────────────────────── */

    _onMessage(event) {
        if (event.data instanceof ArrayBuffer) {
            this._enqueueWav(event.data);
            return;
        }

        let frame;
        try { frame = JSON.parse(event.data); } catch { return; }

        switch (frame.type) {
            case 'transcript':
                this._h.onUserTranscript(String(frame.text ?? ''));
                break;
            case 'fragment':
                this._h.onAgentFragment(String(frame.text ?? ''));
                break;
            case 'interrupted':
                this._stopAudioNow();
                this._h.onInterrupted();
                break;
            case 'error':
                this._h.onError(String(frame.message || 'The agent hit an unexpected error.'));
                break;
            default:
                break; // ignore unknown control frames
        }
    }

    /* ── Audio playback queue ───────────────────────────────── */

    _enqueueWav(arrayBuffer) {
        if (!this._audioEl) return;
        const blobUrl = URL.createObjectURL(new Blob([arrayBuffer], { type: 'audio/wav' }));
        this._blobUrls.push(blobUrl);
        this._h.onAgentSpeechStart();
        if (!this._playing) this._playNext();
    }

    _playNext() {
        const next = this._blobUrls.shift();
        if (!next) {
            this._playing = false;
            this._h.onPlaybackIdle();
            return;
        }
        this._playing = true;
        const el = this._audioEl;
        el.src = next;
        el.onended = () => {
            URL.revokeObjectURL(next);
            this._playNext();
        };
        el.onerror = () => {
            URL.revokeObjectURL(next);
            this._playNext();
        };
        el.play().catch(() => this._playNext());
    }

    _stopAudioNow() {
        if (this._audioEl) {
            this._audioEl.onended = null;
            this._audioEl.pause();
            this._audioEl.removeAttribute('src');
        }
        this._revokeBlobUrls();
        if (this._playing) {
            this._playing = false;
            this._h.onPlaybackIdle();
        }
    }

    _revokeBlobUrls() {
        this._blobUrls.splice(0).forEach((url) => URL.revokeObjectURL(url));
    }

    /* ── Mic (secondary path) ───────────────────────────────── */

    async sendMicOn() {
        if (this.micActive || !this.isOpen) return false;
        try {
            this._micStream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
            });

            this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const workletCode = `
                class PcmTap extends AudioWorkletProcessor {
                    process(inputs) {
                        const channel = inputs[0] && inputs[0][0];
                        if (channel && channel.length) this.port.postMessage(channel.slice(0));
                        return true;
                    }
                }
                registerProcessor('pcm-tap', PcmTap);
            `;
            const workletUrl = URL.createObjectURL(
                new Blob([workletCode], { type: 'application/javascript' })
            );
            await this._audioCtx.audioWorklet.addModule(workletUrl);
            URL.revokeObjectURL(workletUrl);

            // Declare the stream shape BEFORE any audio bytes leave the client.
            this._sendJson({
                type: 'audio_meta',
                format: 'pcm16',
                mime_type: 'audio/pcm',
                sample_rate: 16000,
                channels: 1,
            });
            this._micDeclared = true;

            this._sourceNode = this._audioCtx.createMediaStreamSource(this._micStream);
            this._workletNode = new AudioWorkletNode(this._audioCtx, 'pcm-tap');
            this._workletNode.port.onmessage = (e) => this._queuePcm(e.data);
            this._sourceNode.connect(this._workletNode);

            this._micActive = true;
            return true;
        } catch (err) {
            this._teardownMic();
            this._h.onError('Microphone unavailable — you can still chat by text.');
            return false;
        }
    }

    sendMicOff() {
        if (!this._micActive) return;
        this._flushPendingPcm();
        this._teardownMic();
    }

    get micActive() {
        return this._micActive;
    }

    /** Resample a Float32 chunk to 16 kHz mono and buffer as PCM16. */
    _queuePcm(float32Chunk) {
        const targetRate = 16000;
        const sourceRate = this._audioCtx.sampleRate;
        let resampled = float32Chunk;

        if (Math.abs(sourceRate - targetRate) > 1) {
            const ratio = sourceRate / targetRate;
            const outLength = Math.max(1, Math.floor(float32Chunk.length / ratio));
            resampled = new Float32Array(outLength);
            for (let i = 0; i < outLength; i++) {
                const srcIndex = i * ratio;
                const low = Math.floor(srcIndex);
                const high = Math.min(low + 1, float32Chunk.length - 1);
                const weight = srcIndex - low;
                resampled[i] = float32Chunk[low] * (1 - weight) + float32Chunk[high] * weight;
            }
        }

        const pcm = new Int16Array(resampled.length);
        for (let i = 0; i < resampled.length; i++) {
            const clamped = Math.max(-1, Math.min(1, resampled[i]));
            pcm[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
        }
        this._pendingPcm.push(pcm);

        // Flush ~100 ms batches so frames stay small but frequent.
        if (this._pendingPcm.reduce((n, c) => n + c.length, 0) >= 1600) {
            this._flushPendingPcm();
        }
    }

    _flushPendingPcm() {
        if (!this.isOpen || !this._pendingPcm.length) return;
        const total = this._pendingPcm.reduce((n, c) => n + c.length, 0);
        const merged = new Int16Array(total);
        let offset = 0;
        this._pendingPcm.splice(0).forEach((chunk) => {
            merged.set(chunk, offset);
            offset += chunk.length;
        });
        try { this._ws.send(merged.buffer); } catch { /* socket gone */ }
    }

    _teardownMic() {
        this._micActive = false;
        this._micDeclared = false;
        if (this._workletNode) { try { this._workletNode.disconnect(); } catch { /* noop */ } }
        if (this._sourceNode) { try { this._sourceNode.disconnect(); } catch { /* noop */ } }
        this._workletNode = null;
        this._sourceNode = null;
        if (this._audioCtx) { this._audioCtx.close().catch(() => {}); this._audioCtx = null; }
        if (this._micStream) {
            this._micStream.getTracks().forEach((t) => t.stop());
            this._micStream = null;
        }
    }

    /* ── Call lifecycle REST (best-effort, never blocks the UX) ── */

    async _startCallRecord() {
        const agentId = this._agentId;
        if (!agentId) return; // default-agent demos have no row to reference
        const resp = await fetch(`${env.backendUrl}/agents/${encodeURIComponent(agentId)}/call/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: demoUserId(),
                caller_name: 'Landing-page demo visitor',
            }),
        });
        if (resp.ok) {
            const data = await resp.json();
            this._conversationId = data.conversation_id ?? null;
        }
    }

    async _endCallRecord() {
        if (!this._conversationId) return;
        const durationSec = Math.round((Date.now() - this._startedAt) / 1000);
        await fetch(`${env.backendUrl}/agents/${encodeURIComponent(this._agentId)}/call/end`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: demoUserId(),
                conversation_id: this._conversationId,
                duration_sec: durationSec,
                status: 'resolved',
            }),
        }).catch(() => {});
        this._conversationId = null;
    }

    /** Remember which agent this connection targets (used by lifecycle REST). */
    set agentId(id) {
        this._agentId = id || '';
    }

    /** Transcript lines collected this session, for the call/end record. */
    transcriptText = '';
}

export default DemoCallClient;
