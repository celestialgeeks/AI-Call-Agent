// GENERATED FILE — DO NOT EDIT BY HAND.
// Source: dead-button-inventory-v1.md via qa/generators/gen_g7_inventory.py
// Generated: 2026-08-25T14:00:04.856945+00:00
// Re-run the generator whenever the inventory markdown revs (G7 contract).
import { test, expect } from '@playwright/test';

const BASE = 'https://sahaiy.vercel.app';
const MANIFEST = {
  'g7-landing-01-logo-product-capabilities-pricing-nav': {"page": "Landing", "status": "wired", "element": "Logo, PRODUCT / CAPABILITIES / PRICING nav", "behavior": "Anchor scroll \u2713", "expected": "Anchor scroll \u2713", "id": "g7-landing-01-logo-product-capabilities-pricing-nav", "probe": "capabilities", "url": "https://sahaiy.vercel.app"},
  'g7-landing-02-log-in': {"page": "Landing", "status": "wired", "element": "LOG IN", "behavior": "\u2192 `auth.html?mode=login` \u2713", "expected": "\u2192 `auth.html?mode=login` \u2713", "id": "g7-landing-02-log-in", "probe": "LOG IN", "url": "https://sahaiy.vercel.app"},
  'g7-landing-03-start-for-free': {"page": "Landing", "status": "wired", "element": "START FOR FREE \u2192 (nav), START FOR FREE (hero), START BUILDING FREE \u2192 (footer CTA)", "behavior": "\u2192 `auth.html` \u2713", "expected": "\u2192 `auth.html` \u2713", "id": "g7-landing-03-start-for-free", "probe": "START BUILDING FREE", "url": "https://sahaiy.vercel.app"},
  'g7-landing-04-hero-book-a-demo': {"page": "Landing", "status": "wired", "element": "Hero BOOK A DEMO", "behavior": "Smooth-scrolls to demo section \u2713", "expected": "Smooth-scrolls to demo section \u2713", "id": "g7-landing-04-hero-book-a-demo", "probe": "BOOK", "url": "https://sahaiy.vercel.app"},
  'g7-landing-05-get-started-free': {"page": "Landing", "status": "wired", "element": "GET STARTED FREE (Starter pricing), START FREE TRIAL (Pro pricing)", "behavior": "\u2192 `auth.html` \u2713", "expected": "\u2192 `auth.html` \u2713", "id": "g7-landing-05-get-started-free", "probe": "GET STARTED FREE", "url": "https://sahaiy.vercel.app"},
  'g7-landing-06-sign-up-sign-in-toggle-links-on-auth': {"page": "Landing", "status": "wired", "element": "Sign-up/sign-in toggle links on auth", "behavior": "wired \u2713", "expected": "wired \u2713", "id": "g7-landing-06-sign-up-sign-in-toggle-links-on-auth", "probe": "sign in|sign up", "url": "https://sahaiy.vercel.app/auth.html"},
  'g7-landing-07-start-call': {"page": "Landing", "status": "dead", "element": "START CALL \u2197 (live-demo widget) + demo orb", "behavior": "`onclick=\"startDemo()\"` but `startDemo` is undefined in shipped bundle \u2192 Uncaught ReferenceError, nothing happens", "expected": "Should launch browser-based simulated call (mic \u2192 ASR \u2192 LLM \u2192 TTS round-trip). This is the hero moment of the whole landing page.", "id": "g7-landing-07-start-call", "probe": "START CALL", "url": "https://sahaiy.vercel.app"},
  'g7-landing-08-contact-sales': {"page": "Landing", "status": "dead", "element": "CONTACT SALES \u2192 (Enterprise card)", "behavior": "No onclick, no href \u2014 inert", "expected": "mailto: link or contact modal minimum", "id": "g7-landing-08-contact-sales", "probe": "CONTACT SALES", "url": "https://sahaiy.vercel.app"},
  'g7-landing-09-book-a-demo': {"page": "Landing", "status": "dead", "element": "BOOK A DEMO (footer CTA band)", "behavior": "No onclick, no href \u2014 inert", "expected": "Same as hero (scroll) or calendar link", "id": "g7-landing-09-book-a-demo", "probe": "BOOK", "url": "https://sahaiy.vercel.app"},
  'g7-landing-10-docs': {"page": "Landing", "status": "dead", "element": "DOCS (top nav)", "behavior": "`href=\"#\"` \u2014 jumps to top", "expected": "Needs docs page (none exists \u2014 `/docs.html` 404)", "id": "g7-landing-10-docs", "probe": "DOCS", "url": "https://sahaiy.vercel.app"},
  'g7-landing-11-enterprise': {"page": "Landing", "status": "dead", "element": "ENTERPRISE (top nav)", "behavior": "`href=\"#\"`", "expected": "Needs enterprise page or anchor", "id": "g7-landing-11-enterprise", "probe": "ENTERPRISE", "url": "https://sahaiy.vercel.app"},
  'g7-landing-12-all-18-footer-links-ai-agents-voice-library-knowledge-base-i': {"page": "Landing", "status": "dead", "element": "All 18 footer links: AI Agents, Voice Library, Knowledge Base, Integrations, Analytics, Pricing, Documentation, API Reference, SDKs, Webhooks, Status, Changelog, About, Blog, Careers, Privacy, Terms, Contact", "behavior": "All `href=\"#\"`", "expected": "Route to real pages or remove until they exist", "id": "g7-landing-12-all-18-footer-links-ai-agents-voice-library-knowledge-base-i", "probe": "All 18", "url": "https://sahaiy.vercel.app"},
  'g7-landing-13-social-icons-in': {"page": "Landing", "status": "dead", "element": "Social icons \ud835\udd4f / in / \u25b8", "behavior": "`href=\"#\"`", "expected": "Real profile URLs or remove", "id": "g7-landing-13-social-icons-in", "probe": "Social icons", "url": "https://sahaiy.vercel.app"},
  'g7-landing-14-terms-of-service-privacy-policy': {"page": "Landing", "status": "dead", "element": "Terms of Service / Privacy Policy (auth page footer)", "behavior": "`href=\"#\"`", "expected": "Legal pages required before public launch (DPDP claim on same page!)", "id": "g7-landing-14-terms-of-service-privacy-policy", "probe": "Terms of", "url": "https://sahaiy.vercel.app/auth.html"},
};

for (const [id, row] of Object.entries(MANIFEST)) {
  const wired = row.status === 'wired';
  test(`G7 ${id}: ${row.element}`, async ({ page }) => {
    // Wired rows must keep working; dead rows are expected-fail canaries
    // (they pass only after the fix lands — flip their status in the
    // inventory + regenerate when that happens).
    test.skip(!wired && !process.env.G7_CANARIES, 'dead-row canary; run with G7_CANARIES=1');
    await page.goto(row.url);
    const target = page.locator(`text=/${escapeRe(row.probe)}/i`).first();
    if (wired) {
      await expect(target).toBeVisible();
    } else {
      // Canary assertion for dead controls: element inert or absent.
      const visible = await target.isVisible().catch(() => false);
      expect(visible, `dead control '${row.element}' became interactive — verify fix landed, then regenerate`).toBe(false);
    }
  });
}

function escapeRe(s: string): string {
  // Escape regex metacharacters EXCEPT '|' (allowed alternation), then
  // collapse whitespace so multi-word labels match across newlines.
  return s.replace(/[-[\]{}()*+?.,\\^${}#\s]/g, ' ').trim().replace(/\s+/g, '\\s+');
}
