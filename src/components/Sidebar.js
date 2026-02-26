/**
 * src/components/Sidebar.js
 * ──────────────────────────
 * Dashboard SPA navigation manager.
 * Handles active state for both nav items and page panels.
 */

import { $, $$ } from '@/utils/dom.js';

/** Map of pageId → display title */
const PAGE_TITLES = {
    home: 'Home',
    agents: 'Agents',
    'agent-config': 'Agent Configuration',
    knowledge: 'Knowledge Base',
    tools: 'Tools',
    integrations: 'Integrations',
    voices: 'Voices',
    conversations: 'Conversations',
    analytics: 'Analytics',
    tests: 'Tests',
    phonenumbers: 'Phone Numbers',
    whatsapp: 'WhatsApp',
    outbound: 'Outbound',
    widget: 'Web Widget',
    settings: 'Settings',
};

/**
 * Navigates to a given dashboard page.
 * Hides all other pages and deactivates all nav items.
 *
 * @param {string}      pageId  — matches the `id="page-<pageId>"` convention
 * @param {Element|null} navEl  — the sidebar nav item to mark active
 */
export function navigate(pageId, navEl) {
    // Deactivate everything
    $$('.page').forEach((p) => p.classList.remove('active'));
    $$('.sidebar-item').forEach((i) => i.classList.remove('active'));

    // Activate target
    const page = $(`#page-${pageId}`);
    if (page) page.classList.add('active');
    if (navEl) navEl.classList.add('active');

    // Update top-bar title
    const titleEl = $('#topbar-title');
    if (titleEl) titleEl.textContent = PAGE_TITLES[pageId] ?? pageId;
}

/** Switches the config tab inside the agent-config page. */
export function switchConfigTab(tabName, buttonEl) {
    $$('[id^="config-tab-"]').forEach((el) => (el.style.display = 'none'));
    $$('.config-tab').forEach((t) => t.classList.remove('active'));

    const target = $(`#config-tab-${tabName}`);
    if (target) target.style.display = 'block';
    if (buttonEl) buttonEl.classList.add('active');
}

/** Switches the chart period tab. */
export function switchPeriodTab(btn) {
    $$('.chart-period-tab').forEach((t) => t.classList.remove('active'));
    btn?.classList.add('active');
}
