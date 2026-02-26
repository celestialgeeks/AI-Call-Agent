/**
 * src/components/Modal.js
 * ────────────────────────
 * Create Agent modal manager.
 * Completely decoupled from data logic — fires a callback on submit.
 */

import { $ } from '@/utils/dom.js';

/**
 * Opens the Create Agent modal.
 */
export function openCreateModal() {
    $('#create-modal')?.classList.add('open');
}

/**
 * Closes the Create Agent modal and resets form fields.
 */
export function closeCreateModal() {
    $('#create-modal')?.classList.remove('open');
    const nameInput = $('#new-agent-name');
    if (nameInput) nameInput.value = '';
    $('[data-template].selected')?.classList.remove('selected');
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
 * @returns {{ name: string, voiceName: string, icon: string, template: string }}
 */
export function readCreateAgentForm() {
    const name = ($('#new-agent-name')?.value ?? '').trim() || 'New Agent';
    const voiceRaw = $('#new-agent-voice')?.value ?? '';
    const voiceName = voiceRaw.split('(')[0].trim() || 'Priya';
    const icon = document.querySelector('.template-card.selected .template-icon')?.textContent?.trim() ?? '🤖';
    const template = document.querySelector('.template-card.selected')?.dataset.template ?? 'blank';
    return { name, voiceName, icon, template };
}
