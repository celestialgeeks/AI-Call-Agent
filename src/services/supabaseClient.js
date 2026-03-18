/**
 * src/services/supabaseClient.js
 * ──────────────────────────────
 * Singleton Supabase client.
 * Import this wherever you need Supabase — it is initialised exactly once.
 *
 * ❌  Do NOT import createClient from @supabase/supabase-js anywhere else.
 * ✅  Import { supabase } from '@/services/supabaseClient.js' instead.
 */

import { createClient } from '@supabase/supabase-js';
import env from '@/config/env.js';

// Use placeholder values when credentials are absent so the module always
// loads successfully.  All Supabase calls will return errors until real
// credentials are provided in a .env file — see .env.example for setup.
const _url = env.supabaseUrl || 'https://placeholder.supabase.co';
const _key = env.supabaseAnonKey || 'placeholder-anon-key';

export const supabase = createClient(_url, _key, {
    auth: {
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: true,
    },
});

/** True when both Supabase env vars are filled in. */
export const isConfigured =
    Boolean(env.supabaseUrl) && !env.supabaseUrl.includes('YOUR_') &&
    Boolean(env.supabaseAnonKey) && !env.supabaseAnonKey.includes('YOUR_');
