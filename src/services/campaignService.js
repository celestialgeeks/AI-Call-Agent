/**
 * src/services/campaignService.js
 * ───────────────────────────────
 * Outreach campaign API client (issue #8).
 *
 * Codes against the FROZEN contract of the campaigns backend (issue #7,
 * PR #21). All endpoints are JWT-auth'd — the Supabase access token is
 * attached as a Bearer header; user_id is derived server-side from it.
 *
 * Locked vocabularies (do NOT invent new values — backend CHECKs enforce):
 *   CAMPAIGN_STATUSES     draft | running | paused | completed
 *   CONTACT_CALL_STATUSES queued | dialing | completed | failed | skipped | dnd
 *   OUTCOMES              connected | no_answer | busy | voicemail |
 *                         callback_requested | not_interested | dnd | failed
 */

import { supabase } from '@/services/supabaseClient.js';
import env from '@/config/env.js';

/** Locked vocabularies — mirrored verbatim from backend campaign_service.py */
export const CAMPAIGN_STATUSES = ['draft', 'running', 'paused', 'completed'];
export const CONTACT_CALL_STATUSES = ['queued', 'dialing', 'completed', 'failed', 'skipped', 'dnd'];
export const OUTCOMES = [
    'connected',
    'no_answer',
    'busy',
    'voicemail',
    'callback_requested',
    'not_interested',
    'dnd',
    'failed',
];

const API_BASE = `${env.backendUrl}/api/v1/campaigns`;

// ── Auth + transport helpers ─────────────────────────────────────────────────

async function _authHeaders(extra = {}) {
    const { data } = await supabase.auth.getSession();
    const token = data?.session?.access_token;
    return {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...extra,
    };
}

/**
 * Performs a fetch against the campaigns API.
 * @returns {Promise<{ ok: boolean, status: number, data: any, error: string|null }>}
 */
async function _request(path, { method = 'GET', body, headers = {}, isForm = false } = {}) {
    let res;
    try {
        res = await fetch(`${API_BASE}${path}`, {
            method,
            headers: await _authHeaders(headers),
            body: isForm ? body : body !== undefined ? JSON.stringify(body) : undefined,
        });
    } catch (err) {
        return {
            ok: false,
            status: 0,
            data: null,
            error: `Network error — is the backend running at ${env.backendUrl}?`,
        };
    }

    let data = null;
    const text = await res.text();
    if (text) {
        try { data = JSON.parse(text); } catch { data = null; }
    }

    if (!res.ok) {
        // FastAPI errors: { detail: "..." | [{msg, loc, ...}] }
        let message = `Request failed (${res.status})`;
        if (typeof data?.detail === 'string') message = data.detail;
        else if (Array.isArray(data?.detail)) {
            message = data.detail.map((d) => d.msg ?? JSON.stringify(d)).join('; ');
        }
        return { ok: false, status: res.status, data, error: message };
    }
    return { ok: true, status: res.status, data, error: null };
}

// ── CRUD ─────────────────────────────────────────────────────────────────────

/**
 * Creates a campaign.
 * @param {{ name: string, agent_id: string, objective?: string }} payload
 * @returns {Promise<{data: object|null, error: string|null}>} CampaignOut (201)
 */
export async function createCampaign(payload) {
    const res = await _request('', { method: 'POST', body: payload });
    return { data: res.data, error: res.error };
}

/**
 * Lists caller's campaigns, newest first. Optional status filter + cursor.
 * @returns {Promise<{data: object[]|null, error: string|null}>}
 */
export async function listCampaigns({ status, cursor, limit = 50 } = {}) {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (cursor) params.set('cursor', cursor);
    params.set('limit', String(limit));
    const res = await _request(`?${params.toString()}`);
    return { data: res.data, error: res.error };
}

/**
 * Campaign detail with live counters.
 * @returns {Promise<{data: object|null, error: string|null}>}
 *   data.counters = { total, by_status, by_outcome, pending, finished, connected_pct }
 */
export async function getCampaign(campaignId) {
    const res = await _request(`/${encodeURIComponent(campaignId)}`);
    return { data: res.data, error: res.error };
}

/**
 * PATCH name / objective / schedule / status (pause via 'paused', resume via 'running').
 * @returns {Promise<{data: object|null, error: string|null}>}
 */
export async function updateCampaign(campaignId, patch) {
    const res = await _request(`/${encodeURIComponent(campaignId)}`, { method: 'PATCH', body: patch });
    return { data: res.data, error: res.error };
}

// ── Contacts ─────────────────────────────────────────────────────────────────

/**
 * CSV upload. Server parses with per-row validation errors SURFACED (never
 * swallowed): valid rows are kept, invalid rows reported individually.
 *
 * @param {string} campaignId
 * @param {File|string} fileOrText — File/Blob or raw CSV text
 * @returns {Promise<{data: {
 *   added: number, duplicates_merged: number, total_rows_parsed: number,
 *   errors: Array<{ row: number|null, phone?: string, error: string }>
 * }|null, error: string|null}>}
 */
export async function uploadContacts(campaignId, fileOrText) {
    const form = new FormData();
    if (fileOrText instanceof Blob) {
        form.append('file', fileOrText, fileOrText.name || 'contacts.csv');
    } else {
        form.append('file', new Blob([String(fileOrText)], { type: 'text/csv' }), 'contacts.csv');
    }
    const res = await _request(
        `/${encodeURIComponent(campaignId)}/contacts`,
        { method: 'POST', body: form, isForm: true },
    );
    return { data: res.data, error: res.error };
}

/**
 * Paginated contact list with call status + locked-vocab outcome.
 * @returns {Promise<{data: { items: Array<{
 *   id, status, attempts, last_attempted_at, outcome, outcome_notes, phone, name, dnd
 * }>, limit, offset }|null, error: string|null}>}
 */
export async function getContacts(campaignId, { status, limit = 200, offset = 0 } = {}) {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    const res = await _request(
        `/${encodeURIComponent(campaignId)}/contacts?${params.toString()}`,
    );
    return { data: res.data, error: res.error };
}

/**
 * Removes a contact from a campaign.
 * @returns {Promise<{error: string|null}>}
 */
export async function removeContact(campaignId, contactId) {
    const res = await _request(
        `/${encodeURIComponent(campaignId)}/contacts/${encodeURIComponent(contactId)}`,
        { method: 'DELETE' },
    );
    return { error: res.error };
}

// ── Lifecycle: start / stop / simulate ───────────────────────────────────────

/**
 * Validates (agent published, ≥1 contact, schedule window), enqueues, sets running.
 * @returns {Promise<{data: { ok, id, status, queued }|null, error: string|null}>}
 */
export async function startCampaign(campaignId) {
    const res = await _request(`/${encodeURIComponent(campaignId)}/start`, { method: 'POST' });
    return { data: res.data, error: res.error };
}

/**
 * Stops queue drain (→ paused). Resume with startCampaign or PATCH status='running'.
 * @returns {Promise<{data: { ok, id, status }|null, error: string|null}>}
 */
export async function stopCampaign(campaignId) {
    const res = await _request(`/${encodeURIComponent(campaignId)}/stop`, { method: 'POST' });
    return { data: res.data, error: res.error };
}

/**
 * v1 demo mode: simulated browser calls through the same LLM pipeline as WS
 * text_input (no PSTN/SIP). Drains the queue synchronously; returns outcomes +
 * live counters when done. Can take several seconds per call — UI must show
 * progress state while awaiting.
 *
 * @returns {Promise<{data: {
 *   ok, id, simulated_calls, status,
 *   outcomes: Array<{ campaign_contact_id, outcome }>,
 *   counters: { total, by_status, by_outcome, pending, finished, connected_pct }
 * }|null, error: string|null}>}
 */
export async function simulateCampaign(campaignId) {
    const res = await _request(`/${encodeURIComponent(campaignId)}/simulate`, { method: 'POST' });
    return { data: res.data, error: res.error };
}
