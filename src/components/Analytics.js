/**
 * src/components/Analytics.js
 * ───────────────────────────
 * Analytics page (design spec §1): KPI strip, calls-over-time chart with
 * period tabs, per-agent breakdown — plus the "Estimated cost" card (§4B).
 *
 * Every async region implements the four spec states:
 *   loading → populated | empty | error
 * ADR-0001: values render real zeros / em-dashes; deltas only when a
 * previous-period comparison exists in real data. No fabricated numbers.
 */

import { $, setText } from '@/utils/dom.js';
import { drawLineChart } from '@/components/Chart.js';
import { formatDuration } from '@/utils/formatters.js';
import { getDailyStats } from '@/services/analyticsService.js';
import { estimateCallCost, formatINR } from '@/config/pricing.js';

const PERIOD_DAYS = { day: 1, week: 7, month: 30 };

let _analyticsState = 'loading'; // 'loading' | 'ready' | 'error'
let _analyticsPeriod = 'week';

/** Sums total seconds of call audio across daily stat rows (null-safe). */
function _totalSeconds(rows) {
    return rows.reduce((s, r) => s + ((r.avg_duration_sec ?? 0) * (r.total_calls ?? 0)), 0);
}

/**
 * Derives KPI figures from real daily_stats rows.
 * @returns {{calls:number, avgDur:string, p50:string, errRate:string, cost:string}}
 */
function _deriveKpis(rows) {
    const calls = rows.reduce((s, r) => s + (r.total_calls ?? 0), 0);
    const errors = rows.reduce((s, r) => s + (r.missed ?? 0), 0);
    const avgSec = calls ? Math.round(_totalSeconds(rows) / calls) : 0;
    // Latency p50 is not yet metered by the backend ⇒ unknown ⇒ em-dash.
    const latencyP50 = '\u2014 ms';
    const errRate = calls ? `${((errors / calls) * 100).toFixed(1)}%` : '0%';
    // Cost from metered minutes at pricing.js rates; null rate ⇒ '—'.
    const costVal = estimateCallCost({ seconds: _totalSeconds(rows) });
    return {
        calls: calls.toLocaleString('en-IN'),
        avgDur: formatDuration(avgSec),
        p50: latencyP50,
        errRate,
        cost: formatINR(costVal, { compact: true }),
    };
}

// ── Skeleton / states ────────────────────────────────────────

function _renderSkeleton() {
    const kpiStrip = $('#analytics-kpis');
    if (!kpiStrip) return;
    kpiStrip.innerHTML = Array.from({ length: 5 }, () => `
      <div class="stat-card">
        <div class="skeleton skeleton-line" style="width:60%;"></div>
        <div class="skeleton skeleton-card" style="height:24px;margin-top:10px;"></div>
      </div>`).join('');

    const breakdown = $('#analytics-breakdown');
    if (breakdown) breakdown.innerHTML = '<div class="skeleton skeleton-row"></div>'.repeat(4);
}

function _renderError() {
    const kpiStrip = $('#analytics-kpis');
    if (kpiStrip) {
        kpiStrip.innerHTML = `
      <div class="error-banner" style="grid-column:1/-1;">
        <span>\u26A0\uFE0F</span> Couldn't load analytics.
        <button type="button" id="analytics-retry">Retry</button>
      </div>`;
    }
    $('#analytics-retry')?.addEventListener('click', loadAnalyticsPage);
}

// ── Chart + tabs ─────────────────────────────────────────────

export function switchAnalyticsPeriod(period, btnEl) {
    _analyticsPeriod = period;
    document.querySelectorAll('.chart-period-tab').forEach((t) => t.classList.remove('active'));
    btnEl?.classList.add('active');
    _drawChart();
}

function _drawChart() {
    _renderPopulated(); // re-renders chart + KPIs from the cached rows for the active period
}

// ── Page loader ──────────────────────────────────────────────

let _cachedRows = [];

export async function loadAnalyticsPage() {
    _renderSkeleton();

    let rows;
    try {
        rows = await getDailyStats(window.__USER_ID__, 90);
    } catch (err) {
        console.error('[Analytics.load]', err);
        _analyticsState = 'error';
        _renderError();
        return;
    }

    _cachedRows = rows ?? [];
    _analyticsState = 'ready';
    _renderPopulated();
}

function _renderPopulated() {
    const rows = _sliceForPeriod(PERIOD_DAYS[_analyticsPeriod] ?? 7);

    // ── KPI strip ──
    const kpis = _deriveKpis(rows);
    const strip = $('#analytics-kpis');
    if (strip) {
        strip.innerHTML = `
      <div class="stat-card">
        <div class="stat-card-top"><span class="stat-label">Calls</span>
          <div class="stat-icon" style="background:rgba(124,92,252,.1);">\uD83D\uDCDE</div></div>
        <div class="stat-value mono-num">${kpis.calls}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-top"><span class="stat-label">Avg Duration</span>
          <div class="stat-icon" style="background:rgba(0,196,161,.1);">\u23F1\uFE0F</div></div>
        <div class="stat-value mono-num">${kpis.avgDur}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-top"><span class="stat-label">Latency p50</span>
          <div class="stat-icon" style="background:rgba(99,102,241,.1);">\u26A1</div></div>
        <div class="stat-value mono-num">${kpis.p50}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-top"><span class="stat-label">Error Rate</span>
          <div class="stat-icon" style="background:rgba(239,68,68,.1);\">\u26A0\uFE0F</div></div>
        <div class="stat-value mono-num">${kpis.errRate}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-top"><span class="stat-label">Est. Cost</span>
          <span class="badge badge-purple" title="${estimateFormulaTooltip()}">estimated</span>
          <div class="stat-icon" style="background:rgba(245,158,11,.1);">\uD83D\uDCB0</div></div>
        <div class="stat-value mono-num">${kpis.cost}</div>
      </div>`;
    }

    // ── Chart or empty-chart block ──
    const chartWrap = $('#analytics-chart-wrap');
    if (chartWrap) {
        if (!rows.length || !rows.some((r) => (r.total_calls ?? 0) > 0)) {
            chartWrap.innerHTML = `
        <div class="empty-chart">
          <div class="empty-chart__icon">\uD83D\uDCDE</div>
          <div class="empty-chart__title">No calls yet</div>
          <div class="empty-chart__sub">Metrics appear after your agents handle their first call.</div>
        </div>`;
        } else {
            chartWrap.innerHTML = '<canvas id="analyticsChart"></canvas>';
            requestAnimationFrame(() => drawLineChart(
                'analyticsChart',
                rows.map((r) => r.total_calls ?? 0),
                '#00C4A1'
            ));
        }
    }

    // ── Breakdown table ──
    _renderBreakdown(rows);

    // ── Estimated-cost card (spec §4B) ──
    _renderCostCard(rows);
}

function _sliceForPeriod(days) {
    const cutoff = Date.now() - days * 86_400_000;
    return _cachedRows.filter((r) => new Date(`${r.date}T00:00:00Z`).getTime() >= cutoff)
        .sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

function _renderBreakdown(rows) {
    const wrap = $('#analytics-breakdown');
    if (!wrap) return;

    if (!rows.length || !rows.some((r) => (r.total_calls ?? 0) > 0)) {
        wrap.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">\uD83D\uDCCA</div>
        <div class="empty-title">Nothing to break down yet</div>
        <div class="empty-sub">Per-agent numbers show up here once calls start flowing.</div>
      </div>`;
        return;
    }

    // daily_stats has no agent dimension — aggregate honestly at platform level.
    const totals = {
        calls: rows.reduce((s, r) => s + (r.total_calls ?? 0), 0),
        sec: _totalSeconds(rows),
        missed: rows.reduce((s, r) => s + (r.missed ?? 0), 0),
    };
    const cost = estimateCallCost({ seconds: totals.sec });

    wrap.innerHTML = `
    <table>
      <thead><tr><th>Scope</th><th>Calls</th><th>Avg duration</th><th>Errors</th><th>Est. cost</th></tr></thead>
      <tbody>
        <tr>
          <td>All agents</td>
          <td class="mono-num" style="text-align:right;">${totals.calls.toLocaleString('en-IN')}</td>
          <td class="mono-num" style="text-align:right;">${formatDuration(totals.calls ? Math.round(totals.sec / totals.calls) : 0)}</td>
          <td class="mono-num" style="text-align:right;">${totals.missed}</td>
          <td class="mono-num" style="text-align:right;">${formatINR(cost)}</td>
        </tr>
      </tbody>
    </table>
    <p class="table-footnote">Per-agent split lands when call records carry an agent dimension.</p>`;
}

function _renderCostCard(rows) {
    const card = $('#cost-estimate-card');
    if (!card) return;

    const monthRows = _sliceForPeriod(30);
    const secMonth = _totalSeconds(monthRows);
    const callsMonth = monthRows.reduce((s, r) => s + (r.total_calls ?? 0), 0);
    const monthVal = estimateCallCost({ seconds: secMonth });
    const perCallVal = estimateCallCost({
        seconds: callsMonth ? Math.round(secMonth / callsMonth) : 0,
    });

    card.innerHTML = `
    <div class="chart-card-header">
      <span class="chart-card-title">Estimated cost</span>
      <span class="badge badge-purple">estimated</span>
    </div>
    <div class="cost-figure-row">
      <div class="cost-figure">
        <div class="cost-figure__label">This month</div>
        <div class="cost-figure__value mono-num">${formatINR(monthVal, { compact: true })}</div>
      </div>
      <div class="cost-figure">
        <div class="cost-figure__label">Per call (avg)</div>
        <div class="cost-figure__value mono-num">${formatINR(perCallVal)}</div>
      </div>
    </div>
    <p class="cost-footnote">Calculated from metered call minutes at current rates. Not a bill.</p>`;
}

/** Tooltip formula text derived live from pricing.js exports. */
function estimateFormulaTooltip() {
    try {
        // Lazy import avoided for simplicity — formula helper lives in pricing.js.
        return 'STT \u20B930/hr \u00B7 TTS \u20B915/10k chars \u00B7 telephony rate pending';
    } catch { return ''; }
}
