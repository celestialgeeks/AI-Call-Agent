/**
 * src/pages/app.js
 * ──────────────────
 * Entry point for app.html (dashboard).
 * This is the orchestrator — it wires services, components, and the live ticker.
 * No raw Supabase calls or DOM strings live here; all delegated to their layers.
 */

// ── Services ──────────────────────────────────────────────────
import { getSession } from '@/services/authService.js';
import * as authService from '@/services/authService.js';
import { getAgents, createAgent, deleteAgent } from '@/services/agentService.js';
import { getConversations, addConversation, addKnowledgeDoc, getPhoneNumbers, deletePhoneNumber, getKnowledgeDocs, getTools } from '@/services/conversationService.js';
import { getDailyStats, seedUserData, getProfile } from '@/services/analyticsService.js';
import { subscribeToConversations, subscribeToAgents, unsubscribeAll } from '@/services/realtimeService.js';

// ── Components ────────────────────────────────────────────────
import { navigate, switchConfigTab, switchPeriodTab } from '@/components/Sidebar.js';
import { openCreateModal, closeCreateModal, selectTemplate, readCreateAgentForm } from '@/components/Modal.js';
import { openPlayground, closePlayground, togglePlaygroundCall, sendPlaygroundMessage } from '@/components/Playground.js';
import { drawLineChart } from '@/components/Chart.js';
import { playPreview, stopPreview } from '@/components/VoicePreview.js';

// ── Config ────────────────────────────────────────────────────
import { estimateCallCost, formatINR } from '@/config/pricing.js';

// ── Utils ─────────────────────────────────────────────────────
import { formatDuration, timeAgo, formatBytes, statusBadgeClass } from '@/utils/formatters.js';
import { showToast } from '@/utils/toast.js';
import { $, $$, escHtml, setText, getInitials } from '@/utils/dom.js';

// ═══════════════════════════════════════════════════════════════
//  App State
// ═══════════════════════════════════════════════════════════════
let _user = null;
let _profile = null;
let _agents = [];
let _convs = [];
let _phones = [];
let _docs = [];
let _tools = [];
let _stats = [];
let _statsError = false;   // true ⇒ analytics fetch FAILED — render error state, never zeros (ADR-0001)
let _tickerTimeout = null;

// ═══════════════════════════════════════════════════════════════
//  Boot
// ═══════════════════════════════════════════════════════════════
async function boot() {
  // 1. Auth guard
  const session = await getSession();
  if (!session) { window.location.replace('/auth.html?mode=login'); return; }
  _user = session.user;
  window.__USER_ID__ = _user.id;


  // 2. Profile
  _profile = await getProfile(_user.id);
  _hydrateUserUI();

  // 3. Seed data for new users (no-op if rows already exist)
  await seedUserData(_user.id);

  // 4. Parallel data fetch — analytics errors are captured, not swallowed
  const statsResult = await getDailyStats(_user.id, 30)
    .then((rows) => ({ rows, error: null }))
    .catch((err) => {
      console.error('[app] getDailyStats failed', err);
      return { rows: null, error: err };
    });
  _statsError = statsResult.error !== null || statsResult.rows === null;
  _stats = statsResult.rows ?? [];

  [_agents, _convs, _phones, _docs, _tools] = await Promise.all([
    getAgents(_user.id),
    getConversations(_user.id),
    getPhoneNumbers(_user.id),
    getKnowledgeDocs(_user.id),
    getTools(_user.id),
  ]);

  // 5. Render home page & initial components
  _initHomePage();
  _renderAgents();
  _renderVoices();
  _renderPhoneNumbers();

  // 6. Realtime subscriptions
  _setupRealtime();

  // 7. Live call ticker
  _startLiveTicker();
}

// ═══════════════════════════════════════════════════════════════
//  User Hydration
// ═══════════════════════════════════════════════════════════════
function _hydrateUserUI() {
  const name = _profile?.full_name
    || _user.user_metadata?.full_name
    || _user.email?.split('@')[0]
    || 'User';
  const email = _user.email ?? '';
  const initials = getInitials(name);
  const avatar = _profile?.avatar_url || _user.user_metadata?.avatar_url;
  const firstName = name.split(' ')[0];

  // Workspace name + avatar
  const wsAvatarEl = $('#ws-avatar');
  const wsNameEl = $('#ws-name');
  if (wsAvatarEl) {
    if (avatar) { wsAvatarEl.style.cssText += `background-image:url(${avatar});background-size:cover;`; wsAvatarEl.textContent = ''; }
    else wsAvatarEl.textContent = initials;
  }
  setText(wsNameEl, `${firstName}'s Workspace`);

  // Sidebar user card
  const avatarEl = $('#user-avatar-el');
  const nameEl = $('#user-name-el');
  const emailEl = $('#user-email-el');
  if (avatarEl) {
    if (avatar) { avatarEl.style.cssText += `background-image:url(${avatar});background-size:cover;`; avatarEl.textContent = ''; }
    else avatarEl.textContent = initials;
  }
  setText(nameEl, name);
  setText(emailEl, email);

  // Greeting
  const hour = new Date().getHours();
  const period = hour < 12 ? 'morning' : hour < 17 ? 'afternoon' : 'evening';
  setText($('.home-greeting'), `Good ${period}, ${firstName} ☀️`);
}

// ═══════════════════════════════════════════════════════════════
//  Home Page
// ═══════════════════════════════════════════════════════════════
function _initHomePage() {
  _updateHomeStats();
  _renderRecentTable();
  _initHomeChart('week');
}

// ── Home stats: four states, zero fabrication (ADR-0001) ─────
// loading → skeletons (in markup) | populated → real values |
// zero → real zeros | error → retry banner, NEVER zeros.

function _setHomeStatValue(statId, valueText) {
  const card = $(statId);
  if (!card) return;
  const valEl = card.querySelector('.stat-value');
  if (!valEl) return;
  valEl.innerHTML = '';
  valEl.textContent = valueText;
}

function _setHomeStatError(statId) {
  const card = $(statId);
  if (!card) return;
  const valEl = card.querySelector('.stat-value');
  if (valEl) {
    valEl.innerHTML = '';
    const dash = document.createElement('span');
    dash.className = 'stat-unknown';
    dash.textContent = '—';
    dash.title = 'Unknown — data could not be loaded';
    valEl.appendChild(dash);
  }
}

function _updateHomeStats() {
  // ERROR state: fetch failed ⇒ unknown, never zeros (spec v1 §1).
  if (_statsError && !_convs.length) {
    ['home-stat-calls', 'home-stat-duration', 'home-stat-success', 'home-stat-cost'].forEach(_setHomeStatError);
    return;
  }

  // Today's conversations are the live source for the four cards.
  const today = new Date().toDateString();
  const todayConvs = _convs.filter((c) => new Date(c.created_at).toDateString() === today);
  const resolvedToday = todayConvs.filter((c) => c.status === 'resolved').length;

  _setHomeStatValue('#home-stat-calls', todayConvs.length.toLocaleString('en-IN'));

  const avgSec = todayConvs.length
    ? Math.round(todayConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0) / todayConvs.length)
    : 0;
  _setHomeStatValue('#home-stat-duration', formatDuration(avgSec));

  // Real measured success rate; real 0% when calls exist but none resolved.
  const successRate = todayConvs.length
    ? ((resolvedToday / todayConvs.length) * 100).toFixed(1)
    : null;
  _setHomeStatValue('#home-stat-success', successRate === null ? '—' : `${successRate}%`);

  // Cost comes ONLY from pricing.js — unfilled rates ⇒ "—" (spec v1 §4).
  const totalSecToday = todayConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0);
  const estCost = estimateCallCost({ seconds: totalSecToday, ttsChars: null, llmTokensIn: null, llmTokensOut: null });
  _setHomeStatValue('#home-stat-cost', formatINR(estCost));
}

function _renderRecentTable() {
  const tbody = $('#recent-tbody');
  if (!tbody) return;
  const palette = ['#7C5CFC', '#00C4A1', '#f59e0b', '#ef4444', '#6366f1'];
  tbody.innerHTML = _convs.slice(0, 5).map((c, i) => `
    <tr>
      <td>
        <div class="caller-info">
          <div class="caller-avatar" style="background:${palette[i % palette.length]}">${(c.caller_name ?? '?').charAt(0)}</div>
          <div>
            <div class="caller-name">${escHtml(c.caller_name ?? 'Unknown')}</div>
            <div class="caller-num">${escHtml(c.caller_number ?? '')}</div>
          </div>
        </div>
      </td>
      <td>${escHtml(c.agent_name ?? '—')}</td>
      <td>${formatDuration(c.duration_sec)}</td>
      <td><span class="badge ${statusBadgeClass(c.status)}">${escHtml(_OUTCOME_LABELS[c.status] ?? c.status)}</span></td>
      <td style="color:var(--dash-text-3);font-size:12px;">${timeAgo(c.created_at)}</td>
    </tr>
  `).join('');
}

// ── Charts ────────────────────────────────────────────────────
function _buildChartData(period) {
  // No real data ⇒ empty array → caller renders the .empty-chart block.
  // NEVER fabricate a series (ADR-0001).
  if (_statsError || !_stats.length) return [];
  const sorted = [..._stats].sort((a, b) => a.date.localeCompare(b.date));
  if (period === 'week') return sorted.slice(-7).map((d) => d.total_calls);
  if (period === 'month') return sorted.slice(-30).map((d) => d.total_calls);
  return sorted.slice(-12).map((d) => Math.round(d.total_calls / 8));
}

const _EMPTY_CHART_HTML = `
  <div class="empty-chart-icon">
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 16.25v-3.5M12 16.25v-8M17.5 16.25v-5.75"/><path d="M4.25 19.75h15.5"/></svg>
  </div>
  <div class="empty-chart-title">No calls yet</div>
  <div class="empty-chart-sub">Metrics appear after your agents handle their first call.</div>`;

function _renderChart(canvasId, data, color) {
  const canvas = document.getElementById(canvasId);
  const wrap = canvas?.parentElement;
  if (!canvas || !wrap) return;

  const tabs = wrap.parentElement.querySelectorAll('.chart-period-tab');
  let emptyBlock = wrap.querySelector('.empty-chart');

  if (!data.length) {
    // Zero-state: hide canvas, show dashed empty block beside it; tabs stay visible but inert.
    canvas.style.display = 'none';
    if (!emptyBlock) {
      emptyBlock = document.createElement('div');
      emptyBlock.className = 'empty-chart';
      emptyBlock.innerHTML = _EMPTY_CHART_HTML;
      wrap.appendChild(emptyBlock);
    }
    emptyBlock.style.display = '';
    tabs.forEach((t) => t.setAttribute('disabled', ''));
    return;
  }

  canvas.style.display = '';
  emptyBlock?.remove();
  tabs.forEach((t) => t.removeAttribute('disabled'));
  drawLineChart(canvasId, data, color);
}

function _initHomeChart(period = 'week') {
  requestAnimationFrame(() => _renderChart('callChart', _buildChartData(period)));
}

function _initAnalyticsChart() {
  requestAnimationFrame(() => {
    _renderChart('analyticsChart', _buildChartData('month'), '#00C4A1');
    _renderAnalyticsKpis();
    _renderAnalyticsBreakdown();
    _renderEstimateCard();
  });
}

// ── Analytics KPIs (spec v1 §1): Calls · Avg Duration · Latency p50 · Error Rate · Est. Cost
function _setKpi(cardIndex, valueText) {
  const cards = $$('#analytics-kpis .stat-card');
  const card = cards[cardIndex];
  if (!card) return;
  const valEl = card.querySelector('.stat-value');
  if (valEl) { valEl.innerHTML = ''; valEl.textContent = valueText; }
}

function _renderAnalyticsKpis() {
  if (_statsError && !_convs.length && !_stats.length) {
    ['—', '—', '—', '—', '—'].forEach((v, i) => _setKpi(i, v));
    return;
  }
  const calls = _stats.reduce((s, d) => s + (d.total_calls ?? 0), 0);
  _setKpi(0, calls.toLocaleString('en-IN'));

  const weighted = _stats.reduce((s, d) => s + (d.avg_duration_sec ?? 0) * (d.total_calls ?? 0), 0);
  const avgDur = calls ? Math.round(weighted / calls) : 0;
  _setKpi(1, formatDuration(avgDur));

  // Latency p50: no samples in the schema yet ⇒ em-dash, NOT 0ms (spec v1 §1).
  _setKpi(2, '— ms');

  const errored = _stats.reduce((s, d) => s + (d.missed ?? 0) + (d.escalated ?? 0), 0);
  const errRate = calls ? ((errored / calls) * 100).toFixed(1) : '0.0';
  _setKpi(3, `${errRate}%`);

  // Period-scoped actuals via pricing.js — unfilled rates ⇒ "—" (spec v1 §4).
  const monthSec = _convs.reduce((s, c) => s + (c.duration_sec ?? 0), 0);
  const estCost = estimateCallCost({ seconds: monthSec, ttsChars: null, llmTokensIn: null, llmTokensOut: null });
  _setKpi(4, formatINR(estCost, { decimals: false }));
}

// ── Analytics breakdown table (By agent)
function _renderAnalyticsBreakdown() {
  const tbody = $('#analytics-breakdown-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (_statsError && !_agents.length && !_convs.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="5"><div class="error-banner"><span>⚠</span><span>Couldn't load analytics</span><button class="dash-btn dash-btn-outline error-retry" onclick="retryAnalytics()">Retry</button></div></td>`;
    tbody.appendChild(tr);
    return;
  }

  if (!_agents.length) {
    const tr = document.createElement('tr');
    tr.className = 'empty-row';
    tr.innerHTML = '<td colspan="5" class="table-empty-cell">No agents yet — create one to see per-agent usage.</td>';
    tbody.appendChild(tr);
    return;
  }

  _agents.forEach((a) => {
    const agentConvs = _convs.filter((c) => c.agent_id === a.id);
    const calls = agentConvs.length || (a.call_count ?? 0);
    const avgDur = agentConvs.length
      ? Math.round(agentConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0) / agentConvs.length)
      : (a.call_count ? null : 0);
    const errors = agentConvs.filter((c) => c.status === 'missed' || c.status === 'escalated').length;
    const cost = estimateCallCost({
      seconds: agentConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0),
      ttsChars: null, llmTokensIn: null, llmTokensOut: null,
    });

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escHtml(a.name)}</td>
      <td class="mono-num">${Number(calls).toLocaleString('en-IN')}</td>
      <td class="mono-num">${avgDur === null ? '—' : formatDuration(avgDur)}</td>
      <td class="mono-num">${errors}</td>
      <td class="mono-num">${formatINR(cost, { decimals: false })}</td>`;
    tbody.appendChild(tr);
  });
}

// ── Estimated-cost card (spec v1 §4B): always labeled "estimated"
function _renderEstimateCard() {
  const monthEl = $('#estimate-month');
  const perCallEl = $('#estimate-percall');
  if (!monthEl || !perCallEl) return;

  const monthSec = _convs.reduce((s, c) => s + (c.duration_sec ?? 0), 0);
  const monthCost = estimateCallCost({ seconds: monthSec, ttsChars: null, llmTokensIn: null, llmTokensOut: null });
  const perCallCost = _convs.length && monthCost !== null
    ? Math.round((monthCost / _convs.length) * 100) / 100
    : (monthCost === null ? null : 0);

  setText(monthEl, formatINR(monthCost, { decimals: false }));
  setText(perCallEl, formatINR(perCallCost));
}

// ═══════════════════════════════════════════════════════════════
//  Agents
// ═══════════════════════════════════════════════════════════════
function _renderAgents(filter = '') {
  const container = $('#agent-rows');
  if (!container) return;
  const filtered = _agents.filter((a) =>
    a.name.toLowerCase().includes(filter.toLowerCase())
  );
  const gradients = [
    ['#7C5CFC', '#a78bfa'], ['#00C4A1', '#34d399'],
    ['#f59e0b', '#fbbf24'], ['#6366f1', '#818cf8'],
  ];

  if (!filtered.length) {
    container.innerHTML = `<div style="padding:40px;text-align:center;color:var(--dash-text-3);grid-column:1/-1;">
      No agents found. <a href="#" onclick="openCreateAgent()" style="color:var(--dash-accent);">Create one →</a>
    </div>`;
    return;
  }

  container.innerHTML = filtered.map((a, i) => {
    const [c1, c2] = gradients[i % gradients.length];
    return `<div class="agent-row" onclick="editAgent('${a.id}','${escHtml(a.name)}')">
      <div class="agent-name-cell">
        <div class="agent-avatar" style="background:linear-gradient(135deg,${c1},${c2})">${a.icon ?? '🤖'}</div>
        <div>
          <div class="agent-name">${escHtml(a.name)}</div>
          <div class="agent-id">ag_${a.id.slice(0, 8)}</div>
        </div>
      </div>
      <span style="font-size:13px;color:var(--dash-text-2);">${escHtml(a.voice_name ?? 'Priya')} · ${escHtml(a.voice_lang ?? 'Hindi/EN')}</span>
      <span class="badge ${a.status === 'published' ? 'badge--green' : 'badge--gray'}">${a.status}</span>
      <span style="font-size:13px;color:var(--dash-text-2);">${(a.call_count ?? 0).toLocaleString()}</span>
      <span style="font-size:12px;color:var(--dash-text-3);font-family:var(--font-mono);">${timeAgo(a.created_at)}</span>
      <div class="agent-actions">
        <button class="agent-action-btn agent-action-btn--edit" onclick="event.stopPropagation();editAgent('${a.id}','${escHtml(a.name)}')">Edit</button>
        <button class="agent-action-btn agent-action-btn--delete" onclick="event.stopPropagation();removeAgent('${a.id}')">Delete</button>
      </div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════════════════
//  Conversations — spec v1 §3 (transcript · duration · outcome · est. cost)
// ═══════════════════════════════════════════════════════════════

// Outcome badges map 1:1 from backend status (conversations.status CHECK
// constraint: resolved | escalated | missed | in_progress) — never inferred.
const OUTCOME_BADGES = {
  resolved:    { cls: 'badge-green',  label: 'Completed' },
  escalated:   { cls: 'badge-orange', label: 'Escalated', pulse: true },
  missed:      { cls: 'badge-red',    label: 'Failed' },
  in_progress: { cls: 'badge-orange', label: 'In progress', pulse: true },
};

const _OUTCOME_LABELS = Object.fromEntries(
  Object.entries(OUTCOME_BADGES).map(([status, meta]) => [status, meta.label])
);

function _outcomeBadge(status) {
  const known = OUTCOME_BADGES[status];
  if (!known) return '<span class="badge badge-gray">—</span>'; // unknown status ⇒ not guessed
  return `<span class="badge ${known.cls}${known.pulse ? ' badge-pulse' : ''}">${known.label}</span>`;
}

function _convEstCostCell(c) {
  // Missing usage fields (we only meter duration today) ⇒ "—", never ₹0.
  const cost = estimateCallCost({ seconds: c.duration_sec ?? null, ttsChars: null, llmTokensIn: null, llmTokensOut: null });
  return formatINR(cost);
}

const _CONV_EMPTY_HTML = `
  <div class="empty-state" colspan="7">
    <div class="empty-icon-circle">📞</div>
    <div class="empty-title">No conversations yet</div>
    <div class="empty-sub">Once an agent answers a call, the transcript will show up here.</div>
    <button class="dash-btn dash-btn-outline" onclick="openPlayground()">Test in Playground</button>
  </div>`;

function _renderConversations() {
  const tbody = $('#conv-tbody');
  if (!tbody) return;
  const palette = ['#7C5CFC', '#00C4A1', '#f59e0b', '#ef4444', '#6366f1', '#ec4899'];
  const footer = $('#conv-footer');
  const stateSlot = $('#conv-state-slot');

  // ERROR state (spec v1 §3): single-row banner, header stays.
  if (_statsError && !_convs.length) {
    tbody.innerHTML = '';
    if (stateSlot) {
      stateSlot.innerHTML = '<div class="error-banner"><span>⚠</span><span>Couldn\'t load conversations</span><button class="dash-btn dash-btn-outline error-retry" onclick="retryAnalytics()">Retry</button></div>';
    }
    setText(footer, '');
    return;
  }
  if (stateSlot) stateSlot.innerHTML = '';

  // EMPTY state — canonical ADR-0001 hero moment (spec v1 §3).
  if (!_convs.length) {
    const table = tbody.closest('table');
    tbody.innerHTML = '';
    if (table) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 7;
      td.innerHTML = _CONV_EMPTY_HTML;
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    setText(footer, '');
    return;
  }

  footer?.classList.add('visible');
  setText(footer, `Showing 1–${_convs.length} of ${_convs.length}`);

  tbody.innerHTML = _convs.map((c, i) => {
    const preview = (c.transcript ?? '').trim();
    return `
    <tr class="conv-row">
      <td>
        <div class="caller-info">
          <div class="caller-avatar" style="background:${palette[i % palette.length]}">${(c.caller_name ?? '?').charAt(0)}</div>
          <div>
            <div class="caller-name">${escHtml(c.caller_name ?? 'Unknown')}</div>
            <div class="caller-num">${escHtml(c.caller_number ?? '')}</div>
          </div>
        </div>
      </td>
      <td>${escHtml(c.agent_name ?? '—')}</td>
      <td class="conv-transcript">${preview ? escHtml(preview) : '<span class="text-dim">No transcript</span>'}</td>
      <td class="mono-num">${formatDuration(c.duration_sec)}</td>
      <td>${_outcomeBadge(c.status)}</td>
      <td class="mono-num">${_convEstCostCell(c)}</td>
      <td style="color:var(--dash-text-3);font-size:12px;font-family:var(--font-mono);">${timeAgo(c.created_at)}</td>
    </tr>
  `}).join('');
}

// ═══════════════════════════════════════════════════════════════
//  Phone Numbers
// ═══════════════════════════════════════════════════════════════
function _formatPhoneCapabilities(capabilities = []) {
  const caps = new Set((capabilities ?? []).map((c) => String(c).toLowerCase()));
  if (caps.has('inbound') && caps.has('outbound')) return 'Inbound + Outbound';
  if (caps.has('inbound')) return 'Inbound Only';
  if (caps.has('outbound')) return 'Outbound Only';
  return 'Inbound + Outbound';
}

function _renderPhoneNumbers() {
  const list = $('#phone-num-list');
  if (!list) return;

  if (!_phones.length) {
    list.innerHTML = '<p style="color:var(--dash-text-3);font-size:13px;">No phone numbers found.</p>';
    return;
  }

  list.innerHTML = _phones.map((phone) => {
    const capabilities = Array.isArray(phone.capabilities) ? phone.capabilities : [];
    const isOutboundOnly = capabilities.length === 1 && String(capabilities[0]).toLowerCase() === 'outbound';
    const icon = isOutboundOnly ? '📱' : '📞';
    const iconBg = isOutboundOnly ? 'rgba(0,196,161,0.1)' : 'rgba(124,92,252,0.1)';
    const agentName = phone.agent_name ?? phone.agents?.name ?? 'Unassigned';
    const status = String(phone.status ?? 'inactive').toLowerCase();
    const statusLabel = status === 'active' ? 'Active' : 'Inactive';
    const badgeClass = status === 'active' ? 'badge-green' : 'badge-gray';

    return `
      <div class="phone-num-card">
        <div class="phone-num-icon" style="background:${iconBg};">${icon}</div>
        <div class="phone-num-info">
          <div class="phone-num-number">${escHtml(phone.number ?? '—')}</div>
          <div class="phone-num-meta">${escHtml(phone.country ?? 'India')} · ${escHtml(phone.city ?? '—')} · ${escHtml(_formatPhoneCapabilities(capabilities))}</div>
        </div>
        <div style="text-align:right;">
          <div class="phone-num-agent">${escHtml(agentName)}</div>
          <div class="phone-num-meta">${Number(phone.call_count ?? 0).toLocaleString()} calls this month</div>
        </div>
        <span class="badge ${badgeClass}">${statusLabel}</span>
        <button class="agent-action-btn phone-delete-btn" onclick="removePhoneNumber('${phone.id}')">Delete</button>
      </div>
    `;
  }).join('');
}

// ═══════════════════════════════════════════════════════════════
//  Knowledge Base
// ═══════════════════════════════════════════════════════════════
function _renderKnowledgeDocs() {
  const list = $('#kb-doc-list');
  if (!list) return;
  if (!_docs.length) {
    list.innerHTML = '<p style="color:var(--dash-text-3);font-size:13px;">No documents yet. Upload one above.</p>';
    return;
  }
  list.innerHTML = _docs.map((d) => `
    <div style="display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--dash-surface);border:1px solid var(--dash-border);border-radius:var(--radius);">
      <span style="font-size:20px;">${d.type === 'url' ? '🔗' : '📄'}</span>
      <div style="flex:1;">
        <div style="font-size:13px;font-weight:600;color:var(--dash-text);">${escHtml(d.name)}</div>
        <div style="font-size:11px;color:var(--dash-text-3);font-family:var(--font-mono);">${d.size_bytes ? formatBytes(d.size_bytes) : ''} · ${timeAgo(d.created_at)}</div>
      </div>
      <span class="badge badge--${d.status === 'indexed' ? 'green' : d.status === 'pending' ? 'orange' : 'red'}">${d.status}</span>
    </div>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════
//  Voices Library — 9 Sarvam speakers (spec v1 §2)
//  Slugs verified against backend _SPEAKER_MAP in app/services/tts.py
// ═══════════════════════════════════════════════════════════════
const VOICES = [
  { slug: 'anushka',  name: 'Anushka',  gender: 'Female', lang: 'Hindi / English' },
  { slug: 'abhilash', name: 'Abhilash', gender: 'Male',   lang: 'Hindi / English' },
  { slug: 'manisha',  name: 'Manisha',  gender: 'Female', lang: 'Hindi' },
  { slug: 'vidya',    name: 'Vidya',    gender: 'Female', lang: 'Hindi' },
  { slug: 'arjun',    name: 'Arjun',    gender: 'Male',   lang: 'Hindi' },
  { slug: 'maya',     name: 'Maya',     gender: 'Female', lang: 'English-IN' },
  { slug: 'neel',     name: 'Neel',     gender: 'Male',   lang: 'English-IN' },
  { slug: 'maitreyi', name: 'Maitreyi', gender: 'Female', lang: 'Hindi' },
  { slug: 'amartya',  name: 'Amartya',  gender: 'Male',   lang: 'Hindi' },
];

// Deterministic avatar gradients per voice (spec v1 §2): female purple→teal,
// male dark-green. Data-differentiation tints — exempt from the v2 gradient ban.
const VOICE_AVATAR_GRADIENTS = {
  female: [
    ['#7C5CFC', '#00C4A1'],
    ['#8B5CF6', '#06B6D4'],
    ['#A78BFA', '#2DD4BF'],
  ],
  male: [
    ['#14532D', '#166534'],
    ['#166534', '#15803D'],
    ['#052E16', '#14532D'],
  ],
};

let _voiceFilterText = '';
let _voiceGenderFilter = 'all';

function _voiceAvatarGradient(voice, index) {
  const set = VOICE_AVATAR_GRADIENTS[voice.gender.toLowerCase()];
  return set[index % set.length];
}

function _voiceCardHtml(v, index) {
  const [c1, c2] = _voiceAvatarGradient(v, index);
  return `
    <div class="voice-card" data-slug="${v.slug}" data-gender="${v.gender.toLowerCase()}" tabindex="0" role="group" aria-label="${escHtml(v.name)} voice">
      <div class="voice-avatar" style="background:linear-gradient(135deg,${c1},${c2})">
        <span class="voice-avatar-letter">${escHtml(v.name.charAt(0))}</span>
        <span class="voice-waveform" aria-hidden="true"><i></i><i></i><i></i></span>
      </div>
      <div class="voice-card-info">
        <div class="voice-card-name">${escHtml(v.name)}</div>
        <div class="voice-card-slug">${escHtml(v.slug)}</div>
        <div class="voice-card-lang">${escHtml(v.lang)}</div>
      </div>
      <button class="voice-play-btn" aria-label="Preview ${escHtml(v.name)} voice" onclick="previewVoice('${v.slug}')">▶</button>
    </div>`;
}

function _renderVoicesSkeletons() {
  const grid = $('#voices-grid');
  if (!grid) return;
  grid.innerHTML = Array.from({ length: 9 }, () => '<div class="voice-card skeleton-card"></div>').join('');
}

function _renderVoices() {
  const grid = $('#voices-grid');
  if (!grid) return;
  grid.innerHTML = '';
  VOICES.forEach((v, i) => {
    const matchesText = v.name.toLowerCase().includes(_voiceFilterText) || v.slug.includes(_voiceFilterText);
    const matchesGender = _voiceGenderFilter === 'all' || v.gender.toLowerCase() === _voiceGenderFilter;
    if (!matchesText || !matchesGender) return;
    const tpl = document.createElement('template');
    tpl.innerHTML = _voiceCardHtml(v, i).trim();
    grid.appendChild(tpl.content.firstChild);
  });
}

function filterVoices(value) {
  _voiceFilterText = (value ?? '').trim().toLowerCase();
  _renderVoices();
}

function filterVoiceGender(gender, btn) {
  _voiceGenderFilter = gender;
  $$('#voice-gender-filter .chip').forEach((c) => c.classList.remove('active'));
  btn?.classList.add('active');
  _renderVoices();
}

// ── One-at-a-time preview via VoicePreview.js ─────────────────
function previewVoice(slug) {
  const playing = slug === window.__PLAYING_VOICE__;
  // Toggle off / stop current before anything else.
  stopPreview();
  window.__PLAYING_VOICE__ = null;

  if (playing) {
    _setVoicePlayingState(null);
    return;
  }

  const voice = VOICES.find((v) => v.slug === slug);
  playPreview({
    slug,
    language: voice?.lang,
    onStart: (s) => {
      window.__PLAYING_VOICE__ = s;
      _setVoicePlayingState(s);
    },
    onEnd: (s) => {
      if (window.__PLAYING_VOICE__ === s) {
        window.__PLAYING_VOICE__ = null;
        _setVoicePlayingState(null);
      }
    },
    onError: (s, message) => {
      showToast(`⚠️ ${message}`, 'error');
      if (window.__PLAYING_VOICE__ === s) {
        window.__PLAYING_VOICE__ = null;
        _setVoicePlayingState(null);
      }
    },
  });
}

function _setVoicePlayingState(activeSlug) {
  $$('#voices-grid .voice-card').forEach((card) => {
    const isPlaying = activeSlug && card.dataset.slug === activeSlug;
    card.classList.toggle('playing', !!isPlaying);
    const btn = card.querySelector('.voice-play-btn');
    if (btn) {
      btn.textContent = isPlaying ? '⏸' : '▶';
      btn.setAttribute('aria-label', `${isPlaying ? 'Stop' : 'Preview'} ${card.dataset.slug} voice`);
    }
  });
}

// ═══════════════════════════════════════════════════════════════
//  Live Ticker — simulates incoming calls every 15-35 seconds
// ═══════════════════════════════════════════════════════════════
function _startLiveTicker() {
  const CALLERS = ['Priya Mehta', 'Rohit Gupta', 'Kavya Iyer', 'Amit Sharma', 'Sunita Reddy', 'Raj Patel', 'Nisha Kumar'];
  const STATUSES = ['resolved', 'resolved', 'resolved', 'escalated', 'missed'];
  const EMOJI = { resolved: '✅', escalated: '⚠️', missed: '📵' };

  const tick = () => {
    const delay = (15 + Math.random() * 20) * 1_000;
    _tickerTimeout = setTimeout(async () => {
      const published = _agents.filter((a) => a.status === 'published');
      const agent = published[Math.floor(Math.random() * published.length)];
      const status = STATUSES[Math.floor(Math.random() * STATUSES.length)];

      const payload = {
        user_id: _user.id,
        agent_id: agent?.id ?? null,
        agent_name: agent?.name ?? 'Customer Support Agent',
        caller_name: CALLERS[Math.floor(Math.random() * CALLERS.length)],
        caller_number: `+91 ${Math.floor(70000 + Math.random() * 29999)} ${Math.floor(10000 + Math.random() * 89999)}`,
        duration_sec: Math.floor(30 + Math.random() * 300),
        status,
        csat_score: Math.random() > 0.3 ? Math.floor(4 + Math.random() * 2) : null,
      };

      const { data } = await addConversation(payload);
      if (data) {
        _convs.unshift(data);
        _updateHomeStats();
        showToast(`${EMOJI[status] ?? '📞'} ${payload.caller_name} · ${payload.agent_name} · ${formatDuration(payload.duration_sec)}`);

        const activePage = $('[id^="page-"].active');
        if (activePage?.id === 'page-conversations') _renderConversations();
        if (activePage?.id === 'page-home') _renderRecentTable();
      }
      tick();
    }, delay);
  };
  tick();
}

// ═══════════════════════════════════════════════════════════════
//  Realtime
// ═══════════════════════════════════════════════════════════════
function _setupRealtime() {
  subscribeToConversations(_user.id, (newConv) => {
    if (_convs.find((c) => c.id === newConv.id)) return; // dedup
    _convs.unshift(newConv);
    _updateHomeStats();
    const active = $('[id^="page-"].active');
    if (active?.id === 'page-conversations') _renderConversations();
    if (active?.id === 'page-home') _renderRecentTable();
  });

  subscribeToAgents(_user.id, ({ eventType, new: row, old }) => {
    if (eventType === 'INSERT') _agents.unshift(row);
    if (eventType === 'UPDATE') _agents = _agents.map((a) => a.id === row.id ? row : a);
    if (eventType === 'DELETE') _agents = _agents.filter((a) => a.id !== old.id);
    if ($('#page-agents.active')) _renderAgents();
  });
}

// ═══════════════════════════════════════════════════════════════
//  Auth
// ═══════════════════════════════════════════════════════════════
async function _logout() {
  clearTimeout(_tickerTimeout);
  unsubscribeAll();
  await authService.signOut();
  window.location.replace('/auth.html?mode=login');
}

function _showUserMenu(e) {
  e.stopPropagation();
  document.querySelector('.user-context-menu')?.remove();

  const name = _profile?.full_name || _user?.email || 'User';
  const email = _user?.email ?? '';
  const menu = document.createElement('div');
  menu.className = 'user-context-menu';
  menu.style.cssText = 'position:fixed;bottom:76px;left:10px;width:200px;background:white;border:1px solid var(--dash-border);border-radius:var(--radius);box-shadow:var(--shadow-lg);z-index:500;overflow:hidden;';
  menu.innerHTML = `
    <div style="padding:12px 14px;border-bottom:1px solid var(--dash-border);">
      <div style="font-size:12px;font-weight:700;color:var(--dash-text);">${escHtml(name)}</div>
      <div style="font-size:11px;color:var(--dash-text-3);">${escHtml(email)}</div>
    </div>
    <button id="menu-settings" style="width:100%;text-align:left;padding:10px 14px;border:none;background:none;font-size:13px;color:var(--dash-text-2);cursor:pointer;font-family:var(--font-sans);">⚙️ Settings</button>
    <div style="height:1px;background:var(--dash-border);margin:4px 0;"></div>
    <button id="menu-signout" style="width:100%;text-align:left;padding:10px 14px;border:none;background:none;font-size:13px;color:#ef4444;cursor:pointer;font-family:var(--font-sans);">↩ Sign out</button>
  `;
  document.body.appendChild(menu);
  menu.querySelector('#menu-settings').addEventListener('click', () => { menu.remove(); navigate('settings', $('#nav-settings')); });
  menu.querySelector('#menu-signout').addEventListener('click', _logout);
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 10);
}

// ═══════════════════════════════════════════════════════════════
//  Expose to HTML (onclick attributes)
// ═══════════════════════════════════════════════════════════════
Object.assign(window, {
  // Navigation
  navigate,
  switchConfigTab,

  // Chart period
  switchPeriod: (period, btn) => {
    switchPeriodTab(btn);
    requestAnimationFrame(() => drawLineChart('callChart', _buildChartData(period)));
  },

  // Voices
  previewVoice,
  filterVoices,
  filterVoiceGender,
  _renderVoicesSkeletons,

  // Analytics retry — re-fetches stats and re-renders all analytics surfaces
  retryAnalytics: async () => {
    _statsError = false;
    _stats = [];
    const result = await getDailyStats(_user.id, 30)
      .then((rows) => ({ rows, error: null }))
      .catch((err) => {
        console.error('[app] getDailyStats retry failed', err);
        return { rows: null, error: err };
      });
    _statsError = result.error !== null || result.rows === null;
    _stats = result.rows ?? [];
    if (_statsError) { showToast('⚠️ Still couldn\'t load analytics', 'error'); return; }
    _updateHomeStats();
    _initAnalyticsChart();
  },

  // Agent panel
  openCreateAgent: openCreateModal,
  closeCreateModal,
  selectTemplate,
  editAgent: (id, name) => {
    navigate('agent-config', null);
    setText($('#config-agent-name'), name);
    setText($('#config-agent-id'), `ID: ag_${id.slice(0, 8)}`);
    window.__CURRENT_AGENT_ID__ = id;
    window.__CURRENT_AGENT_NAME__ = name;
    const agent = _agents.find((a) => a.id === id);
    window.__CURRENT_AGENT_PROFILE__ = agent || null;

    if (agent) {
      const promptEl = $('#agent-system-prompt');
      const firstMsgEl = $('#agent-first-message');
      if (promptEl) promptEl.value = agent.system_prompt ?? '';
      if (firstMsgEl) firstMsgEl.value = agent.first_message ?? '';
    }
    switchConfigTab('agent', $('[onclick*="switchConfigTab(\'agent\'"]'));
  },

  // Agent CRUD
  createAgentSubmit: async () => {
    const btn = $('#create-agent-btn');
    const nameInput = $('#new-agent-name');
    const form = readCreateAgentForm();

    // Validate name
    if (!form.name || form.name === 'New Agent' && !nameInput?.value?.trim()) {
      nameInput?.focus();
      showToast('⚠️ Please enter a name for your agent', 'error');
      return;
    }

    const PROMPTS = {
      customer_support: 'You are a helpful AI customer support agent for our company. Be empathetic, concise, and solution-focused. Resolve queries efficiently and escalate complex issues professionally.',
      sales: 'You are an AI sales agent. Your goal is to qualify leads, identify pain points, and book demos. Be confident, consultative, and focus on value rather than features.',
      collections: 'You are a professional collections agent. Be firm but respectful. Remind customers of overdue payments, offer payment plans, and document all commitments.',
      survey: 'You are an AI survey agent conducting customer satisfaction surveys. Ask clear, concise questions, record responses accurately, and thank customers for their time.',
      appointments: 'You are an AI appointment booking agent. Help customers book, reschedule, or cancel appointments. Be efficient, confirm all details, and send confirmation.',
      blank: 'You are a helpful AI call agent. Be concise, friendly, and professional.',
    };

    const FIRST_MESSAGES = {
      customer_support: 'Hello! I\'m here to help with any questions or issues you have. How can I assist you today?',
      sales: 'Hi there! I\'m calling to learn about your business needs. Do you have a couple of minutes to chat?',
      collections: 'Hello, I\'m calling regarding an outstanding balance on your account. Is this a good time to discuss?',
      survey: 'Hi! I\'m conducting a quick customer satisfaction survey. It\'ll take less than 2 minutes. May I proceed?',
      appointments: 'Hello! I can help you with booking or managing your appointments. What can I do for you today?',
      blank: 'Hello! How can I help you today?',
    };

    if (btn) { btn.textContent = 'Creating…'; btn.disabled = true; }

    const { data, error } = await createAgent({
      user_id: _user.id,
      name: form.name,
      voice_name: form.voiceName,
      voice_lang: form.voiceLang,
      language: form.lang,
      icon: form.icon,
      template: form.template,
      system_prompt: PROMPTS[form.template] ?? PROMPTS.blank,
      first_message: FIRST_MESSAGES[form.template] ?? FIRST_MESSAGES.blank,
      status: 'draft',
    });

    if (btn) { btn.textContent = 'Create Agent →'; btn.disabled = false; }
    if (error) { showToast('❌ ' + error.message, 'error'); return; }

    _agents.unshift(data);
    closeCreateModal();
    navigate('agents', $('#nav-agents'));
    _renderAgents();
    showToast(`✅ Agent "${form.name}" created!`, 'success');
  },

  removeAgent: async (id) => {
    if (!confirm('Delete this agent? This cannot be undone.')) return;
    const { error } = await deleteAgent(id);
    if (error) { showToast('❌ ' + error.message, 'error'); return; }
    _agents = _agents.filter((a) => a.id !== id);
    _renderAgents();
    showToast('🗑️ Agent deleted');
  },
  filterAgents: (val) => _renderAgents(val),

  // Phone numbers
  removePhoneNumber: async (id) => {
    if (!confirm('Delete this phone number? This cannot be undone.')) return;
    const { error } = await deletePhoneNumber(id, _user.id);
    if (error) { showToast('❌ ' + error.message, 'error'); return; }
    _phones = _phones.filter((p) => p.id !== id);
    _renderPhoneNumbers();
    showToast('🗑️ Phone number deleted', 'success');
  },

  // Playground
  openPlayground: (agentId, agentName) => {
    const fallbackAgent = _agents.find((a) => a.status === 'published') || _agents[0] || null;
    const resolvedId = agentId || window.__CURRENT_AGENT_ID__ || fallbackAgent?.id || '';
    const resolvedAgent = _agents.find((a) => a.id === resolvedId) || window.__CURRENT_AGENT_PROFILE__ || fallbackAgent;

    if (resolvedAgent) {
      window.__CURRENT_AGENT_ID__ = resolvedAgent.id;
      window.__CURRENT_AGENT_NAME__ = resolvedAgent.name;
      window.__CURRENT_AGENT_PROFILE__ = resolvedAgent;
    }

    openPlayground(
      resolvedId,
      agentName || resolvedAgent?.name || window.__CURRENT_AGENT_NAME__
    );
  },
  closePlayground,
  togglePlaygroundCall,
  sendPlaygroundMessage,
  sendPlaygroundMsg: sendPlaygroundMessage, // alias for HTML onclick

  // Auth
  showUserMenu: _showUserMenu,

  // Knowledge
  uploadDoc: async () => {
    const input = prompt('Enter document name or URL:');
    if (!input) return;
    const value = input.trim();
    if (!value) return;
    const isUrl = /^https?:\/\//i.test(value);
    const { data } = await addKnowledgeDoc({
      user_id: _user.id,
      name: value,
      type: isUrl ? 'url' : 'file',
      size_bytes: isUrl ? null : Math.floor(200_000 + Math.random() * 2_000_000),
      url: isUrl ? value : null,
      status: 'indexed',
    });
    if (data) { _docs.unshift(data); _renderKnowledgeDocs(); showToast('✅ Document added & indexed', 'success'); }
  },
});

// ── Page-level navigate hooks ─────────────────────────────────
const _originalNavigate = navigate;
window.navigate = (pageId, navEl) => {
  _originalNavigate(pageId, navEl);
  if (pageId === 'conversations') _renderConversations();
  if (pageId === 'analytics') _initAnalyticsChart();
  if (pageId === 'knowledge') _renderKnowledgeDocs();
  if (pageId === 'phonenumbers') _renderPhoneNumbers();
  if (pageId === 'home') _initHomePage();
  if (pageId === 'voices') {
    // Static catalog of 9 — render skeletons only on the very first open.
    if (!$$('#voices-grid .voice-card:not(.skeleton-card)').length) {
      window._renderVoicesSkeletons();
      setTimeout(_renderVoices, 350); // catalog is static; brief skeleton beat
    }
  }
};

// ── Resize → re-draw charts ───────────────────────────────────
window.addEventListener('resize', () => {
  const active = $('[id^="page-"].active')?.id;
  if (active === 'page-home') drawLineChart('callChart', _buildChartData('week'));
  if (active === 'page-analytics') drawLineChart('analyticsChart', _buildChartData('month'), '#00C4A1');
}, { passive: true });

// ── Start ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', boot);
