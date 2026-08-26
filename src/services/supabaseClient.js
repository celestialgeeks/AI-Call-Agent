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

export const supabase = createClient(env.supabaseUrl, env.supabaseAnonKey, {
    auth: {
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: true,
        // PKCE flow: the auth code is exchanged for a session via fetch (stored in
        // localStorage) instead of relying on third-party cookies in the implicit-flow
        // redirect chain. Fixes the login → instant-logout loop where the dashboard
        // booted with no recoverable session and bounced back to auth.html.
        flowType: 'pkce',
    },
});
