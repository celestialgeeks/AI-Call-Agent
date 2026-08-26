/**
 * src/config/pricing.js
 * ────────────────────
 * Single source of truth for cost estimation math (design spec §4).
 * All rates in INR unless noted. Every constant carries a citation comment
 * with its source URL and retrieval date.
 *
 * HARD RULE: any `null` rate ⇒ estimateCallCost() returns null ⇒ the UI
 * renders an em-dash "—", NEVER a guess. Zero means measured-zero; a
 * missing/unknown rate means unknown — different things.
 *
 * Update procedure: replace a null / stale number with the current published
 * vendor rate, cite the page + retrieval date next to the constant, and every
 * consumer (Conversations rows, Analytics KPI, Home estimate card) updates
 * automatically. No cost math may be duplicated outside this module.
 */

/** Currency for all formatted amounts in this module. */
export const CURRENCY = 'INR';

/**
 * Sarvam STT — ₹30 per hour of audio (₹45 with diarization).
 * Source: https://docs.sarvam.ai/api/getting-started/pricing (retrieved 2026-08-26).
 * NOTE: sahaiy-backend currently transcribes with self-hosted whisper.cpp
 * (sahaiy-backend/app/config.py STT_URL), so effective infra cost is ~0 today.
 * This rate is kept as the published Sarvam rate for when we switch back;
 * set to 0 explicitly if self-hosting is confirmed permanent.
 */
export const SARVAM_STT_PER_HOUR = 30;

/**
 * Per-second STT rate derived from SARVAM_STT_PER_HOUR.
 * Derived value — do not edit independently.
 */
export const STT_PER_SEC = SARVAM_STT_PER_HOUR / 3600;

/**
 * Sarvam TTS (Bulbul v2) — ₹15 per 10,000 characters.
 * Bulbul v3 would be ₹30 per 10,000 chars on the same page.
 * Source: https://docs.sarvam.ai/api/getting-started/pricing (retrieved 2026-08-26).
 */
export const SARVAM_TTS_PER_10K_CHARS = 15;

/**
 * LLM rates per 1K tokens. The backend currently runs a local llama.cpp
 * server (sahaiy-backend/app/config.py LLM_URL), so token inference carries
 * no metered API cost today ⇒ both are 0 (measured-zero infrastructure),
 * NOT null (the rate IS known). If the stack moves to NVIDIA NIM hosted
 * endpoints (~$0.90/1M tokens blended for llama-3.3-70b-nim, per
 * build.nvidia.com pricing, retrieved 2026-08-26), update here only.
 */
export const LLM_PER_1K_TOKENS = { input: 0, output: 0 };

/**
 * Telephony (PSTN) per-minute rate. No telephony provider is wired yet
 * (LiveKit config exists but issue #24 outbound calling is dormant), so the
 * rate is UNKNOWN ⇒ null ⇒ cost contributions render as "—".
 * Fill from the chosen provider's published rate before enabling real calls.
 */
export const TELEPHONY_PER_MIN = null;

// ────────────────────────────────────────────────────────────────
//  Estimation
// ────────────────────────────────────────────────────────────────

/**
 * Estimates the infrastructure cost of one call in INR.
 *
 * @param {object} usage          — real metered usage for a single call
 * @param {number} [usage.seconds]      — audio duration in seconds
 * @param {number} [usage.ttsChars]     — characters spoken via TTS
 * @param {number} [usage.llmTokensIn]  — prompt tokens consumed
 * @param {number} [usage.llmTokensOut] — completion tokens generated
 * @returns {number|null} estimated cost in INR, or null when any REQUIRED
 *   component's rate is unknown (TELEPHONY_PER_MIN === null) or no usage
 *   fields are provided at all. Never fabricates a number.
 */
export function estimateCallCost({ seconds = 0, ttsChars = 0, llmTokensIn = 0, llmTokensOut = 0 } = {}) {
    // Telephony is part of every real call and its rate is unknown ⇒ total unknown.
    if (TELEPHONY_PER_MIN === null) return null;

    // Require at least one real usage signal — an empty record is not a ₹0 call.
    const hasUsage = seconds > 0 || ttsChars > 0 || llmTokensIn > 0 || llmTokensOut > 0;
    if (!hasUsage) return null;

    let total = 0;
    total += seconds * STT_PER_SEC;
    total += ttsChars * (SARVAM_TTS_PER_10K_CHARS / 10_000);
    total += ((llmTokensIn + llmTokensOut) / 1000)
        * (LLM_PER_1K_TOKENS.input + LLM_PER_1K_TOKENS.output);

    return total;
}

const inrFmt = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

/** Compact variant without decimals — used for card-level figures like ₹1,240. */
const inrFmtCompact = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
});

/**
 * Formats a number as INR. Returns '—' for null/undefined/NaN
 * (unknown ≠ zero: zeros must come through as the number 0).
 *
 * @param {number|null|undefined} n
 * @param {{compact?: boolean}} [opts]
 */
export function formatINR(n, opts = {}) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return '\u2014'; // em-dash
    return opts.compact ? inrFmtCompact.format(n) : inrFmt.format(n);
}

/**
 * Human-readable formula for tooltips / footnotes.
 * Reflects the constants above — keep in sync automatically by deriving text
 * from the exported values.
 */
export function estimateFormulaText() {
    if (TELEPHONY_PER_MIN === null) {
        return 'Telephony rate not yet configured \u2014 estimates unavailable.';
    }
    return [
        `STT \u20B9${SARVAM_STT_PER_HOUR}/hr`,
        `TTS \u20B9${SARVAM_TTS_PER_10K_CHARS}/10k chars`,
        `LLM \u20B9${LLM_PER_1K_TOKENS.input + LLM_PER_1K_TOKENS.output}/1k tokens`,
        `telephony \u20B9${TELEPHONY_PER_MIN}/min`,
    ].join(' · ');
}
