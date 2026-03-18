/**
 * src/services/realtimeService.js
 * ────────────────────────────────
 * Supabase Realtime subscriptions.
 * Each subscription returns the Supabase channel instance.
 * Call channel.unsubscribe() to tear down.
 */

import { supabase } from '@/services/supabaseClient.js';

/** @type {import('@supabase/supabase-js').RealtimeChannel[]} */
const _channels = [];

/**
 * Subscribes to new conversation INSERT events for a user.
 * @param {string}   userId
 * @param {function} onInsert  — called with the new Conversation row
 * @returns {import('@supabase/supabase-js').RealtimeChannel}
 */
export function subscribeToConversations(userId, onInsert) {
    const channel = supabase
        .channel(`conversations:${userId}`)
        .on(
            'postgres_changes',
            {
                event: 'INSERT',
                schema: 'public',
                table: 'conversations',
                filter: `user_id=eq.${userId}`,
            },
            (payload) => onInsert(payload.new)
        )
        .subscribe();

    _channels.push(channel);
    return channel;
}

/**
 * Subscribes to all agent changes (INSERT, UPDATE, DELETE) for a user.
 * @param {string}   userId
 * @param {function} onChange  — called with the full Supabase payload
 * @returns {import('@supabase/supabase-js').RealtimeChannel}
 */
export function subscribeToAgents(userId, onChange) {
    const channel = supabase
        .channel(`agents:${userId}`)
        .on(
            'postgres_changes',
            {
                event: '*',
                schema: 'public',
                table: 'agents',
                filter: `user_id=eq.${userId}`,
            },
            (payload) => onChange(payload)
        )
        .subscribe();

    _channels.push(channel);
    return channel;
}

/**
 * Subscribes to message INSERT events for one conversation.
 * @param {string}   conversationId
 * @param {function} onInsert  — called with the new ConversationMessage row
 * @returns {import('@supabase/supabase-js').RealtimeChannel}
 */
export function subscribeToConversationMessages(conversationId, onInsert) {
    const channel = supabase
        .channel(`conversation_messages:${conversationId}`)
        .on(
            'postgres_changes',
            {
                event: 'INSERT',
                schema: 'public',
                table: 'conversation_messages',
                filter: `conversation_id=eq.${conversationId}`,
            },
            (payload) => onInsert(payload.new)
        )
        .subscribe();

    _channels.push(channel);
    return channel;
}

/**
 * Unsubscribes all active channels registered by this service.
 * Call on page unload / sign-out.
 */
export function unsubscribeAll() {
    _channels.forEach((ch) => ch.unsubscribe());
    _channels.length = 0;
}
