/**
 * src/components/Campaigns.js
 * ───────────────────────────
 * Outbound campaigns UI (issue #8).
 *
 * Flow: create campaign → upload contacts CSV (per-row errors SHOWN, valid
 * rows kept) → start/stop → v1 simulate (backend simulated calls over the
 * same LLM pipeline as WS text_input) → live counters + outcomes view using
 * the LOCKED vocabulary. Refresh preserves state via location.hash (G8).
 *
 * No SIP/PSTN affordances anywhere in v1 (ruling B4).
 */

import {
    CAMPAIGN_STATUSES,
    OUTCOMES,
    listCampaigns,
    getCampaign,
    createCampaign,
    updateCampaign,
    getContacts,
    uploadContacts,
    removeContact,
    startCampaign,
    stopCampaign,
    simulateCampaign,
} from '@/services/campaignService.js';
import { showToast } from '@/utils/toast.js';
import { $, $$, escHtml } from '@/utils/dom.js';

// ═══════════════════════════════════════════════════════════════
//  State
// ═══════════════════════════════════════════════════════════════
let _campaigns = [];
let _current = null;          // full detail row incl. counters
let _contacts = [];           // contact rows for the current campaign
let _pollTimer = null;
let _simulating = false;

const POLL_MS = 5_000; // live counters refresh while a campaign is active

// ═══════════════════════════════════════════════════════════════
//  Badge helpers — LOCKED vocab only
// ═══════════════════════════════════════════════════════════════

function campaignStatusClass(status) {
    return { running: 'badge-green', paused: 'badge-orange', completed: 'badge-gray' }[status] ?? 'badge-gray';
}

function callStatusClass(status) {
    return {
        queued: 'badge-gray', dialing: 'badge-blue',
        completed: 'badge-green', failed: 'badge-red',
        skipped: 'badge-gray', dnd: 'badge-gray',
    }[status] ?? 'badge-gray';
}

function outcomeClass(outcome) {
    return {
        connected: 'badge-green',
        callback_requested: 'badge-blue',
        no_answer: 'badge-orange', busy: 'badge-orange', voicemail: 'badge-orange',
        not_interested: 'badge-red', dnd: 'badge-red', failed: 'badge-red',
    }[outcome] ?? 'badge-gray';
}

const prettyLabel = (v) => String(v ?? '').replaceAll('_', ' ');

// ═══════════════════════════════════════════════════════════════
//  Hash routing (G8: refresh preserves state)
// ═══════════════════════════════════════════════════════════════

function _setHash(campaignId = null) {
    const hash = campaignId ? `#outbound/${campaignId}` : '#outbound';
    if (window.location.hash !== hash) history.replaceState(null, '', hash);
}

/** Parses #outbound[/<campaignId>] → campaignId|null */
export function parseCampaignHash() {
    const m = window.location.hash.match(/^#outbound(?:\/([0-9a-fA-F-]{8,}))?$/);
    return m?.[1] ?? null;
}

// ═══════════════════════════════════════════════════════════════
//  List view
// ═══════════════════════════════════════════════════════════════

export async function loadCampaigns({ preserveDetail = false } = {}) {
    const { data, error } = await listCampaigns();
    if (error) {
        showToast('❌ ' + error, 'error');
        return;
    }
    _campaigns = data ?? [];

    // Re-enter detail if hash points at one (G8), unless explicitly leaving.
    const fromHash = parseCampaignHash();
    if (!preserveDetail && fromHash && fromHash !== _current?.id) {
        await openCampaign(fromHash);
        return;
    }
    if (!_current || !_campaigns.some((c) => c.id === _current.id)) {
        renderList();
    }
}

function renderList() {
    const tbody = $('#campaign-tbody');
    if (!tbody) return;
    _stopPolling();
    $('#campaign-detail')?.style.setProperty('display', 'none');
    $('#campaign-list-wrap')?.style.removeProperty('display');

    if (!_campaigns.length) {
        tbody.innerHTML = `
            <tr><td colspan="6" style="text-align:center;padding:32px;color:var(--dash-text-3);">
                No campaigns yet.
                <a href="#" onclick="openCreateCampaign();return false;" style="color:var(--dash-accent);">Create your first campaign →</a>
            </td></tr>`;
        return;
    }

    tbody.innerHTML = _campaigns.map((c) => `
        <tr class="campaign-row" tabindex="0" role="button"
            aria-label="Open campaign ${escHtml(c.name)}"
            onclick="openCampaign('${c.id}')"
            onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openCampaign('${c.id}');}">
            <td>
                <span style="font-weight:600;color:var(--dash-text);">${escHtml(c.name)}</span>
                ${c.objective ? `<div style="font-size:11px;color:var(--dash-text-3);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escHtml(c.objective)}</div>` : ''}
            </td>
            <td style="font-size:12px;color:var(--dash-text-2);">${escHtml(_agentNameFor(c.agent_id))}</td>
            <td><span class="badge ${campaignStatusClass(c.status)}">${escHtml(prettyLabel(c.status))}</span></td>
            <td colspan="3" style="font-size:12px;color:var(--dash-text-3);">
                ${_campaigns.length ? 'Open for counters & calls →' : ''}
            </td>
        </tr>`).join('');
}

/** Resolves an agent display name from the dashboard's loaded agents. */
function _agentNameFor(agentId) {
    if (!agentId) return '—';
    const agents = window.__AGENTS__ ?? [];
    return agents.find((a) => a.id === agentId)?.name ?? agentId.slice(0, 8);
}

// ═══════════════════════════════════════════════════════════════
//  Detail view (live counters + contacts/outcomes + lifecycle)
// ═══════════════════════════════════════════════════════════════

export async function openCampaign(campaignId) {
    const { data, error } = await getCampaign(campaignId);
    if (error) {
        showToast('❌ ' + error, 'error');
        renderList();
        return;
    }
    _current = data;
    _setHash(campaignId);
    $('#campaign-list-wrap')?.style.setProperty('display', 'none');
    $('#campaign-detail')?.style.removeProperty('display');
    renderDetail();

    const contactsRes = await getContacts(campaignId);
    _contacts = contactsRes.data?.items ?? [];
    renderContacts();
    _startPolling();
}

export function closeCampaign() {
    _current = null;
    _contacts = [];
    _setHash(null);
    _stopPolling();
    renderList();
}

function renderDetail() {
    const c = _current;
    if (!c) return;

    setTextSafe('#camp-name', c.name);
    setTextSafe('#camp-objective', c.objective || '');
    setTextSafe('#camp-agent', _agentNameFor(c.agent_id));

    const statusEl = $('#camp-status');
    if (statusEl) {
        statusEl.className = `badge ${campaignStatusClass(c.status)}`;
        statusEl.textContent = prettyLabel(c.status);
    }

    renderCounters(c.counters ?? {});
    renderLifecycleButtons(c.status);
}

function renderCounters(counters) {
    setTextSafe('#cnt-total', String(counters.total ?? 0));
    setTextSafe('#cnt-pending', String(counters.pending ?? 0));
    setTextSafe('#cnt-finished', String(counters.finished ?? 0));
    setTextSafe('#cnt-connected', `${counters.connected_pct ?? 0}%`);

    const grid = $('#outcome-grid');
    if (!grid) return;
    // LOCKED vocabulary order — never invent outcomes client-side (B3/B4).
    const byOutcome = counters.by_outcome ?? {};
    grid.innerHTML = OUTCOMES.map((o) => `
        <div class="outcome-cell">
            <span class="badge ${outcomeClass(o)}">${escHtml(prettyLabel(o))}</span>
            <strong>${Number(byOutcome[o] ?? 0).toLocaleString()}</strong>
        </div>`).join('');
}

function renderLifecycleButtons(status) {
    const startBtn = $('#camp-start-btn');
    const stopBtn = $('#camp-stop-btn');
    const simBtn = $('#camp-simulate-btn');
    if (!startBtn || !stopBtn || !simBtn) return;

    // Mirrors backend transitions: start from draft|paused, stop from running,
    // simulate from draft|paused. Completed is terminal.
    startBtn.style.display = ['draft', 'paused'].includes(status) ? '' : 'none';
    stopBtn.style.display = status === 'running' ? '' : 'none';
    simBtn.style.display = ['draft', 'paused'].includes(status) ? '' : 'none';
    simBtn.disabled = _simulating;
    simBtn.textContent = _simulating ? 'Simulating… (calls in progress)' : '▶ Simulate calls';
}

// ── Contacts table + CSV upload ──────────────────────────────────────────────

function renderContacts() {
    const tbody = $('#contact-tbody');
    if (!tbody) return;
    if (!_contacts.length) {
        tbody.innerHTML = `
            <tr><td colspan="4" style="text-align:center;padding:24px;color:var(--dash-text-3);">
                No contacts yet. Upload a CSV above — one phone per row.
            </td></tr>`;
        return;
    }
    tbody.innerHTML = _contacts.map((r) => `
        <tr>
            <td style="font-family:var(--font-mono);font-size:12px;">${escHtml(r.phone ?? '')}</td>
            <td>${escHtml(r.name ?? '—')}</td>
            <td><span class="badge ${callStatusClass(r.status)}">${escHtml(prettyLabel(r.status))}</span></td>
            <td>${r.outcome
                ? `<span class="badge ${outcomeClass(r.outcome)}" title="${escHtml(r.outcome_notes ?? '')}">${escHtml(prettyLabel(r.outcome))}</span>`
                : '<span style="color:var(--dash-text-3);">—</span>'}</td>
            <td>
                <button class="agent-action-btn agent-action-btn--delete"
                    onclick="removeCampaignContact('${_current.id}','${r.id}')"
                    aria-label="Remove contact ${escHtml(r.phone ?? r.id)}">✕</button>
            </td>
        </tr>`).join('');
}

/**
 * CSV upload handler. Per-row errors are SHOWN to the user, never swallowed:
 * invalid rows are listed individually with row number + reason; valid rows
 * are kept (server upserts them even when siblings fail — G3).
 */
export async function uploadCsv() {
    if (!_current) return;
    const input = $('#csv-file-input');
    const file = input?.files?.[0];
    if (!file) {
        showToast('⚠️ Choose a .csv file first', 'warning');
        input?.focus();
        return;
    }
    setUploadBusy(true);
    const { data, error } = await uploadContacts(_current.id, file);
    setUploadBusy(false);
    if (input) input.value = '';

    if (error) {
        showToast('❌ ' + error, 'error');
        return;
    }
    showUploadReport(data);
    await refreshCurrent();
}

function setUploadBusy(busy) {
    const btn = $('#csv-upload-btn');
    if (btn) {
        btn.disabled = busy;
        btn.textContent = busy ? 'Uploading…' : 'Upload CSV';
    }
}

/** Renders the per-row upload result panel (added / merged / each error row). */
function showUploadReport(result) {
    const panel = $('#csv-report');
    if (!panel) return;
    const errors = result?.errors ?? [];
    const parts = [
        `<div style="font-weight:600;margin-bottom:6px;">
            ✅ ${result.added} added · ♻️ ${result.duplicates_merged} already existed
            <span style="color:var(--dash-text-3);font-weight:400;">(${result.total_rows_parsed} rows parsed)</span>
        </div>`,
    ];
    if (errors.length) {
        parts.push(`
            <div role="alert" style="margin-top:8px;">
                <div style="font-weight:600;color:#b45309;margin-bottom:4px;">⚠️ ${errors.length} row${errors.length > 1 ? 's' : ''} rejected:</div>
                <ul style="margin:0;padding-left:18px;font-size:12px;line-height:1.7;">
                    ${errors.map((e) => `<li>Row ${e.row ?? '?'}${e.phone ? ` (${escHtml(e.phone)})` : ''}: ${escHtml(e.error)}</li>`).join('')}
                </ul>
                <div style="font-size:11px;color:var(--dash-text-3);margin-top:4px;">Valid rows were kept and added.</div>
            </div>`);
    }
    panel.innerHTML = parts.join('');
    panel.style.display = 'block';
}

export function dismissCsvReport() {
    const panel = $('#csv-report');
    if (panel) { panel.innerHTML = ''; panel.style.display = 'none'; }
}

// ── Lifecycle actions ────────────────────────────────────────────────────────

export async function startCurrentCampaign() {
    if (!_current) return;
    const { data, error } = await startCampaign(_current.id);
    if (error) { showToast('❌ ' + error, 'error'); return; }
    showToast(`🚀 Campaign started — ${data.queued} contact${data.queued === 1 ? '' : 's'} queued`, 'success');
    await refreshCurrent();
}

export async function stopCurrentCampaign() {
    if (!_current) return;
    const { error } = await stopCampaign(_current.id);
    if (error) { showToast('❌ ' + error, 'error'); return; }
    showToast('⏸️ Campaign paused', 'success');
    await refreshCurrent();
}

/**
 * v1 simulate (B4): backend runs simulated browser calls through the same LLM
 * pipeline as WS text_input. This is a synchronous endpoint that can take
 * several seconds — we poll-free await it while showing progress, then show
 * transcript excerpts (outcome_notes) + locked-vocab outcomes ≤15s post-call.
 */
export async function simulateCurrentCampaign() {
    if (!_current || _simulating) return;
    _simulating = true;
    renderLifecycleButtons(_current.status);
    const startedAt = Date.now();

    try {
        const { data, error } = await simulateCampaign(_current.id);
        _simulating = false;
        if (error) { showToast('❌ ' + error, 'error'); }
        else {
            const secs = Math.round((Date.now() - startedAt) / 1000);
            showToast(`📞 Simulated ${data.simulated_calls} call${data.simulated_calls === 1 ? '' : 's'} in ${secs}s`, 'success');
        }
    } catch {
        _simulating = false;
        showToast('❌ Simulation failed — check the backend connection', 'error');
    }
    await refreshCurrent();
}

export async function removeCampaignContact(contactId) {
    if (!_current) return;
    const { error } = await removeContact(_current.id, contactId);
    if (error) { showToast('❌ ' + error, 'error'); return; }
    _contacts = _contacts.filter((r) => r.id !== contactId);
    renderContacts();
    showToast('🗑️ Contact removed');
}

// ── Live polling (counters + statuses while detail is open) ──────────────────

function _startPolling() {
    _stopPolling();
    _pollTimer = setInterval(async () => {
        if (_simulating || !_current) return; // simulate response carries final state
        await refreshCurrent({ silent: true });
    }, POLL_MS);
}

function _stopPolling() {
    clearInterval(_pollTimer);
    _pollTimer = null;
}

async function refreshCurrent({ silent = false } = {}) {
    if (!_current) return;
    const { data, error } = await getCampaign(_current.id);
    if (error) {
        if (!silent) showToast('❌ ' + error, 'error');
        return;
    }
    _current = data;
    renderDetail();

    const contactsRes = await getContacts(_current.id);
    if (!contactsRes.error) {
        _contacts = contactsRes.data?.items ?? [];
        renderContacts();
    }
    if (!silent && data.counters) showToast('🔄 Counters refreshed', 'info');
}

// ═══════════════════════════════════════════════════════════════
//  Create-campaign modal
// ═══════════════════════════════════════════════════════════════

export function openCreateModal() {
    populateAgentSelect();
    $('#create-campaign-modal')?.classList.add('open');
    setTimeout(() => $('#new-campaign-name')?.focus(), 300);
}

export function closeCreateModal() {
    $('#create-campaign-modal')?.classList.remove('open');
    const nameInput = $('#new-campaign-name');
    const objInput = $('#new-campaign-objective');
    if (nameInput) nameInput.value = '';
    if (objInput) objInput.value = '';
}

function populateAgentSelect() {
    const select = $('#new-campaign-agent');
    if (!select) return;
    const agents = window.__AGENTS__ ?? [];
    select.innerHTML = agents.length
        ? agents.map((a) => `
            <option value="${a.id}">${escHtml(a.name)}${a.status === 'published' ? '' : ' (draft)'}</option>`).join('')
        : '<option value="">No agents found — create one first</option>';
}

export async function submitCreateCampaign() {
    const name = ($('#new-campaign-name')?.value ?? '').trim();
    const objective = ($('#new-campaign-objective')?.value ?? '').trim();
    const agentId = $('#new-campaign-agent')?.value ?? '';

    if (!name) { showToast('⚠️ Enter a campaign name', 'warning'); $('#new-campaign-name')?.focus(); return; }
    if (!agentId) { showToast('⚠️ Pick an agent for this campaign', 'warning'); $('#new-campaign-agent')?.focus(); return; }

    const btn = $('#create-campaign-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }

    const { data, error } = await createCampaign({ name, agent_id: agentId, objective: objective || null });

    if (btn) { btn.disabled = false; btn.textContent = 'Create Campaign →'; }
    if (error) { showToast('❌ ' + error, 'error'); return; }

    closeCreateModal();
    showToast(`✅ Campaign "${name}" created — now add contacts`, 'success');
    await loadCampaigns({ preserveDetail: true });
    await openCampaign(data.id);
}

function setTextSafe(selector, text) {
    const el = $(selector);
    if (el) el.textContent = text;
}

// ═══════════════════════════════════════════════════════════════
//  Teardown (sign-out / page unload)
// ═══════════════════════════════════════════════════════════════

export function teardownCampaigns() {
    _stopPolling();
    _current = null;
    _contacts = [];
}
