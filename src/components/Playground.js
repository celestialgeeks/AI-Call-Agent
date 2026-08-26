/**
 * src/components/Playground.js
 * ─────────────────────────────
 * Playground panel — Real-time AI voice tester.
 * Opens on Preview → connects WebSocket → speaks first_message immediately.
 */

import { $ } from '@/utils/dom.js';
import { showToast } from '@/utils/toast.js';
import env from '@/config/env.js';

let _isConnected  = false;
let _ws           = null;
let _mediaRecorder = null;
let _audioStream  = null;
let _audioContext = null;
let _audioSourceNode = null;
let _audioProcessorNode = null;
let _audioQueue   = [];      // Sequential audio playback queue
let _isPlaying    = false;   // Is audio currently playing?
let _agentName    = 'Agent'; // Display name shown in header

const resolveWsBase = () => {
    try {
        const backendUrl = new URL(env.backendUrl);
        backendUrl.protocol = backendUrl.protocol === 'https:' ? 'wss:' : 'ws:';
        return backendUrl.origin;
    } catch {
        return window.location.protocol === 'https:'
            ? `wss://${window.location.host}`
            : 'ws://localhost:8000';
    }
};

const WS_BASE = resolveWsBase();

// ─────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────

/**
 * Opens the playground for a specific agent and auto-starts the call.
 * @param {string} agentId   – UUID of the agent to test
 * @param {string} agentName – Display name for the header
 */
export function openPlayground(agentId, agentName) {
    // Allow calling with no args when agent is already set globally
    if (agentId) window.__CURRENT_AGENT_ID__ = agentId;
    _agentName = agentName || 'Agent';

    const panel = $('#playground-panel');
    panel?.classList.add('open');

    // Update header title
    const title = $('#playground-title');
    if (title) title.textContent = `🎮 ${_agentName}`;

    // Clear previous chat (keep only a loading placeholder)
    _clearChat();
    _appendTypingIndicator();

    // Auto-start call
    setTimeout(_startCall, 150); // small delay for panel slide-in animation
}

/** Closes the playground and cleans up. */
export function closePlayground() {
    $('#playground-panel')?.classList.remove('open');
    _stopCall(false); // don't show "Call ended" bubble when user manually closes
}

/** Toggles call state when the orb is clicked while already open. */
export async function togglePlaygroundCall() {
    if (_isConnected) {
        _stopCall(true);
    } else {
        _clearChat();
        await _startCall();
    }
}

/** Sends a typed message over the WebSocket. */
export function sendPlaygroundMessage() {
    const input = $('#playground-msg-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';

    _appendChatBubble(text, 'user');

    if (_isConnected && _ws?.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({ type: 'text_input', message: text }));
    } else {
        _appendChatBubble('Not connected — click the orb to reconnect.', 'agent');
    }
}

// ─────────────────────────────────────────────────────────────────────
// Internal Logic
// ─────────────────────────────────────────────────────────────────────

async function _startCall() {
    if (_isConnected) return; // guard against double-start

    const agentId = window.__CURRENT_AGENT_ID__ || '';
    const userId  = window.__USER_ID__  || '';

    if (!agentId) {
        _removeTypingIndicator();
        _appendChatBubble('No agent selected. Go to Agents → Edit an agent first.', 'agent');
        return;
    }

    _setOrbState('connecting');

    // 1. Open WebSocket FIRST — mic is requested only after the socket is
    //    live, so a denied mic can never masquerade as "backend unreachable".
    _ws = new WebSocket(`${WS_BASE}/ws/audio?agent_id=${agentId}&user_id=${userId}`);
    _ws.binaryType = 'arraybuffer';

    _ws.onopen = async () => {
        _isConnected = true;
        _setOrbState('connected');

        _sendAgentMeta();

        try {
            await _startMic();
            _startPcmStreaming();
        } catch (err) {
            // getUserMedia rejected → mic problem, NOT a connection problem.
            console.warn('[Playground] mic unavailable:', err?.name || err);
            _appendChatBubble('❌ Microphone access denied. Text chat below still works — allow the microphone in your browser and click the orb to retry.', 'agent');
        }
    };

    _ws.onmessage = async (event) => {
        if (typeof event.data === 'string') {
            // JSON control frame
            const data = _jsonSafe(event.data);
            if (data.type === 'fragment') {
                _removeTypingIndicator();
                _appendChatBubble(data.text, 'agent');
            } else if (data.type === 'transcript') {
                _appendChatBubble(data.text, 'user');
                _appendTypingIndicator(); // agent is "thinking"
            } else if (data.type === 'interrupted') {
                _removeTypingIndicator();
                _audioQueue = []; // discard queued audio
            } else if (data.type === 'error') {
                _removeTypingIndicator();
                _appendChatBubble(_errorCopy(data), 'agent');
                if (data.code === 'backend_unreachable' || data.code === 'stt_failed') {
                    showToast('❌ ' + (data.message || 'Voice service problem.'), 'error');
                }
            }
        } else {
            // Binary frame: WAV audio — queue for sequential playback
            _removeTypingIndicator();
            const blob = new Blob([event.data], { type: 'audio/wav' });
            _enqueueAudio(blob);
        }
    };

    _ws.onerror = () => {
        console.error('[Playground WS error]');
        _removeTypingIndicator();
        if (!_isConnected) {
            // Socket never opened → genuine connectivity failure.
            const backend = (() => { try { return new URL(env.backendUrl).origin; } catch { return env.backendUrl; } })();
            _appendChatBubble(`⚠️ Backend unreachable at ${backend}. Check your connection or try again later.`, 'agent');
        } else {
            _appendChatBubble('⚠️ Connection problem — the call was interrupted.', 'agent');
        }
        _setOrbState('idle');
    };

    _ws.onclose = (event) => {
        // Only handle if we're still marked as connected (avoid double cleanup)
        if (_isConnected) {
            if (event && event.code === 4001) {
                _appendChatBubble('⚠️ Session expired — sign in again to start a call.', 'agent');
            }
            _stopCall(true);
        }
    };
}

/** Request microphone access. Throws when the browser denies it. */
async function _startMic() {
    _audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
}

function _stopCall(showEndBubble = true) {
    const wasConnected = _isConnected;
    _isConnected = false;

    _stopPcmStreaming();
    _mediaRecorder?.state !== 'inactive' && _mediaRecorder?.stop();
    _mediaRecorder = null;
    _audioStream?.getTracks().forEach((t) => t.stop());
    _audioStream = null;

    if (_ws && _ws.readyState < 2) { // CONNECTING or OPEN
        _ws.onclose = null; // prevent re-entrant call
        _ws.close();
    }
    _ws = null;
    _audioQueue = [];

    _setOrbState('idle');

    if (showEndBubble && wasConnected) {
        _appendChatBubble('Call ended.', 'status');
    }
}

// ─── Audio Queue (sequential playback, no overlap) ───────────────────

function _enqueueAudio(blob) {
    _audioQueue.push(blob);
    if (!_isPlaying) _playNext();
}

function _playNext() {
    if (!_audioQueue.length) { _isPlaying = false; return; }
    _isPlaying = true;
    const blob = _audioQueue.shift();
    const url  = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => {
        URL.revokeObjectURL(url);
        _playNext();
    };
    audio.onerror = () => {
        URL.revokeObjectURL(url);
        _playNext();
    };
    audio.play().catch(() => _playNext());
}

// ─── Orb State ────────────────────────────────────────────────────────

function _setOrbState(state) {
    const orb    = $('#playground-orb-btn');
    const status = $('#playground-call-status');

    const config = {
        idle: {
            icon: '🎙️',
            label: 'CLICK ORB TO START A TEST CALL',
            shadow: '0 0 0 12px rgba(124,92,252,0.08), 0 8px 24px rgba(124,92,252,0.25)',
            color: 'var(--dash-text-3)',
        },
        connecting: {
            icon: '⏳',
            label: 'CONNECTING…',
            shadow: '0 0 0 14px rgba(251,191,36,0.12), 0 8px 24px rgba(251,191,36,0.3)',
            color: '#f59e0b',
        },
        connected: {
            icon: '⏹',
            label: '● LIVE — SPEAK NOW',
            shadow: '0 0 0 14px rgba(34,197,94,0.12), 0 8px 32px rgba(34,197,94,0.35)',
            color: '#22c55e',
        },
    };

    const cfg = config[state] ?? config.idle;
    if (orb)    { orb.textContent = cfg.icon; orb.style.boxShadow = cfg.shadow; }
    if (status) { status.textContent = cfg.label; status.style.color = cfg.color; }
}

// ─── Chat Helpers ─────────────────────────────────────────────────────

function _clearChat() {
    const chat = $('#playground-chat');
    if (chat) chat.innerHTML = '';
}

function _appendChatBubble(text, role) {
    const chat = $('#playground-chat');
    if (!chat) return;
    _removeTypingIndicator(); // remove spinner before appending real content

    const bubble = document.createElement('div');
    bubble.className = role === 'status'
        ? 'chat-status'
        : `chat-bubble ${role}`;
    bubble.textContent = text;
    chat.appendChild(bubble);
    chat.scrollTop = chat.scrollHeight;
}

function _appendTypingIndicator() {
    const chat = $('#playground-chat');
    if (!chat || $('#pg-typing')) return;
    const el = document.createElement('div');
    el.id = 'pg-typing';
    el.className = 'chat-bubble agent chat-typing';
    el.innerHTML = '<span></span><span></span><span></span>';
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
}

function _removeTypingIndicator() {
    $('#pg-typing')?.remove();
}

function _jsonSafe(str) {
    try { return JSON.parse(str); } catch { return {}; }
}

/**
 * Human copy for server error frames — names the actual failing component
 * instead of a generic "connection error" (issue #30).
 */
function _errorCopy(data) {
    switch (data.code) {
        case 'backend_unreachable':
            return '⚠️ Voice backend is unreachable right now. Please try again shortly.';
        case 'mic_denied':
            return '❌ Microphone access denied. Allow the microphone in your browser to speak.';
        case 'stt_failed':
            return `⚠️ Speech recognition failed (${data.message || 'service error'}). Text chat still works.`;
        case 'llm_failed':
            return `⚠️ The AI model failed to respond (${data.message || 'LLM error'}). Please try again.`;
        case 'tts_failed':
            return `⚠️ Voice playback failed (${data.message || 'TTS error'}). Replies will still appear as text.`;
        default:
            return `⚠️ ${data.message || data.detail || 'Something went wrong.'}`;
    }
}

function _sendAgentMeta() {
    if (_ws?.readyState !== WebSocket.OPEN) return;

    const profile = window.__CURRENT_AGENT_PROFILE__ || {};
    const agentMeta = {
        id: window.__CURRENT_AGENT_ID__ || profile.id || '',
        name: window.__CURRENT_AGENT_NAME__ || profile.name || '',
        system_prompt: profile.system_prompt || '',
        first_message: profile.first_message || '',
        voice_name: profile.voice_name || '',
        language: profile.language || profile.lang || '',
        voice_lang: profile.voice_lang || '',
    };

    _ws.send(JSON.stringify({ type: 'agent_meta', agent: agentMeta }));
}

function _startPcmStreaming() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) {
        _appendChatBubble('AudioContext is not available in this browser.', 'agent');
        return;
    }

    _audioContext = new AudioCtx();
    _audioSourceNode = _audioContext.createMediaStreamSource(_audioStream);

    const bufferSize = 4096;
    _audioProcessorNode = _audioContext.createScriptProcessor(bufferSize, 1, 1);

    if (_ws?.readyState === WebSocket.OPEN) {
        _ws.send(JSON.stringify({
            type: 'audio_meta',
            format: 'pcm16',
            sample_rate: _audioContext.sampleRate,
            channels: 1,
        }));
    }

    _audioProcessorNode.onaudioprocess = (event) => {
        if (_ws?.readyState !== WebSocket.OPEN) return;
        const input = event.inputBuffer.getChannelData(0);
        const pcmBytes = _floatTo16BitPcm(input);
        if (pcmBytes.byteLength > 0) {
            _ws.send(pcmBytes);
        }
    };

    _audioSourceNode.connect(_audioProcessorNode);
    _audioProcessorNode.connect(_audioContext.destination);
}

function _stopPcmStreaming() {
    if (_audioProcessorNode) {
        _audioProcessorNode.disconnect();
        _audioProcessorNode.onaudioprocess = null;
        _audioProcessorNode = null;
    }
    if (_audioSourceNode) {
        _audioSourceNode.disconnect();
        _audioSourceNode = null;
    }
    if (_audioContext) {
        _audioContext.close().catch(() => {});
        _audioContext = null;
    }
}

function _floatTo16BitPcm(float32Array) {
    const pcm = new Int16Array(float32Array.length);
    for (let index = 0; index < float32Array.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, float32Array[index]));
        pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return pcm.buffer;
}
