// Verify the PKCE client initializes correctly on the dev server: supabase client
// should report flowType pkce and generate an authorize URL containing code_challenge
// (PKCE) rather than response_type=token (implicit). No login performed — just
// inspecting the generated OAuth authorize URL.
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.goto('http://localhost:5173/auth.html?mode=login', { waitUntil: 'networkidle' });

const result = await page.evaluate(async () => {
  // Reach the singleton via the module graph through dynamic import of the app bundle path used by auth.html
  const mod = await import('/src/services/supabaseClient.js');
  const { data } = await mod.supabase.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: `${window.location.origin}/app.html`, skipBrowserRedirect: true },
  });
  return data?.url ?? null;
});
console.log('authorize url:', (result || 'NONE').slice(0, 160));
const isPkce = result && result.includes('code_challenge=');
const isImplicit = result && result.includes('response_type=token');
console.log(isPkce && !isImplicit
  ? 'PASS | signInWithGoogle now issues a PKCE authorize URL (code_challenge present, no implicit token response)'
  : `CHECK | pkce=${Boolean(isPkce)} implicit=${Boolean(isImplicit)}`);
await browser.close();
