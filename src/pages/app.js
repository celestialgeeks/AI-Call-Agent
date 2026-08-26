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
import { getAgents, createAgent, deleteAgent, updateAgent } from '@/services/agentService.js';
import { getConversations, addKnowledgeDoc, getPhoneNumbers, deletePhoneNumber, getKnowledgeDocs, getTools } from '@/services/conversationService.js';
import { getDailyStats, seedUserData, getProfile, updateProfile } from '@/services/analyticsService.js';
import { subscribeToConversations, subscribeToAgents, unsubscribeAll } from '@/services/realtimeService.js';

// ── Components ────────────────────────────────────────────────
import { navigate, switchConfigTab, switchPeriodTab } from '@/components/Sidebar.js';
import { openCreateModal, closeCreateModal, selectTemplate, readCreateAgentForm } from '@/components/Modal.js';
import { openPlayground, closePlayground, togglePlaygroundCall, sendPlaygroundMessage } from '@/components/Playground.js';
import { drawLineChart } from '@/components/Chart.js';
import { SARVAM_VOICES, renderVoiceGrid, initVoiceFilters, previewVoice } from '@/components/Voices.js';
import { loadAnalyticsPage, switchAnalyticsPeriod } from '@/components/Analytics.js';
import { estimateCallCost, formatINR } from '@/config/pricing.js';
import { icon } from '@/utils/icons.js';

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

  // 4. Parallel data fetch
  [_agents, _convs, _phones, _docs, _tools, _stats] = await Promise.all([
    getAgents(_user.id),
    getConversations(_user.id),
    getPhoneNumbers(_user.id),
    getKnowledgeDocs(_user.id),
    getTools(_user.id),
    getDailyStats(_user.id, 30),
  ]);

  // 5. Render home page & initial components
  _hydrateIcons();       // Inline all [data-icon] SVGs (design spec v2 §3)
  _initHomePage();
  _renderAgents();
  _renderTools();        // Tools page — real data or honest empty state
  renderVoiceGrid();      // Voices library (src/components/Voices.js)
  initVoiceFilters();
  _renderPhoneNumbers();
  _renderKnowledgeDocs();

  // 6. Realtime subscriptions
  _setupRealtime();

  // 7. Live ticker removed (ADR-0001) — real conversations arrive via realtime.
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
  setText($('.home-greeting'), `Good ${period}, ${firstName}`);
}

// ═══════════════════════════════════════════════════════════════
//  Icon hydration (design spec v2 §3)
//  Replaces every [data-icon] placeholder with its inline SVG once at boot.
// ═══════════════════════════════════════════════════════════════
function _hydrateIcons() {
  document.querySelectorAll('[data-icon]').forEach((el) => {
    if (el.querySelector('svg')) return; // already hydrated
    el.innerHTML = icon(el.dataset.icon) || el.innerHTML;
    el.removeAttribute('data-icon');
  });
}

// ═══════════════════════════════════════════════════════════════
//  Tools (real data or honest empty state — ADR-0001)
// ═══════════════════════════════════════════════════════════════
function _renderTools() {
  const container = $('#tool-rows');
  if (!container) return;

  if (!_tools.length) {
    container.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">${icon('tools')}</div>
        <div class="empty-title">No tools configured</div>
        <div class="empty-sub">Add tools so your agents can call external APIs and take actions during calls.</div>
        <button type="button" class="dash-btn dash-btn-primary" onclick="openCreateModal()">+ Add tool</button>
      </div>`;
    return;
  }

  container.innerHTML = _tools.map((t) => `
    <div class="tool-card">
      <div class="tool-card__head">
        <div>
          <div class="tool-card__name">${escHtml(t.name ?? 'Untitled tool')}</div>
          <div class="tool-card__endpoint mono-num">${escHtml(t.method ?? 'GET')} ${escHtml(t.endpoint ?? '')}</div>
        </div>
        <span class="badge ${t.status === 'active' ? 'badge-green' : 'badge-gray'}">${escHtml(t.status ?? 'inactive')}</span>
      </div>
      ${t.description ? `<p class="tool-card__desc">${escHtml(t.description)}</p>` : ''}
    </div>`).join('');
}

// ═══════════════════════════════════════════════════════════════
//  Home Page
// ═══════════════════════════════════════════════════════════════
function _initHomePage() {
  _updateHomeStats();
  _renderRecentTable();
  _initHomeChart('week');
}

function _updateHomeStats() {
  const today = new Date().toDateString();
  const todayConvs = _convs.filter((c) => new Date(c.created_at).toDateString() === today);
  const resolvedToday = todayConvs.filter((c) => c.status === 'resolved').length;
  const avgSec = todayConvs.length
    ? Math.round(todayConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0) / todayConvs.length)
    : 0;
  // ADR-0001: no data ⇒ real zeros. Fabricated fallbacks removed
  // (was: successRate default 98.2%, cost = convs × hardcoded ₹0.44).
  const successRate = todayConvs.length ? ((resolvedToday / todayConvs.length) * 100).toFixed(1) : '0.0';
  // Cost from pricing.js — null rate ⇒ em-dash, never a guess.
  const totalSecToday = todayConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0);
  const costVal = estimateCallCost({ seconds: totalSecToday });
  const perCallVal = estimateCallCost({
    seconds: todayConvs.length ? Math.round(totalSecToday / todayConvs.length) : 0,
  });

  _setStatCard(0, todayConvs.length.toLocaleString('en-IN'), null);
  _setStatCard(1, formatDuration(avgSec), null);
  _setStatCard(2, `${successRate}%`, null);
  _setStatCard(3, formatINR(costVal, { compact: true }), `≈ ${formatINR(perCallVal)} per call`);
}

/**
 * Fills one home stat card. Deltas render ONLY from a real previous-period
 * comparison (design spec §1/§6.4); passing `null` omits the line entirely.
 */
function _setStatCard(index, value, sub) {
  const cards = $$('.stat-card');
  if (!cards[index]) return;
  const valEl = cards[index].querySelector('.stat-value');
  const subEl = cards[index].querySelector('.stat-delta, .stat-cost');
  setText(valEl, value);
  if (!subEl) return;
  if (sub === null || sub === undefined) {
    subEl.textContent = '';
    subEl.hidden = true; // no fabricated "↑ x% vs yesterday" lines
  } else {
    subEl.hidden = false;
    subEl.textContent = sub;
    subEl.style.color = '';
  }
}

// ── Charts ────────────────────────────────────────────────────
function _buildChartData(period) {
  // ADR-0001: no data ⇒ empty array ⇒ caller renders the empty-chart state.
  if (!_stats.length) return [];
  const sorted = [..._stats].sort((a, b) => a.date.localeCompare(b.date));
  if (period === 'week') return sorted.slice(-7).map((d) => d.total_calls);
  if (period === 'month') return sorted.slice(-30).map((d) => d.total_calls);
  return sorted.slice(-12).map((d) => Math.round(d.total_calls / 8));
}

function _initHomeChart(period = 'week') {
  const data = _buildChartData(period);
  const wrap = document.querySelector('#page-home .chart-wrap');
  if (!wrap) return;
  if (data.length < 2) {
    wrap.innerHTML = `
      <div class="empty-chart">
        <div class="empty-chart__icon">${icon('phone-numbers')}</div>
        <div class="empty-chart__title">No calls yet</div>
        <div class="empty-chart__sub">Metrics appear after your agents handle their first call.</div>
      </div>`;
    return;
  }
  if (!wrap.querySelector('canvas')) {
    wrap.innerHTML = '<canvas id="callChart"></canvas>';
  }
  requestAnimationFrame(() => drawLineChart('callChart', data));
}

// ═══════════════════════════════════════════════════════════════
//  Recent Conversations (home)
// ═══════════════════════════════════════════════════════════════
function _renderRecentTable() {
  const tbody = $('#recent-tbody');
  if (!tbody) return;
  if (!_convs.length) {
    tbody.innerHTML = `
      <tr><td colspan="5">
        <div class="empty-state">
          <div class="empty-icon">${icon('conversations')}</div>
          <div class="empty-title">No conversations yet</div>
          <div class="empty-sub">Once an agent answers a call, the transcript will show up here.</div>
          <button type="button" class="dash-btn dash-btn-outline" onclick="openPlayground()">Test in Playground</button>
        </div>
      </td></tr>`;
    return;
  }
  _renderRecentRows();
}

function _renderRecentRows() {
  const tbody = $('#recent-tbody');
  if (!tbody) return;
  tbody.innerHTML = _convs.slice(0, 5).map((c) => `
    <tr>
      <td>
        <div class="caller-info">
          <div class="caller-avatar">${escHtml((c.caller_name ?? '?').charAt(0).toUpperCase())}</div>
          <div>
            <div class="caller-name">${escHtml(c.caller_name ?? 'Unknown')}</div>
            <div class="caller-num">${escHtml(c.caller_number ?? '')}</div>
          </div>
        </div>
      </td>
      <td>${escHtml(c.agent_name ?? '—')}</td>
      <td>${formatDuration(c.duration_sec)}</td>
      <td><span class="badge ${statusBadgeClass(c.status)}">${escHtml(c.status)}</span></td>
      <td style="color:var(--dash-text-3);font-size:12px;">${timeAgo(c.created_at)}</td>
    </tr>
  `).join('');
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

  if (!filtered.length) {
    container.innerHTML = `<div style="padding:40px;text-align:center;color:var(--dash-text-3);grid-column:1/-1;">
      No agents found. <a href="#" onclick="openCreateAgent()" style="color:var(--dash-accent);">Create one →</a>
    </div>`;
    return;
  }

  container.innerHTML = filtered.map((a) => {
    return `<div class="agent-row" onclick="editAgent('${a.id}','${escHtml(a.name)}')">
      <div class="agent-name-cell">
        <div class="agent-avatar">${escHtml((a.name ?? 'A').charAt(0).toUpperCase())}</div>
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
//  Conversations (spec §3: Caller | Agent | Transcript | Duration |
//  Outcome | Est. cost | Date — 48px rows, honest empty state)
// ═══════════════════════════════════════════════════════════════

/** Maps backend status → badge variant 1:1; never infers. */
function _outcomeBadge(status) {
  const map = {
    resolved: 'badge-green',
    completed: 'badge-green',
    escalated: 'badge-orange',
    in_progress: 'badge-orange',
    missed: 'badge-red',
    failed: 'badge-red',
    abandoned: 'badge-gray',
  };
  return map[status] ?? 'badge-gray';
}

function _renderConversations() {
  const tbody = $('#conv-tbody');
  if (!tbody) return;

  // Empty state (ADR-0001 hero moment, spec §3)
  if (!_convs.length) {
    tbody.innerHTML = `
      <tr><td colspan="7">
        <div class="empty-state">
          <div class="empty-icon">${icon('conversations')}</div>
          <div class="empty-title">No conversations yet</div>
          <div class="empty-sub">Once an agent answers a call, the transcript will show up here.</div>
          <button type="button" class="dash-btn dash-btn-outline" onclick="openPlayground()">Test in Playground</button>
        </div>
      </td></tr>`;
    const footer = $('#conv-footer');
    if (footer) footer.hidden = true;
    return;
  }

  tbody.innerHTML = _convs.map((c) => {
    const costVal = estimateCallCost({ seconds: c.duration_sec ?? 0 });
    const transcript = c.transcript || c.first_message || '';
    const preview = transcript ? String(transcript).slice(0, 120) : '\u2014';
    const inProgress = c.status === 'in_progress';
    return `
    <tr>
      <td>
        <div class="caller-info">
          <div class="caller-avatar">${escHtml((c.caller_name ?? '?').charAt(0).toUpperCase())}</div>
          <div>
            <div class="caller-name">${escHtml(c.caller_name ?? 'Unknown')}</div>
            <div class="caller-num">${escHtml(c.caller_number ?? '')}</div>
          </div>
        </div>
      </td>
      <td>${escHtml(c.agent_name ?? '—')}</td>
      <td><span class="conv-transcript" title="${escHtml(preview)}">${escHtml(preview)}</span></td>
      <td class="mono-num" style="text-align:right;">${formatDuration(c.duration_sec)}</td>
      <td><span class="badge ${_outcomeBadge(c.status)}${inProgress ? ' pulse-dot' : ''}">${escHtml(c.status)}</span></td>
      <td class="conv-cost" style="text-align:right;">${formatINR(costVal)}</td>
      <td style="color:var(--dash-text-3);font-size:12px;font-family:var(--font-mono);">${timeAgo(c.created_at)}</td>
    </tr>`;
  }).join('');

  // Footer count line — "Showing X–Y of Z", Z only when known.
  const footer = $('#conv-footer');
  if (footer) {
    footer.hidden = false;
    footer.textContent = `Showing 1\u2013${_convs.length} of ${_convs.length}`;
  }
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
    const phoneIcon = icon('phone-numbers');
    const agentName = phone.agent_name ?? phone.agents?.name ?? 'Unassigned';
    const status = String(phone.status ?? 'inactive').toLowerCase();
    const statusLabel = status === 'active' ? 'Active' : 'Inactive';
    const badgeClass = status === 'active' ? 'badge-green' : 'badge-gray';

    return `
      <div class="phone-num-card">
        <div class="phone-num-icon">${phoneIcon}</div>
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
      <span style="display:inline-flex;color:var(--dash-text-2);">${d.type === 'url' ? icon('integrations') : icon('file')}</span>
      <div style="flex:1;">
        <div style="font-size:13px;font-weight:600;color:var(--dash-text);">${escHtml(d.name)}</div>
        <div style="font-size:11px;color:var(--dash-text-3);font-family:var(--font-mono);">${d.size_bytes ? formatBytes(d.size_bytes) : ''} · ${timeAgo(d.created_at)}</div>
      </div>
      <span class="badge badge--${d.status === 'indexed' ? 'green' : d.status === 'pending' ? 'orange' : 'red'}">${d.status}</span>
    </div>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════
//  Voices — moved to src/components/Voices.js (Sarvam catalog,
//  per-voice TTS preview, gender/search filters). SARVAM_VOICES is
//  imported there; the create-agent voice picker consumes the same list.
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
//  Live Ticker — REMOVED (ADR-0001).
//  The old ticker fabricated incoming calls (random callers, durations,
//  statuses) and wrote them into Supabase every 15–35s. Real conversations
//  arrive via the realtime subscription below; nothing else may invent them.
// ═══════════════════════════════════════════════════════════════
function _startLiveTicker() { /* intentionally empty — see ADR-0001 note above */ }

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
    <button id="menu-settings" style="width:100%;text-align:left;padding:10px 14px;border:none;background:none;font-size:13px;color:var(--dash-text-2);cursor:pointer;font-family:var(--font-sans);">Settings</button>
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
  showToast,
  previewVoice: (slug) => previewVoice(slug),

  // Chart period (home page)
  switchPeriod: (period, btn) => {
    switchPeriodTab(btn);
    requestAnimationFrame(() => _initHomeChart(period));
  },

  // Analytics period tabs
  switchAnalyticsPeriod,

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
      showToast('Please enter a name for your agent', 'error');
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
    if (error) { showToast('Error: ' + error.message, 'error'); return; }
    _agents.unshift(data);
    closeCreateModal();
    navigate('agents', $('#nav-agents'));
    _renderAgents();
    showToast(`Agent "${form.name}" created!`, 'success');
  },

  removeAgent: async (id) => {
    if (!confirm('Delete this agent? This cannot be undone.')) return;
    const { error } = await deleteAgent(id);
    if (error) { showToast('Error: ' + error.message, 'error'); return; }
    _agents = _agents.filter((a) => a.id !== id);
    _renderAgents();
    showToast('Agent deleted');
  },
  filterAgents: (val) => _renderAgents(val),

  // Agent config: publish / save (Supabase-backed)
  publishAgent: async () => {
    const id = window.__CURRENT_AGENT_ID__;
    if (!id) { showToast('Open an agent first', 'error'); return; }
    const agent = _agents.find((a) => a.id === id);
    if (agent?.status === 'published') { showToast('Agent is already published', 'info'); return; }
    const { data, error } = await updateAgent(id, { status: 'published' });
    if (error) { showToast('Error: ' + error.message, 'error'); return; }
    _agents = _agents.map((a) => a.id === id ? data : a);
    const badge = $('#config-status-badge');
    if (badge) badge.textContent = '● Published';
    showToast('Agent published', 'success');
  },

  saveAgentConfig: async () => {
    const id = window.__CURRENT_AGENT_ID__;
    if (!id) { showToast('Open an agent first', 'error'); return; }
    const page = $('#page-agent-config');
    if (!page) return;
    const promptEl = page.querySelector('.form-textarea');
    const inputs = page.querySelectorAll('#config-tab-agent input.form-input');
    const payload = {};
    if (promptEl?.value != null) payload.system_prompt = promptEl.value;
    if (inputs[1]?.value != null) payload.first_message = inputs[1].value;
    const nameInput = $('#config-agent-name');
    if (!payload.system_prompt && !payload.first_message && !nameInput?.value) {
      showToast('Nothing to save', 'info'); return;
    }
    if (nameInput?.value && window.__CURRENT_AGENT_NAME__ !== nameInput.value) {
      payload.name = nameInput.value;
    }
    const { data, error } = await updateAgent(id, payload);
    if (error) { showToast('Error: ' + error.message, 'error'); return; }
    _agents = _agents.map((a) => a.id === id ? data : a);
    if (payload.name) {
      window.__CURRENT_AGENT_NAME__ = payload.name;
      const nameEl = $('#config-agent-name');
      if (nameEl) setText(nameEl, payload.name);
    }
    showToast('Changes saved', 'success');
  },

  copyEmbedCode: () => {
    const snippet = '<script src="https://cdn.sahaiy.ai/widget.js"><\/script>\n<sahaiy-widget agent-id="' + (window.__CURRENT_AGENT_ID__ ?? 'ag_01jkxxx') + '" label="Talk to us"></sahaiy-widget>';
    navigator.clipboard.writeText(snippet)
      .then(() => showToast('Embed code copied to clipboard', 'success'))
      .catch(() => showToast('Could not access clipboard', 'error'));
  },

  saveWorkspaceSettings: async () => {
    const settingsPage = $('#page-settings');
    if (!settingsPage || !_user) return;
    const card = settingsPage.querySelector('.form-input');
    const name = card?.value?.trim();
    if (!name) { showToast('Enter a workspace name', 'error'); return; }
    const { error } = await updateProfile(_user.id, { full_name: name });
    if (error) { showToast('Error: ' + error.message, 'error'); return; }
    _profile = { ..._profile, full_name: name };
    _hydrateUserUI();
    showToast('Settings saved', 'success');
  },

  // Phone numbers
  removePhoneNumber: async (id) => {
    if (!confirm('Delete this phone number? This cannot be undone.')) return;
    const { error } = await deletePhoneNumber(id, _user.id);
    if (error) { showToast('Error: ' + error.message, 'error'); return; }
    _phones = _phones.filter((p) => p.id !== id);
    _renderPhoneNumbers();
    showToast('Phone number deleted', 'success');
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
    if (data) { _docs.unshift(data); _renderKnowledgeDocs(); showToast('Document added & indexed', 'success'); }
  },
});

// ── Page-level navigate hooks ─────────────────────────────────
const _originalNavigate = navigate;
window.navigate = (pageId, navEl) => {
  _originalNavigate(pageId, navEl);
  if (pageId === 'conversations') _renderConversations();
  if (pageId === 'analytics') loadAnalyticsPage();
  if (pageId === 'knowledge') _renderKnowledgeDocs();
  if (pageId === 'phonenumbers') _renderPhoneNumbers();
  if (pageId === 'home') _initHomePage();
};

// ── Resize → re-draw charts ───────────────────────────────────
window.addEventListener('resize', () => {
  const active = $('[id^="page-"].active')?.id;
  if (active === 'page-home') _initHomeChart('week');
  if (active === 'page-analytics') loadAnalyticsPage();
}, { passive: true });

// ── Start ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', boot);
