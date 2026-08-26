/**
 * src/components/Modal.js
 * ────────────────────────
 * Create Agent modal manager.
 * Completely decoupled from data logic — fires a callback on submit.
 */

import { $ } from '@/utils/dom.js';

/**
 * Opens the Create Agent modal and focuses the name field after animation.
 */
export function openCreateModal() {
    const modal = $('#create-modal');
    modal?.classList.add('open');
    // Focus name input after CSS transition (300ms)
    setTimeout(() => $('#new-agent-name')?.focus(), 300);
}

/**
 * Closes the Create Agent modal and resets form fields.
 */
export function closeCreateModal() {
    $('#create-modal')?.classList.remove('open');
    const nameInput = $('#new-agent-name');
    if (nameInput) nameInput.value = '';
    $('[data-template].selected')?.classList.remove('selected');
    // Re-select default template
    $('[data-template="customer_support"]')?.classList.add('selected');
}

/**
 * Handles template card selection in the Create Agent modal.
 * @param {Element} card — the clicked template card
 */
export function selectTemplate(card) {
    document.querySelectorAll('.template-card').forEach((c) => c.classList.remove('selected'));
    card.classList.add('selected');
}

/**
 * Reads the current values from the Create Agent modal form.
 * @returns {{ name: string, voiceName: string, voiceLang: string, icon: string, template: string, lang: string }}
 */
export function readCreateAgentForm() {
    const name = ($('#new-agent-name')?.value ?? '').trim() || 'New Agent';
    // Voice select now uses value= attributes that match Sarvam AI speaker names exactly
    const voiceName = $('#new-agent-voice')?.value ?? 'anushka';
    const lang = $('#new-agent-lang')?.value ?? 'en-IN';
    // Template icons are now SVG glyphs (design spec v2 §3); the stored agent
    // icon falls back to the template slug instead of an emoji.
    const template = document.querySelector('.template-card.selected')?.dataset?.template ?? 'blank';
    const icon = template;

    // Friendly voice language label for display
    const langLabels = {
        'en-IN': 'English-IN', 'hi-IN': 'Hindi/EN', 'ta-IN': 'Tamil/EN',
        'te-IN': 'Telugu/EN', 'mr-IN': 'Marathi/EN', 'bn-IN': 'Bengali/EN', 'kn-IN': 'Kannada/EN',
    };
    const voiceLang = langLabels[lang] ?? 'Hindi/EN';

    return { name, voiceName, voiceLang, icon, template, lang };
}
