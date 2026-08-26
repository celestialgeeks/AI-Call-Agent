/**
 * src/config/pricing.js
 * ─────────────────────
 * Single source of truth for all INR cost math (spec v1 §4).
 *
 * HARD RULES:
 * - All rates are in INR. Every rate stays `null` until filled from a cited
 *   vendor pricing page — no guessed numbers, ever (ADR-0001).
 * - Any `null` rate ⇒ estimateCallCost returns `null` ⇒ UI renders "—",
 *   NEVER ₹0 and never an approximation.
 * - Conversations rows, Analytics KPIs, and the estimate card MUST import
 *   their math from this module. No duplicated math elsewhere.
 */

// ── Vendor rates ─────────────────────────────────────────────
// Fill each constant ONLY with the rate printed on the vendor's public
// pricing page; cite URL + retrieval date in the comment next to it.

/**
 * Sarvam TTS price per character, INR.
 * Source: <vendor pricing page URL> — retrieved YYYY-MM-DD.
 */
export const SARVAM_TTS_PER_CHAR = null;

/**
 * Speech-to-text price per second, INR.
 * Source: <vendor pricing page URL> — retrieved YYYY-MM-DD.
 */
export const STT_PER_SEC = null;

/** LLM price per 1K tokens, INR. */
export const LLM_PER_1K_TOKENS = {
    input: null,
    output: null,
};

/**
 * Telephony price per minute of connected call time, INR.
 * Source: <vendor pricing page URL> — retrieved YYYY-MM-DD.
 */
export const TELEPHONY_PER_MIN = null;

export const CURRENCY = 'INR';

// ── Cost math ────────────────────────────────────────────────

const ratesComplete = () =>
    SARVAM_TTS_PER_CHAR !== null
    && STT_PER_SEC !== null
    && LLM_PER_1K_TOKENS.input !== null
    && LLM_PER_1K_TOKENS.output !== null
    && TELEPHONY_PER_MIN !== null;

function round2(n) {
    return Math.round(n * 100) / 100;
}

/**
 * Estimates the total cost of one call from its metered usage.
 *
 * @param {object} usage
 * @param {number|null} [usage.seconds]      - connected call duration
 * @param {number|null} [usage.ttsChars]     - TTS characters spoken
 * @param {number|null} [usage.llmTokensIn]  - prompt tokens consumed
 * @param {number|null} [usage.llmTokensOut] - completion tokens produced
 * @returns {number|null} estimated cost in INR (rounded to 2dp),
 *                        or `null` when any rate is unfilled or usage is absent.
 */
export function estimateCallCost({ seconds = null, ttsChars = null, llmTokensIn = null, llmTokensOut = null } = {}) {
    if (!ratesComplete()) return null;

    // Missing usage fields ⇒ unknown cost, not zero cost.
    if (seconds == null || ttsChars == null || llmTokensIn == null || llmTokensOut == null) {
        return null;
    }

    const telephony = (seconds / 60) * TELEPHONY_PER_MIN;
    const tts = ttsChars * SARVAM_TTS_PER_CHAR;
    const llmIn = (llmTokensIn / 1000) * LLM_PER_1K_TOKENS.input;
    const llmOut = (llmTokensOut / 1000) * LLM_PER_1K_TOKENS.output;

    return round2(telephony + tts + llmIn + llmOut);
}

/**
 * Formats an INR amount for display.
 *
 * @param {number|null} n - amount in INR; `null` renders as em-dash (unknown).
 * @param {object} [opts]
 * @param {boolean} [opts.decimals=true] - include paise ("₹0.42") vs whole rupees ("₹1,240")
 * @returns {string} e.g. "₹0.42" | "₹1,240" | "—"
 */
export function formatINR(n, { decimals = true } = {}) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
    if (!decimals) return `₹${Math.round(n).toLocaleString('en-IN')}`;
    return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
