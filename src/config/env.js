/**
 * src/config/env.js
 * ─────────────────
 * Centralised environment configuration.
 * All import.meta.env reads are funnelled through here.
 * Components / services import from this file — NEVER from import.meta.env directly.
 *
 * Security notes
 * ──────────────
 * • VITE_SUPABASE_ANON_KEY  → safe to expose: Supabase RLS enforces per-user access
 * • VITE_SUPABASE_URL       → safe to expose: it's just a project endpoint
 * • service_role key        → NEVER place here or in any client-side file
 */

const requiredAny = (keys) => {
    const value = keys
        .map((key) => import.meta.env[key])
        .find((candidate) => Boolean(candidate && !candidate.includes('YOUR_')));

    if (!value) {
        console.error(
            `[Sahaiy] Missing env var. Expected one of: ${keys.join(', ')}\n` +
            `Copy .env.example → .env and fill in the value.\n` +
            `See README.md → "Environment Setup" for details.`
        );
    }
    return value ?? '';
};

const env = {
    /** Supabase project URL — e.g. https://xyzabc.supabase.co */
    supabaseUrl: requiredAny(['VITE_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_URL']),

    /** Supabase anon/public key — safe client-side, RLS enforces access */
    supabaseAnonKey: requiredAny([
        'VITE_SUPABASE_ANON_KEY',
        'NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY',
    ]),

    /** Full app base URL — used for OAuth callback redirects */
    appUrl: import.meta.env.VITE_APP_URL ?? 'http://localhost:5173',

    /** Application display name */
    appName: import.meta.env.VITE_APP_NAME ?? 'Sahaiy',

    /** True when running in production build */
    isProd: import.meta.env.PROD ?? false,

    /** True when running in dev server */
    isDev: import.meta.env.DEV ?? true,
};

export default env;
