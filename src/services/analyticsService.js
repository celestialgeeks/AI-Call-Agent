/**
 * src/services/analyticsService.js
 * ──────────────────────────────────
 * Analytics queries — daily stats and seed function.
 */

import { supabase } from '@/services/supabaseClient.js';

/**
 * Fetches daily stats for the past N days.
 * @param {string} userId
 * @param {number} [days=30]
 * @returns {Promise<DailyStat[]>}
 */
export async function getDailyStats(userId, days = 30) {
    const fromDate = new Date(Date.now() - days * 86_400_000)
        .toISOString()
        .split('T')[0];

    const { data, error } = await supabase
        .from('daily_stats')
        .select('*')
        .eq('user_id', userId)
        .gte('date', fromDate)
        .order('date', { ascending: true });

    if (error) { console.error('[analyticsService.getDailyStats]', error); return []; }
    return data ?? [];
}

/**
 * Fetches today's stat row (may be null if none exists yet).
 * @param {string} userId
 * @returns {Promise<DailyStat|null>}
 */
export async function getTodayStats(userId) {
    const today = new Date().toISOString().split('T')[0];
    const { data, error } = await supabase
        .from('daily_stats')
        .select('*')
        .eq('user_id', userId)
        .eq('date', today)
        .single();

    if (error && error.code !== 'PGRST116') {
        // PGRST116 = "No rows found" — expected for new users, not an error
        console.error('[analyticsService.getTodayStats]', error);
    }
    return data ?? null;
}

/**
 * Calls the seed_user_data Postgres function.
 * No-op for existing users (function checks internally).
 * @param {string} userId
 * @returns {Promise<void>}
 */
export async function seedUserData(userId) {
    const { error } = await supabase.rpc('seed_user_data', { p_user_id: userId });
    if (error) { console.error('[analyticsService.seedUserData]', error); }
}

/**
 * Fetches the user's profile row.
 * @param {string} userId
 * @returns {Promise<Profile|null>}
 */
export async function getProfile(userId) {
    const { data, error } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', userId)
        .single();

    if (error) { console.error('[analyticsService.getProfile]', error); return null; }
    return data ?? null;
}

/**
 * Updates the current user's profile row.
 * @param {string} userId
 * @param {object} payload — columns to update (e.g. { full_name })
 * @returns {Promise<{data: object|null, error: Error|null}>}
 */
export async function updateProfile(userId, payload) {
    const { data, error } = await supabase
        .from('profiles')
        .update({ ...payload, updated_at: new Date().toISOString() })
        .eq('id', userId)
        .select()
        .single();
    return { data, error };
}
