/**
 * src/components/Playground.js
 * ─────────────────────────────
 * Playground panel — UI-only chat simulator for testing agents.
 * No real voice/AI calls made here; this is a demo UX component.
 */

import { $ } from '@/utils/dom.js';

let _isConnected = false;
let _replyIndex = 0;

const AGENT_REPLIES = [
    'Namaste! How can I help you today?',
    'Sure, could you please provide more details?',
    'I understand. Let me check that for you right away.',
    'Your order is out for delivery and should arrive by 6 PM today!',
    'Is there anything else I can assist you with?',
    'Thank you for your time. Have a great day! 😊',
];

/** Opens the slide-over playground panel. */
export function openPlayground() {
    $('#playground-panel')?.classList.add('open');
}

/** Closes the playground panel. */
export function closePlayground() {
    $('#playground-panel')?.classList.remove('open');
    _isConnected = false;
    _updateOrbState();
}

/** Toggles the simulated call connection state. */
export function togglePlaygroundCall() {
    _isConnected = !_isConnected;
    _updateOrbState();
}

function _updateOrbState() {
    const status = $('#playground-call-status');
    const orb = $('#playground-orb-btn');

    if (_isConnected) {
        if (status) { status.textContent = '● Connected'; status.style.color = '#22c55e'; }
        if (orb) orb.style.boxShadow = '0 0 0 12px rgba(34,197,94,0.1), 0 8px 24px rgba(34,197,94,0.3)';
    } else {
        if (status) { status.textContent = 'Click orb to start a test call'; status.style.color = 'var(--dash-text-3)'; }
        if (orb) orb.style.boxShadow = '0 0 0 12px rgba(124,92,252,0.08), 0 8px 24px rgba(124,92,252,0.3)';
    }
}

/**
 * Sends a user message and appends a simulated agent reply.
 * Reads the message from #playground-msg-input.
 */
export function sendPlaygroundMessage() {
    const input = $('#playground-msg-input');
    const chat = $('#playground-chat');
    if (!input || !chat) return;

    const text = input.value.trim();
    if (!text) return;

    // Append user bubble
    const userBubble = document.createElement('div');
    userBubble.className = 'chat-bubble chat-bubble--user';
    userBubble.textContent = text;
    chat.appendChild(userBubble);
    input.value = '';
    chat.scrollTop = chat.scrollHeight;

    // Append agent reply after a short delay
    const delay = 600 + Math.random() * 600;
    setTimeout(() => {
        const agentBubble = document.createElement('div');
        agentBubble.className = 'chat-bubble chat-bubble--agent';
        agentBubble.textContent = AGENT_REPLIES[_replyIndex++ % AGENT_REPLIES.length];
        chat.appendChild(agentBubble);
        chat.scrollTop = chat.scrollHeight;
    }, delay);
}
