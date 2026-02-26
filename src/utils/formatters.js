/**
 * src/utils/formatters.js
 * ────────────────────────
 * Pure formatting functions — no side effects, no DOM access.
 * Every function here is deterministic and unit-testable.
 */

/**
 * Converts seconds to a "M:SS" display string.
 * @param {number} totalSeconds
 * @returns {string}  e.g. 134 → "2:14"
 */
export function formatDuration(totalSeconds) {
    if (!totalSeconds || totalSeconds < 0) return '0:00';
    const mins = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${mins}:${String(secs).padStart(2, '0')}`;
}

/**
 * Returns a human-readable relative time string.
 * @param {string|Date} isoOrDate
 * @returns {string}  e.g. "3m ago", "2h ago", "Yesterday"
 */
export function timeAgo(isoOrDate) {
    if (!isoOrDate) return '';
    const diffMs = Date.now() - new Date(isoOrDate).getTime();
    const mins = Math.floor(diffMs / 60_000);

    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;

    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;

    const days = Math.floor(hours / 24);
    return days === 1 ? 'Yesterday' : `${days}d ago`;
}

/**
 * Formats an ISO string into a locale date string for India.
 * @param {string} iso
 * @returns {string}  e.g. "27 Feb 2026"
 */
export function formatDate(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString('en-IN', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
    });
}

/**
 * Converts bytes to a human-readable size string.
 * @param {number} bytes
 * @returns {string}  e.g. "1.2 MB", "800 KB"
 */
export function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    if (bytes < 1_024) return `${bytes} B`;
    if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(0)} KB`;
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

/**
 * Returns a CSS class name for a given conversation status.
 * @param {'resolved'|'escalated'|'missed'|'in_progress'} status
 * @returns {string}
 */
export function statusBadgeClass(status) {
    const map = {
        resolved: 'badge--green',
        escalated: 'badge--orange',
        in_progress: 'badge--blue',
        missed: 'badge--red',
    };
    return map[status] ?? 'badge--gray';
}
