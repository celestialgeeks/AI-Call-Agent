/**
 * src/utils/toast.js
 * ───────────────────
 * Lightweight toast notification utility.
 * Renders a self-removing dismissible notification at the bottom-right.
 */

const TOAST_DURATION_MS = 4_000;
const TOAST_FADE_MS = 300;

/**
 * Shows a toast notification.
 * @param {string} message  — displayed text (emoji supported)
 * @param {'info'|'success'|'error'|'warning'} [type='info']
 */
export function showToast(message, type = 'info') {
    // Remove any existing toast first
    document.querySelector('.toast')?.remove();

    const colorMap = {
        info: '#111',
        success: '#15803d',
        error: '#dc2626',
        warning: '#b45309',
    };

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');

    Object.assign(toast.style, {
        position: 'fixed',
        bottom: '24px',
        right: '24px',
        zIndex: '9999',
        background: '#111',
        color: 'white',
        padding: '12px 20px',
        borderRadius: '10px',
        fontSize: '13px',
        fontFamily: 'var(--font-sans, Inter, sans-serif)',
        boxShadow: '0 8px 24px rgba(0,0,0,0.2)',
        animation: 'slideIn 0.25s ease',
        maxWidth: '340px',
        lineHeight: '1.5',
        borderLeft: `3px solid ${colorMap[type]}`,
    });

    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.transition = `opacity ${TOAST_FADE_MS}ms ease`;
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), TOAST_FADE_MS);
    }, TOAST_DURATION_MS);
}
