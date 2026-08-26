/**
 * src/components/VoicePreview.js
 * ──────────────────────────────
 * Voice preview playback over the existing /ws/audio pipeline.
 *
 * How it works (verified against backend contract, api-contracts v1 §1.4):
 * 1. Open WS `/ws/audio?agent_id=&user_id=<uuid>`
 * 2. Send `{type:"agent_meta", agent:{ first_message: <sample line>,
 *    voice_name: <speaker>, language }}` — backend merges this over defaults.
 * 3. Send `{type:"audio_meta", format:"pcm16", mime_type:"audio/pcm",
 *    sample_rate:16000, channels:1}` — triggers the greeting path
 *    (`_send_greeting_once`) which TTS's `first_message` with our voice
 *    and returns WAV binary frames.
 * 4. Play WAV frames; stop = close socket.
 *
 * No new backend endpoint. One preview at a time (module-level singleton).
 */

import env from '@/config/env.js';

let _current = null; // { ws, audio, url, voiceSlug, onEnd }

const SAMPLE_LINE = 'Namaste! This is a preview of my voice.';

const resolveWsBase = () => {
    try {
        const u = new URL(env.backendUrl);
        u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
        return u.origin;
    } catch {
        // Fail loudly rather than silently connecting to a guessed host
        // (code-reviewer minor #3, PR #35).
        console.error('[VoicePreview] VITE_BACKEND_URL is not configured — cannot resolve preview WebSocket origin.');
        throw new Error('Preview unavailable: backend URL is not configured.');
    }
};

function _teardown() {
    if (!_current) return;
    const { ws, audio, url } = _current;
    if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.onclose = null;
        try { ws.close(); } catch { /* already closing */ }
    }
    if (audio) { audio.pause(); }
    if (url) URL.revokeObjectURL(url);
    _current = null;
}

/**
 * Stops whatever preview is currently playing (no-op if none).
 */
export function stopPreview() {
    const ended = _current;
    _teardown();
    if (ended?.onEnd) ended.onEnd(ended.voiceSlug);
}

/**
 * @returns {string|null} slug of the currently playing voice
 */
export function playingVoice() {
    return _current?.voiceSlug ?? null;
}

/**
 * Starts a one-at-a-time voice preview. Starting a new preview stops the old one.
 *
 * @param {object} opts
 * @param {string} opts.slug       - Sarvam speaker id (e.g. "anushka")
 * @param {string} [opts.language] - language label for TTS lang resolution
 * @param {Function} [opts.onStart] - (slug) => void
 * @param {Function} [opts.onEnd]   - (slug) => void — fired on natural end OR stop
 * @param {Function} [opts.onError] - (slug, message) => void
 */
export function playPreview({ slug, language, onStart, onEnd, onError }) {
    // Rule: only ONE preview at a time — starting another stops the current.
    stopPreview();

    const handle = { ws: null, audio: null, url: null, voiceSlug: slug, onEnd };
    _current = handle;

    onStart?.(slug);

    let greeted = false;

    const finish = () => {
        // Natural end of all audio or socket closure.
        if (_current !== handle) return; // superseded by a newer preview
        _teardown();
        onEnd?.(slug);
    };

    try {
        const userId = window.__USER_ID__ || '';
        const ws = new WebSocket(`${resolveWsBase()}/ws/audio?agent_id=&user_id=${encodeURIComponent(userId)}`);
        ws.binaryType = 'arraybuffer';
        handle.ws = ws;

        ws.onopen = () => {
            if (_current !== handle) return;
            // Override agent config so the greeting is spoken in THIS voice.
            ws.send(JSON.stringify({
                type: 'agent_meta',
                agent: {
                    name: 'Sahaiy Voice Preview',
                    system_prompt: '',
                    first_message: SAMPLE_LINE,
                    voice_name: slug,
                    language: language || 'Hindi / English (Hinglish)',
                },
            }));
            // Declared audio format triggers `_send_greeting_once()` server-side,
            // which speaks `first_message` through the requested speaker and
            // streams back WAV binary frames.
            ws.send(JSON.stringify({
                type: 'audio_meta',
                format: 'pcm16',
                mime_type: 'audio/pcm',
                sample_rate: 16000,
                channels: 1,
            }));
        };

        ws.onmessage = (event) => {
            if (_current !== handle) return;
            if (typeof event.data === 'string') return; // JSON control frames — not needed here

            // Empty binary frame = keepalive ping from server
            if (!event.data || !event.data.byteLength) return;

            // Track chunk cadence for the end-of-stream grace window
            const now = Date.now();
            if (handle.lastChunkAt) {
                handle.lastChunkGap = now - handle.lastChunkAt;
            }
            handle.lastChunkAt = now;

            if (!greeted) greeted = true;

            const blob = new Blob([event.data], { type: 'audio/wav' });
            const url = URL.createObjectURL(blob);
            if (handle.url) URL.revokeObjectURL(handle.url);
            handle.url = url;

            if (!handle.audio) {
                handle.audio = new Audio(url);
                handle.audio.onended = () => {
                    // Natural end: wait for a possible follow-up WAV frame before
                    // declaring completion. The window scales with observed chunk
                    // cadence (min 250ms, up to 2s) so slow links don't truncate
                    // previews mid-sentence (code-reviewer minor #1, PR #35).
                    const grace = Math.min(Math.max(handle.lastChunkGap || 500, 250), 2000);
                    setTimeout(() => {
                        if (_current === handle && !handle.audio?.loop && handle.audio?.paused) finish();
                    }, grace);
                };
                handle.audio.play().catch(() => {
                    onError?.(slug, 'Playback failed in this browser.');
                    stopPreview();
                });
            } else {
                handle.audio.src = url;
                handle.audio.play().catch(() => {});
            }
        };

        ws.onerror = () => {
            onError?.(slug, "Couldn't reach the preview service. Is the backend running?");
            if (_current === handle) { _teardown(); onEnd?.(slug); }
        };

        ws.onclose = () => {
            if (_current === handle) { _teardown(); onEnd?.(slug); }
        };
    } catch (err) {
        onError?.(slug, err?.message || 'Preview failed to start.');
        if (_current === handle) { _teardown(); onEnd?.(slug); }
    }
}
