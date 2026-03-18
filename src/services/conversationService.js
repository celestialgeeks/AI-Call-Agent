/**
 * src/services/conversationService.js
 * ─────────────────────────────────────
 * Read / write operations for call conversation records.
 */

import { supabase } from '@/services/supabaseClient.js';
import env from '@/config/env.js';

/**
 * Fetches recent conversations for a user.
 * @param {string} userId
 * @param {number} [limit=50]
 * @returns {Promise<Conversation[]>}
 */
export async function getConversations(userId, limit = 50) {
    const { data, error } = await supabase
        .from('conversations')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false })
        .limit(limit);

    if (error) { console.error('[conversationService.getConversations]', error); return []; }
    return data ?? [];
}

/**
 * Inserts a new conversation record.
 * @param {Partial<Conversation>} payload
 * @returns {Promise<{data: Conversation|null, error: Error|null}>}
 */
export async function addConversation(payload) {
    const { data, error } = await supabase
        .from('conversations')
        .insert(payload)
        .select()
        .single();
    return { data, error };
}

/**
 * Fetches message history for a conversation.
 * @param {string} conversationId
 * @param {number} [limit=200]
 * @returns {Promise<ConversationMessage[]>}
 */
export async function getConversationMessages(conversationId, limit = 200) {
    const { data, error } = await supabase
        .from('conversation_messages')
        .select('*')
        .eq('conversation_id', conversationId)
        .order('created_at', { ascending: true })
        .limit(limit);

    if (error) { console.error('[conversationService.getConversationMessages]', error); return []; }
    return data ?? [];
}

/**
 * Inserts a conversation message row.
 * @param {Partial<ConversationMessage>} payload
 * @returns {Promise<{data: ConversationMessage|null, error: Error|null}>}
 */
export async function addConversationMessage(payload) {
    const { data, error } = await supabase
        .from('conversation_messages')
        .insert(payload)
        .select()
        .single();
    return { data, error };
}

/**
 * Fetches phone numbers with their assigned agent name.
 * @param {string} userId
 * @returns {Promise<PhoneNumber[]>}
 */
export async function getPhoneNumbers(userId) {
    try {
        const response = await fetch(
            `${env.backendUrl}/phone-numbers?user_id=${encodeURIComponent(userId)}`
        );

        if (!response.ok) {
            throw new Error(`Backend request failed (${response.status})`);
        }

        const data = await response.json();
        return Array.isArray(data) ? data : [];
    } catch (apiError) {
        console.warn('[conversationService.getPhoneNumbers] Falling back to Supabase:', apiError);
        const { data, error } = await supabase
            .from('phone_numbers')
            .select('*, agents(name)')
            .eq('user_id', userId)
            .order('created_at', { ascending: false });

        if (error) { console.error('[conversationService.getPhoneNumbers]', error); return []; }
        return data ?? [];
    }
}

/**
 * Deletes a phone number by id.
 * @param {string} phoneNumberId
 * @param {string} userId
 * @returns {Promise<{data: object|null, error: Error|null}>}
 */
export async function deletePhoneNumber(phoneNumberId, userId) {
    try {
        const response = await fetch(
            `${env.backendUrl}/phone-numbers/${encodeURIComponent(phoneNumberId)}?user_id=${encodeURIComponent(userId)}`,
            { method: 'DELETE' }
        );

        if (!response.ok) {
            throw new Error(`Backend delete failed (${response.status})`);
        }

        const data = await response.json();
        return { data, error: null };
    } catch (apiError) {
        console.warn('[conversationService.deletePhoneNumber] Falling back to Supabase:', apiError);
        const { error } = await supabase
            .from('phone_numbers')
            .delete()
            .eq('id', phoneNumberId)
            .eq('user_id', userId);

        if (error) {
            console.error('[conversationService.deletePhoneNumber]', error);
            return { data: null, error };
        }

        return { data: { ok: true, id: phoneNumberId }, error: null };
    }
}

/**
 * Fetches knowledge base documents for a user.
 * @param {string} userId
 * @returns {Promise<KnowledgeDoc[]>}
 */
export async function getKnowledgeDocs(userId) {
    const { data, error } = await supabase
        .from('knowledge_docs')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false });

    if (error) { console.error('[conversationService.getKnowledgeDocs]', error); return []; }
    return data ?? [];
}

/**
 * Adds a new knowledge base document.
 * @param {Partial<KnowledgeDoc>} payload
 * @returns {Promise<{data: KnowledgeDoc|null, error: Error|null}>}
 */
export async function addKnowledgeDoc(payload) {
    const { data, error } = await supabase
        .from('knowledge_docs')
        .insert(payload)
        .select()
        .single();
    return { data, error };
}

/**
 * Fetches tools for a user.
 * @param {string} userId
 * @returns {Promise<Tool[]>}
 */
export async function getTools(userId) {
    const { data, error } = await supabase
        .from('tools')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false });

    if (error) { console.error('[conversationService.getTools]', error); return []; }
    return data ?? [];
}
