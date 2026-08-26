// Verify boot guard behavior on the dev server WITHOUT touching the owner's Chrome:
// fresh headless context = no stored session → expect bounce to auth.html after the
// grace window, with the [Sahaiy Auth] boot diagnostic logged.
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext();
const page = await ctx.newPage();
const logs = [];
page.on('console', m => { if (m.text().includes('[Sahaiy Auth]')) logs.push(m.text()); });

await page.goto('http://localhost:5173/app.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(6500); // longer than the 4s grace window

const url = page.url();
console.log('final url:', url);
console.log('boot diagnostics seen:', JSON.stringify(logs, null, 1));
const bounced = url.includes('auth.html');
const diagLogged = logs.some(l => l.includes('"origin":"http://localhost:5173"') && l.includes('"hasSession":false'));
console.log(bounced && diagLogged
  ? 'PASS | unauthenticated visit bounces to auth.html AFTER diagnostics + grace window'
  : 'CHECK | bounced=' + bounced + ' diagLogged=' + diagLogged);
await browser.close();
