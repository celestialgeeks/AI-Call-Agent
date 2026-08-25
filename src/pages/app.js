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
  [_agents, _convs, _phones, _docs, , _stats] = await Promise.all([
    getAgents(_user.id),
    getConversations(_user.id),
    getPhoneNumbers(_user.id),
    getKnowledgeDocs(_user.id),
    getTools(_user.id),
    getDailyStats(_user.id, 30),
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

function _updateHomeStats() {
  const today = new Date().toDateString();
  const todayConvs = _convs.filter((c) => new Date(c.created_at).toDateString() === today);
  const resolvedToday = todayConvs.filter((c) => c.status === 'resolved').length;
  const avgSec = todayConvs.length
    ? Math.round(todayConvs.reduce((s, c) => s + (c.duration_sec ?? 0), 0) / todayConvs.length)
    : 0;
  const successRate = todayConvs.length ? ((resolvedToday / todayConvs.length) * 100).toFixed(1) : 98.2;
  const costInr = (_convs.length * 0.44).toFixed(0);

  _setStatCard(0, _convs.length.toLocaleString(), '↑ 18% vs yesterday', true);
  _setStatCard(1, formatDuration(avgSec), '↓ 4% vs yesterday', false);
  _setStatCard(2, `${successRate}%`, '↑ 1.2pp vs last week', true);
  _setStatCard(3, `₹${Number(costInr).toLocaleString()}`, '≈ ₹0.44 per call', null);
}

function _setStatCard(index, value, sub, positive) {
  const cards = $$('.stat-card');
  if (!cards[index]) return;
  const valEl = cards[index].querySelector('.stat-value');
  const subEl = cards[index].querySelector('.stat-delta, .stat-cost');
  setText(valEl, value);
  if (subEl) {
    subEl.textContent = sub;
    if (positive !== null) subEl.style.color = positive ? '#22c55e' : '#ef4444';
  }
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
      <td><span class="badge ${statusBadgeClass(c.status)}">${escHtml(c.status)}</span></td>
      <td style="color:var(--dash-text-3);font-size:12px;">${timeAgo(c.created_at)}</td>
    </tr>
  `).join('');
}

// ── Charts ────────────────────────────────────────────────────
function _buildChartData(period) {
  if (!_stats.length) return [240, 310, 285, 420, 380, 490, 518];
  const sorted = [..._stats].sort((a, b) => a.date.localeCompare(b.date));
  if (period === 'week') return sorted.slice(-7).map((d) => d.total_calls);
  if (period === 'month') return sorted.slice(-30).map((d) => d.total_calls);
  return sorted.slice(-12).map((d) => Math.round(d.total_calls / 8));
}

function _initHomeChart(period = 'week') {
  requestAnimationFrame(() => drawLineChart('callChart', _buildChartData(period)));
}

function _initAnalyticsChart() {
  requestAnimationFrame(() => drawLineChart('analyticsChart', _buildChartData('month'), '#00C4A1'));
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
//  Conversations
// ═══════════════════════════════════════════════════════════════
function _renderConversations() {
  const tbody = $('#conv-tbody');
  if (!tbody) return;
  const palette = ['#7C5CFC', '#00C4A1', '#f59e0b', '#ef4444', '#6366f1', '#ec4899'];
  tbody.innerHTML = _convs.map((c, i) => `
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
      <td><span class="badge ${statusBadgeClass(c.status)}">${escHtml(c.status)}</span></td>
      <td>${c.csat_score ? '⭐'.repeat(c.csat_score) : '—'}</td>
      <td style="color:var(--dash-text-3);font-size:12px;font-family:var(--font-mono);">${timeAgo(c.created_at)}</td>
      <td><button class="agent-action-btn agent-action-btn--edit" style="font-size:11px;">View →</button></td>
    </tr>
  `).join('');
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
//  Voices (static data — no DB row needed)
// ═══════════════════════════════════════════════════════════════
const VOICES = [
  { name: 'Priya', lang: 'Hindi/English', gender: 'Female', style: 'Natural', emoji: '👩', gradient: 'linear-gradient(135deg,#ff6b6b,#ffa06b)' },
  { name: 'Rahul', lang: 'Hindi', gender: 'Male', style: 'Professional', emoji: '👨', gradient: 'linear-gradient(135deg,#667eea,#764ba2)' },
  { name: 'Anita', lang: 'English-IN', gender: 'Female', style: 'Warm', emoji: '👩‍💼', gradient: 'linear-gradient(135deg,#f093fb,#f5576c)' },
  { name: 'Arjun', lang: 'English-IN', gender: 'Male', style: 'Confident', emoji: '👨‍💼', gradient: 'linear-gradient(135deg,#4facfe,#00f2fe)' },
  { name: 'Kavya', lang: 'Tamil/English', gender: 'Female', style: 'Friendly', emoji: '💁‍♀️', gradient: 'linear-gradient(135deg,#43e97b,#38f9d7)' },
  { name: 'Amit', lang: 'Bengali/Hindi', gender: 'Male', style: 'Calm', emoji: '🧑‍💻', gradient: 'linear-gradient(135deg,#fa709a,#fee140)' },
];

function _renderVoices() {
  const grid = $('#voices-grid');
  if (!grid) return;
  grid.innerHTML = VOICES.map((v) => `
    <div class="voice-card" tabindex="0" role="button" aria-label="Preview voice ${v.name}">
      <div class="voice-card__header">
        <div class="voice-card__avatar" style="background:${v.gradient}">${v.emoji}</div>
        <div class="voice-card__info">
          <div class="voice-card__name">${v.name}</div>
          <div class="voice-card__meta">${v.gender} · ${v.lang}</div>
        </div>
        <button class="voice-card__play" aria-label="Play ${v.name}">▶</button>
      </div>
      <div class="voice-card__tags">
        <span class="badge badge--gray">${v.style}</span>
        <span class="badge badge--gray">${v.lang}</span>
      </div>
    </div>
  `).join('');
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
};

// ── Resize → re-draw charts ───────────────────────────────────
window.addEventListener('resize', () => {
  const active = $('[id^="page-"].active')?.id;
  if (active === 'page-home') drawLineChart('callChart', _buildChartData('week'));
  if (active === 'page-analytics') drawLineChart('analyticsChart', _buildChartData('month'), '#00C4A1');
}, { passive: true });

// ── Start ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', boot);
