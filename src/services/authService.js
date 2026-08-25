/**
 * src/services/authService.js
 * ───────────────────────────
 * All authentication operations — sign up, sign in, OAuth, session, sign out.
 * No business logic here; this is a pure data/auth layer.
 */

import { supabase } from '@/services/supabaseClient.js';
import env from '@/config/env.js';

/** OAuth redirect target — always the dashboard after sign-in */
const OAUTH_REDIRECT = `${env.appUrl}/app.html`;

/**
 * Returns the current session or null if unauthenticated.
 * @returns {Promise<import('@supabase/supabase-js').Session|null>}
 */
export async function getSession() {
    const { data } = await supabase.auth.getSession();
    return data.session ?? null;
}

/**
 * Subscribes to Supabase auth state changes (SIGNED_IN, SIGNED_OUT, TOKEN_REFRESHED…).
 * @param {(event: string, session: import('@supabase/supabase-js').Session|null) => void} callback
 * @returns {{ data: { subscription: { unsubscribe: () => void } } }}
 */
export function onAuthStateChange(callback) {
    return supabase.auth.onAuthStateChange((event, session) => callback(event, session));
}

/**
 * Returns the currently authenticated user or null.
 * @returns {Promise<import('@supabase/supabase-js').User|null>}
 */
export async function getUser() {
    const { data } = await supabase.auth.getUser();
    return data.user ?? null;
}

/**
 * Initiates Google OAuth flow. Browser will redirect to Google then back.
 * @returns {Promise<{error: Error|null}>}
 */
export async function signInWithGoogle() {
    const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: OAUTH_REDIRECT },
    });
    return { error };
}

/**
 * Initiates GitHub OAuth flow.
 * @returns {Promise<{error: Error|null}>}
 */
export async function signInWithGitHub() {
    const { error } = await supabase.auth.signInWithOAuth({
        provider: 'github',
        options: { redirectTo: OAUTH_REDIRECT },
    });
    return { error };
}

/**
 * Signs in with email + password.
 * @param {string} email
 * @param {string} password
 * @returns {Promise<{data: object|null, error: Error|null}>}
 */
export async function signInWithEmail(email, password) {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    return { data, error };
}

/**
 * Creates a new account with email + password.
 * @param {string} email
 * @param {string} password
 * @param {{ full_name: string, name: string }} meta  — stored in user_metadata
 * @returns {Promise<{data: object|null, error: Error|null}>}
 */
export async function signUpWithEmail(email, password, meta = {}) {
    const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { data: meta },
    });
    return { data, error };
}

/**
 * Sends a password-reset email to the given address.
 * @param {string} email
 * @returns {Promise<{error: Error|null}>}
 */
export async function resetPassword(email) {
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${env.appUrl}/auth.html`,
    });
    return { error };
}

/**
 * Signs the current user out and clears the session.
 * @returns {Promise<void>}
 */
export async function signOut() {
    await supabase.auth.signOut();
}
