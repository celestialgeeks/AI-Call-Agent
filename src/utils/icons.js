/**
 * src/utils/icons.js
 * ──────────────────
 * Inline SVG icon system (design spec v2 §3).
 * Grid: 24×24 viewBox · 1.5px strokes · round caps/joins · fill none ·
 * stroke currentColor (glyph inherits CSS `color`; never hardcode fills).
 *
 * Sources:
 *   - agents/analytics/conversations/knowledge/phone-numbers/settings/
 *     whatsapp delivered by @design-bot (projects/sahaiy/design/icons/nav/),
 *     settings.svg is Lucide gear geometry (ISC license).
 *   - home/tools/integrations/voices/tests/outbound/widget/search/bell/help/
 *     file authored in-house following the same Lucide-style model.
 *
 * Usage: import { icon } from '@/utils/icons.js'; icon('phone')
 */

const S =
    'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"';

const ICONS = {
    home: `
        <path d="M4.75 11.25 12 4.75l7.25 6.5"/>
        <path d="M6.75 9.9v9.35h10.5V9.9"/>`,
    agents: `
        <rect x="4.75" y="9" width="14.5" height="10.25" rx="2.5"/>
        <path d="M12 5.5v3.5"/>
        <circle cx="12" cy="4" r="1.25"/>
        <circle cx="9.25" cy="13.5" r=".4" fill="currentColor" stroke="none"/>
        <circle cx="14.75" cy="13.5" r=".4" fill="currentColor" stroke="none"/>`,
    knowledge: `
        <path d="M5.25 19.5V5.75A2.25 2.25 0 0 1 7.5 3.5h11.25v13.25H7.5a2.25 2.25 0 0 0-2.25 2.25 2.25 2.25 0 0 0 2.25 2.25h11.25"/>`,
    tools: `
        <path d="M14.2 6.6a1.06 1.06 0 0 0 0 1.5l1.7 1.7a1.06 1.06 0 0 0 1.5 0l3.2-3.2a6 6 0 0 1-7.9 7.9l-6.5 6.5a2 2 0 0 1-2.8-2.8l6.5-6.5a6 6 0 0 1 7.9-7.9z"/>`,
    integrations: `
        <path d="M10.2 13.8a3.9 3.9 0 0 0 5.52 0l3.05-3.05a3.9 3.9 0 1 0-5.52-5.52L11.9 6.58"/>
        <path d="M13.8 10.2a3.9 3.9 0 0 0-5.52 0l-3.05 3.05a3.9 3.9 0 1 0 5.52 5.52l1.35-1.35"/>`,
    voices: `
        <path d="M9.25 4.25a2.75 2.75 0 0 1 5.5 0v6a2.75 2.75 0 0 1-5.5 0z"/>
        <path d="M5.5 11.25a6.5 6.5 0 0 0 13 0"/>
        <path d="M12 17.75v3"/>`,
    conversations: `
        <path d="M12 4.75c-4.28 0-7.75 3-7.75 6.7 0 2.1 1.1 3.97 2.83 5.2v3.1l3.06-1.7c.6.13 1.23.2 1.86.2 4.28 0 7.75-3 7.75-6.7s-3.47-6.8-7.75-6.8z"/>`,
    analytics: `
        <path d="M6.5 16.25v-3.5M12 16.25v-8M17.5 16.25v-5.75"/>
        <path d="M4.25 19.75h15.5"/>`,
    tests: `
        <path d="M9.5 3.5h5"/>
        <path d="M10.25 3.5v5.1L4.9 17.9a1.8 1.8 0 0 0 1.57 2.68h11.06a1.8 1.8 0 0 0 1.57-2.68l-5.35-9.3V3.5"/>
        <path d="M7.5 14.5h9"/>`,
    'phone-numbers': `
        <path d="M20.5 16.72v2.36a1.75 1.75 0 0 1-1.91 1.74 17.3 17.3 0 0 1-7.55-2.69 17.05 17.05 0 0 1-5.25-5.25A17.3 17.3 0 0 1 3.1 5.25 1.75 1.75 0 0 1 4.84 3.5H7.2a1.75 1.75 0 0 1 1.75 1.5c.11.85.31 1.68.6 2.47a1.75 1.75 0 0 1-.4 1.84L8.1 10.35a14 14 0 0 0 5.25 5.25l1.04-1.04a1.75 1.75 0 0 1 1.84-.4c.79.29 1.62.49 2.47.61a1.75 1.75 0 0 1 1.5 1.78z"/>`,
    phone: null, // alias resolved in icon() to 'phone-numbers'
    whatsapp: `
        <path d="M4.75 4.75h14.5v11.5H9.5l-4.75 4v-15.5z"/>`,
    outbound: `
        <circle cx="12" cy="12" r="1.9"/>
        <path d="M8.7 15.3a4.67 4.67 0 0 1 0-6.6M15.3 8.7a4.67 4.67 0 0 1 0 6.6"/>
        <path d="M6 18a8.49 8.49 0 0 1 0-12M18 6a8.49 8.49 0 0 1 0 12"/>`,
    widget: `
        <circle cx="12" cy="12" r="8.25"/>
        <path d="M12 3.75c2.6 2.3 2.6 14.2 0 16.5M12 3.75c-2.6 2.3-2.6 14.2 0 16.5"/>
        <path d="M3.75 12h16.5"/>`,
    settings: `
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
        <circle cx="12" cy="12" r="3"/>`,
    search: `
        <circle cx="11" cy="11" r="6.75"/>
        <path d="m15.8 15.8 4.45 4.45"/>`,
    bell: `
        <path d="M17.6 8.9a5.6 5.6 0 1 0-11.2 0c0 5.6-2.3 6.6-2.3 6.6h15.8s-2.3-1-2.3-6.6"/>
        <path d="M10.1 19a2.05 2.05 0 0 0 3.8 0"/>`,
    help: `
        <circle cx="12" cy="12" r="8.25"/>
        <path d="M9.4 9.4a2.6 2.6 0 0 1 5.05.87c0 1.73-2.6 2.6-2.6 2.6"/>
        <circle cx="12" cy="16.6" r=".4" fill="currentColor" stroke="none"/>`,
    file: `
        <path d="M13.5 3.5H7.25a1.5 1.5 0 0 0-1.5 1.5v14a1.5 1.5 0 0 0 1.5 1.5h9.5a1.5 1.5 0 0 0 1.5-1.5V8.25z"/>
        <path d="M13.5 3.5v4.75h4.75"/>`,
};

/** Returns the full inline-SVG string for one icon name. Unknown names → ''. */
export function icon(name) {
    if (name === 'phone') name = 'phone-numbers'; // alias
    const body = ICONS[name];
    return body ? `<svg ${S}>${body}</svg>` : '';
}
