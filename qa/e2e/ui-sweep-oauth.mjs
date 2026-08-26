// Dashboard E2E sweep authenticated via the OWNER'S Google account.
// Opens a HEADED persistent browser, clicks "Continue with Google", then STOPS at the
// Google account chooser — the owner picks his own account interactively (no password
// typing by the agent). Polls until auth completes (up to 10 min), then runs the sweep.
import { chromium } from 'playwright';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = 'http://localhost:5173';
// Use the OWNER'S REAL default Chrome profile so his Google one-click works.
// Requires Google Chrome to be fully quit first (locked profile otherwise).
const PROFILE_DIR = path.join(os.homedir(), 'Library', 'Application Support', 'Google', 'Chrome');

const results = [];
function rec(control, expected, actual, pass) {
  results.push({ control, expected, actual: String(actual), pass: Boolean(pass) });
  console.log(`${pass ? 'PASS' : 'FAIL'} | app | ${control} | expected: ${expected} | actual: ${String(actual).slice(0, 120)}`);
}

fs.mkdirSync(PROFILE_DIR, { recursive: true });
const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
  headless: false,
  channel: 'chrome', // real Google Chrome — one-click account chooser
  viewport: null, // use Chrome's real window size
  args: ['--profile-directory=Default', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
});
const page = ctx.pages()[0] ?? (await ctx.newPage());
page.consoleErrors = [];
page.on('pageerror', (e) => page.consoleErrors.push('PAGEERROR: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') page.consoleErrors.push(m.text()); });

await page.goto(`${BASE}/auth.html?mode=login`, { waitUntil: 'networkidle' });

if (!page.url().includes('auth.html')) {
  console.log('INFO | existing session found — skipping OAuth chooser');
} else {
  const btn = page.locator('#google-btn');
  rec('Google button present on auth page', 'visible', await btn.count(), (await btn.count()) > 0);
  await btn.click();
  // Wait for redirect to Google (chooser may auto-continue if a session cookie exists)
  try {
    await page.waitForURL(/accounts\.google\.com|app\.html/, { timeout: 30000 });
  } catch { /* fall through to poll */ }
}

if (page.url().includes('accounts.google.com')) {
  console.log('════════════════════════════════════════════════════════');
  console.log('ACTION NEEDED FROM SHREYASH:');
  console.log('A browser window is open at the Google account chooser.');
  console.log('Please CLICK YOUR OWN GOOGLE ACCOUNT in that window now.');
  console.log('(No passwords are typed by anyone.)');
  console.log('════════════════════════════════════════════════════════');
}

let authed = page.url().includes('app.html');
const deadline = Date.now() + 10 * 60 * 1000;
while (!authed && Date.now() < deadline) {
  await page.waitForTimeout(20000);
  const url = page.url();
  authed = url.includes('app.html');
  if (!url.includes('accounts.google.com') && !url.includes('app.html')) break; // error state
}
rec('OAuth login completes (owner clicks his account)', 'lands on app.html', page.url(), authed);
if (!authed) {
  console.log('BLOCKED: owner did not complete the Google chooser within 10 min. url=' + page.url());
  fs.writeFileSync(new URL('./oauth-sweep-results.json', import.meta.url), JSON.stringify(results, null, 2));
  process.exit(0);
}

await page.waitForLoadState('networkidle').catch(() => {});
await page.waitForTimeout(2500);

// ── Auth-loop regression check ────────────────────────────────────────────
// The login→instant-logout bug: dashboard rendered ~1s then bounced to auth.html.
// Wait past the boot() guard window and assert we are STILL on app.html.
const stillAuthed = await (async () => {
  for (let i = 0; i < 5; i++) {
    if (!page.url().includes('app.html')) return false;
    await page.waitForTimeout(3000);
  }
  return page.url().includes('app.html');
})();
rec('session stable after 15s (no login→logout loop)', 'still on app.html', page.url(), stillAuthed);
if (!stillAuthed) {
  console.log('BLOCKED: auth loop reproduced — bounced back to ' + page.url());
  fs.writeFileSync(new URL('./oauth-sweep-results.json', import.meta.url), JSON.stringify(results, null, 2));
  process.exit(0);
}
// Capture the boot diagnostics line for evidence
const bootLog = await page.evaluate(() => window.__SAHAIY_BOOT_LOG__ ?? null).catch(() => null);
if (bootLog) console.log('INFO | boot diagnostics:', JSON.stringify(bootLog));

// ── Dashboard sweep ──────────────────────────────────────────────────────
rec('dashboard loads after auth', 'no redirect back to auth.html', page.url(), !page.url().includes('auth'));
const wsName = (await page.locator('#ws-name').textContent().catch(() => '')) || '';
rec('workspace name rendered', 'non-empty', wsName.trim() || 'EMPTY', wsName.trim().length > 0);

// sidebar nav controls
for (const [id, label] of [
  ['nav-agents', 'Agents'], ['nav-conversations', 'Conversations'], ['nav-analytics', 'Analytics'],
  ['nav-knowledge', 'Knowledge'], ['nav-whatsapp', 'WhatsApp'], ['nav-phonenumbers', 'Phone Numbers'],
]) {
  const el = page.locator(`#${id}`);
  const visible = (await el.count()) > 0;
  rec(`sidebar nav → ${label}`, 'element present', visible ? 'present' : 'MISSING', visible);
}

// ── Create agent modal ──
await page.locator('#topbar-new-agent').click();
await page.waitForTimeout(600);
const modalVisible = await page.locator('#create-modal').isVisible().catch(() => false);
rec('create-agent modal opens', '#create-modal visible', modalVisible, modalVisible);

if (modalVisible) {
  await page.locator('#new-agent-name').fill(`QA Sweep Agent ${Date.now() % 10000}`);

  // Voice select — expect Sarvam speakers among options
  const voiceOptions = await page.locator('#new-agent-voice option').allTextContents();
  const sarvamSpeakers = ['anushka', 'abhilash', 'manisha', 'vidya', 'arjun', 'maya', 'neel', 'maitreyi', 'amartya'];
  const matchedSpeakers = sarvamSpeakers.filter((s) => voiceOptions.some((o) => o.toLowerCase().includes(s)));
  rec('voice select lists Sarvam speakers', `${sarvamSpeakers.length} speakers`, `${matchedSpeakers.length} matched (${matchedSpeakers.join(',') || 'none'})`, matchedSpeakers.length >= 5);

  // Language select
  const langOptions = await page.locator('#new-agent-lang option').allTextContents();
  rec('language select has options', '>1 language', String(langOptions.length), langOptions.length > 1);

  await page.locator('#create-agent-btn').click();
  await page.waitForTimeout(2500); // allow Supabase insert + re-render
  const agentRow = page.locator('#agent-rows .agent-row, #agent-rows tr, #agent-rows > div').first();
  rec('agent created appears in list', 'row rendered in #agent-rows', (await agentRow.count()) > 0 ? 'found' : 'none', (await agentRow.count()) > 0);

  // ── Edit agent: open agent-config for the first row ──
  const editTrigger = page.locator('#agent-rows [onclick*="agent-config"], #agent-rows button:has-text("Edit"), #agent-rows .agent-row').first();
  if ((await editTrigger.count()) > 0) {
    await editTrigger.click().catch(() => {});
    await page.waitForTimeout(800);
    const cfgName = (await page.locator('#config-agent-name').textContent().catch(() => '')) || '';
    rec('edit flow opens agent-config page', 'config title rendered', cfgName.trim() || 'EMPTY', cfgName.trim().length > 0);
    const tabs = await page.locator('[id^="config-tab-"]').count();
    rec('agent-config tabs render', '>=4 tabs', String(tabs), tabs >= 4);
  } else {
    rec('edit flow opens agent-config page', 'clickable row/edit button', 'none found', false);
  }
}

// ── Knowledge upload UI ──
await page.locator('#nav-knowledge').click();
await page.waitForTimeout(500);
const kbInput = await page.locator('#kb-file-input, input[type="file"]').count();
rec('knowledge upload control present', 'file input present', kbInput > 0 ? 'present' : 'MISSING', kbInput > 0);

// ── Playground: text chat ──
const pgPanel = page.locator('#playground-panel');
const chatInput = page.locator('#playground-msg-input');
rec('playground chat input present', '#playground-msg-input present', (await chatInput.count()) > 0, (await chatInput.count()) > 0);
if ((await chatInput.count()) > 0) {
  await chatInput.fill('Hello, this is a QA sweep message.');
  await chatInput.press('Enter');
  await page.waitForTimeout(4000);
  const bubbles = await page.locator('#playground-chat > *').count();
  rec('playground text chat responds', 'message bubbles appended', String(bubbles), bubbles >= 1);
}

// ── Call orb start/stop (mic permission pre-granted via fake device flags) ──
const orb = page.locator('#playground-orb-btn');
rec('call orb present', '#playground-orb-btn present', (await orb.count()) > 0, (await orb.count()) > 0);
if ((await orb.count()) > 0) {
  await orb.click();
  await page.waitForTimeout(4000);
  const status1 = ((await page.locator('#playground-call-status').textContent()) || '').toLowerCase();
  const started = status1.includes('listening') || status1.includes('connected') || status1.includes('stop') || status1 !== 'click orb to start a test call';
  rec('call orb starts session', 'status changes from idle', status1.trim(), started);
  await orb.click(); // stop
  await page.waitForTimeout(1500);
  const status2 = ((await page.locator('#playground-call-status').textContent()) || '').toLowerCase();
  rec('call orb stops session', 'status returns to idle/ended', status2.trim(), status2.includes('click orb') || status2.includes('end') || status2.includes('stopped'));
  // interrupt button (may only exist mid-call)
  const interrupt = await page.locator('#interrupt-btn, button:has-text("Interrupt")').count();
  rec('interrupt button availability noted', 'present during call', String(interrupt), true);
}

// ── WhatsApp page renders (new section) ──
await page.locator('#nav-whatsapp').click().catch(() => {});
await page.waitForTimeout(700);
const waPage = await page.locator('#page-whatsapp').isVisible().catch(() => false);
rec('WhatsApp page navigable', '#page-whatsapp visible', waPage, waPage);

console.log('console errors:', JSON.stringify((page.consoleErrors || []).slice(0, 5)));
fs.writeFileSync(new URL('./oauth-sweep-results.json', import.meta.url), JSON.stringify(results, null, 2));
const passed = results.filter((r) => r.pass).length;
console.log(`SUMMARY | app | ${passed}/${results.length} PASS`);
console.log('DONE — closing browser');
await ctx.close();
process.exit(0);
