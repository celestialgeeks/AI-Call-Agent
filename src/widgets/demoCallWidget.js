/**
 * src/widgets/demoCallWidget.js
 * ─────────────────────────────
 * Simulated call widget (issue #6) — live transcript + voice reply.
 * Primary path: WS /ws/audio?agent_id=<uuid> + {type:text_input} turns (no mic).
 * Secondary: 16 kHz mono PCM16 mic via audio_meta frame. v1 = simulated only.
 */

import { DemoCallClient } from '@/services/demoCallService.js';
import { showToast } from '@/utils/toast.js';

const DEMO_AGENT_ID = 'ag_demo'; // server falls back to default config if unknown

const WIDGET_MARKUP = `
<div class="dcw-backdrop" id="dcw-backdrop" hidden></div>
<div class="dcw" id="demo-call-widget" role="dialog" aria-modal="true"
     aria-labelledby="dcw-title" hidden>
  <header class="dcw-header">
    <div class="dcw-agent" aria-hidden="true">
      <div class="orb-container dcw-orb" id="dcw-orb">
        <div class="orb-ring"></div><div class="orb-ring"></div><div class="orb-ring"></div>
        <div class="orb-core" style="width:44px;height:44px;font-size:18px;">🎙️</div>
      </div>
    </div>
    <div class="dcw-meta">
      <h3 id="dcw-title">Customer Support Agent</h3>
      <p class="dcw-status" id="dcw-status"><span class="dcw-dot dcw-dot--connecting"></span>Connecting…</p>
    </div>
    <button class="dcw-icon-btn" id="dcw-close" type="button" aria-label="End demo call and close">✕</button>
  </header>

  <div class="dcw-transcript" id="dcw-transcript" aria-live="polite" aria-label="Live conversation transcript"></div>
  <div class="dcw-error" id="dcw-error" role="alert" hidden></div>

  <footer class="dcw-footer">
    <form class="dcw-composer" id="dcw-form">
      <label class="sr-only" for="dcw-input">Message the agent</label>
      <input id="dcw-input" class="dcw-input" type="text" autocomplete="off"
             placeholder="Type a message…" maxlength="500" />
      <button class="dcw-send" type="submit" aria-label="Send message">➤</button>
    </form>
    <div class="dcw-actions">
      <button class="dcw-chip" id="dcw-mic" type="button" aria-pressed="false"
              title="Talk to the agent with your microphone">🎙️ Talk</button>
      <button class="dcw-chip dcw-chip--danger" id="dcw-interrupt" type="button" disabled
              title="Interrupt the agent mid-sentence">✋ Interrupt</button>
      <button class="dcw-chip dcw-chip--danger" id="dcw-end" type="button">📞 End call</button>
    </div>
    <p class="dcw-note">Simulated demo · no PSTN/SIP calls, no charges.</p>
  </footer>
</div>
`;

let client = null;
let els = null;
/** @type {string[]} */
const agentLineBuffers = [];

function mountWidget() {
    const host = document.createElement('div');
    host.id = 'dcw-root';
    host.innerHTML = WIDGET_MARKUP;
    document.body.appendChild(host);

    els = {
        root: host,
        backdrop: host.querySelector('#dcw-backdrop'),
        widget: host.querySelector('#demo-call-widget'),
        status: host.querySelector('#dcw-status'),
        orb: host.querySelector('#dcw-orb'),
        transcript: host.querySelector('#dcw-transcript'),
        error: host.querySelector('#dcw-error'),
        form: host.querySelector('#dcw-form'),
        input: host.querySelector('#dcw-input'),
        sendBtn: host.querySelector('.dcw-send'),
        micBtn: host.querySelector('#dcw-mic'),
        interruptBtn: host.querySelector('#dcw-interrupt'),
        endBtn: host.querySelector('#dcw-end'),
        closeBtn: host.querySelector('#dcw-close'),
    };

    els.form.addEventListener('submit', onSend);
    els.micBtn.addEventListener('click', onMicToggle);
    els.interruptBtn.addEventListener('click', () => client && client.sendInterrupt());
    els.endBtn.addEventListener('click', closeDemoCall);
    els.closeBtn.addEventListener('click', closeDemoCall);
    els.backdrop.addEventListener('click', closeDemoCall);
    document.addEventListener('keydown', onKeydown);
}

function onKeydown(e) {
    if (!els || els.widget.hidden) return;
    if (e.key === 'Escape') {
        e.preventDefault();
        closeDemoCall();
        return;
    }
    if (e.key === 'Tab') trapFocus(e);
}

function trapFocus(e) {
    const focusable = els.widget.querySelectorAll(
        'button, input, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
    }
}

/* ── Transcript rendering ──────────────────────────────────── */

function addBubble(role, text) {
    const bubble = document.createElement('div');
    bubble.className = `dcw-bubble dcw-bubble--${role}`;
    bubble.textContent = text;
    els.transcript.appendChild(bubble);
    els.transcript.scrollTop = els.transcript.scrollHeight;
    return bubble;
}

function setStatus(state, label) {
    els.status.innerHTML = `<span class="dcw-dot dcw-dot--${state}"></span>${label}`;
    els.orb.classList.toggle('active', state === 'speaking' || state === 'listening');
}

/* ── Handlers ──────────────────────────────────────────────── */

function makeClient() {
    return new DemoCallClient({
        onOpen() {
            setStatus('listening', 'Connected — say hello!');
            hideError();
            els.input.focus();
        },
        onUserTranscript(text) {
            if (text) addBubble('user', text);
        },
        onAgentFragment(text) {
            if (!text) return;
            // Fragments stream in sequence — append to the current agent bubble.
            let bubble = els.transcript.querySelector('.dcw-bubble--agent:last-child');
            const expectingNew = !bubble || agentLineBuffers.length === 0;
            if (expectingNew) {
                agentLineBuffers.push('');
                addBubble('agent', text);
            } else {
                agentLineBuffers[agentLineBuffers.length - 1] += text;
                bubble.textContent = agentLineBuffers[agentLineBuffers.length - 1];
                els.transcript.scrollTop = els.transcript.scrollHeight;
            }
            setStatus('speaking', 'Agent is speaking…');
            els.interruptBtn.disabled = false;
        },
        onAgentSpeechStart() {
            setStatus('speaking', 'Agent is speaking…');
            els.interruptBtn.disabled = false;
        },
        onPlaybackIdle() {
            if (client && client.isOpen) setStatus('listening', 'Your turn — type or talk');
            els.interruptBtn.disabled = true;
        },
        onInterrupted() {
            agentLineBuffers.length = 0;
            setStatus('listening', 'Interrupted — your turn');
            els.interruptBtn.disabled = true;
        },
        onError(message) {
            showError(message);
        },
        onClose() {
            setStatus('ended', 'Call ended');
            showToast('📞 Demo call ended', 'info');
            teardown();
        },
    });
}

async function onSend(e) {
    e.preventDefault();
    const message = els.input.value.trim();
    if (!message || !client || !client.isOpen) return;
    els.input.value = '';
    agentLineBuffers.length = 0; // next fragment starts a fresh agent bubble
    client.sendText(message);
}

async function onMicToggle() {
    if (!client) return;
    if (client.micActive) {
        client.sendMicOff();
        els.micBtn.setAttribute('aria-pressed', 'false');
        els.micBtn.classList.remove('dcw-chip--live');
        els.micBtn.textContent = '🎙️ Talk';
        showToast('🎤 Mic off', 'info');
        return;
    }
    const ok = await client.sendMicOn();
    if (ok) {
        els.micBtn.setAttribute('aria-pressed', 'true');
        els.micBtn.classList.add('dcw-chip--live');
        els.micBtn.textContent = '🔴 Listening';
        showToast('🎤 Mic live — speak after the agent finishes', 'success');
    }
}

/* ── Error surface ─────────────────────────────────────────── */

function showError(message) {
    els.error.textContent = `⚠️ ${message}`;
    els.error.hidden = false;
}

function hideError() {
    els.error.hidden = true;
    els.error.textContent = '';
}

/* ── Lifecycle ─────────────────────────────────────────────── */

export function startDemo() {
    try {
        if (els && !els.widget.hidden) return; // already open

        if (!els) mountWidget();

        // Reset UI state for a fresh session.
        els.transcript.innerHTML = '';
        agentLineBuffers.length = 0;
        hideError();
        els.widget.hidden = false;
        els.backdrop.hidden = false;
        document.body.style.overflow = 'hidden';
        els.interruptBtn.disabled = true;

        client = makeClient();
        client.agentId = DEMO_AGENT_ID;
        client.connect(DEMO_AGENT_ID);

        els.closeBtn.focus();
    } catch (err) {
        console.error('[startDemo]', err);
        showToast('⚠️ Could not start the demo call. Please retry.', 'error');
    }
}

export function closeDemoCall() {
    if (els) {
        els.widget.hidden = true;
        els.backdrop.hidden = true;
        document.body.style.overflow = '';
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    }
    if (client) {
        client.close();
        client = null;
    }
    teardown();
}

function teardown() {
    if (els) {
        els.micBtn.setAttribute('aria-pressed', 'false');
        els.micBtn.classList.remove('dcw-chip--live');
        els.micBtn.textContent = '🎙️ Talk';
        els.interruptBtn.disabled = true;
    }
}
