/**
 * src/services/conversationService.js
 * ─────────────────────────────────────
 * Read / write operations for call conversation records.
 */

import { supabase } from '@/services/supabaseClient.js';

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
 * Fetches phone numbers with their assigned agent name.
 * @param {string} userId
 * @returns {Promise<PhoneNumber[]>}
 */
export async function getPhoneNumbers(userId) {
    const { data, error } = await supabase
        .from('phone_numbers')
        .select('*, agents(name)')
        .eq('user_id', userId)
        .order('created_at', { ascending: false });

    if (error) { console.error('[conversationService.getPhoneNumbers]', error); return []; }
    return data ?? [];
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
