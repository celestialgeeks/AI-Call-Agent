/**
 * src/services/agentService.js
 * ─────────────────────────────
 * CRUD operations for AI agents.
 * All queries are RLS-scoped to the authenticated user.
 */

import { supabase } from '@/services/supabaseClient.js';

/**
 * Fetches all agents for a user, newest first.
 * @param {string} userId
 * @returns {Promise<Agent[]>}
 */
export async function getAgents(userId) {
    const { data, error } = await supabase
        .from('agents')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false });

    if (error) { console.error('[agentService.getAgents]', error); return []; }
    return data ?? [];
}

/**
 * Creates a new agent.
 * @param {Partial<Agent>} payload
 * @returns {Promise<{data: Agent|null, error: Error|null}>}
 */
export async function createAgent(payload) {
    const { data, error } = await supabase
        .from('agents')
        .insert(payload)
        .select()
        .single();
    return { data, error };
}

/**
 * Updates an existing agent by ID.
 * @param {string} id      — agent UUID
 * @param {Partial<Agent>} payload
 * @returns {Promise<{data: Agent|null, error: Error|null}>}
 */
export async function updateAgent(id, payload) {
    const { data, error } = await supabase
        .from('agents')
        .update({ ...payload, updated_at: new Date().toISOString() })
        .eq('id', id)
        .select()
        .single();
    return { data, error };
}

/**
 * Deletes an agent by ID.
 * @param {string} id
 * @returns {Promise<{error: Error|null}>}
 */
export async function deleteAgent(id) {
    const { error } = await supabase.from('agents').delete().eq('id', id);
    return { error };
}

/**
 * Fetches knowledge docs linked to an agent.
 * @param {string} userId
 * @param {string} agentId
 * @returns {Promise<KnowledgeDoc[]>}
 */
export async function getAgentKnowledgeDocs(userId, agentId) {
    const { data, error } = await supabase
        .from('agent_knowledge_docs')
        .select('knowledge_docs(*)')
        .eq('user_id', userId)
        .eq('agent_id', agentId)
        .order('created_at', { ascending: false });

    if (error) { console.error('[agentService.getAgentKnowledgeDocs]', error); return []; }
    return (data ?? []).map((row) => row.knowledge_docs).filter(Boolean);
}

/**
 * Replaces agent-document links for an agent.
 * @param {string} userId
 * @param {string} agentId
 * @param {string[]} docIds
 * @returns {Promise<{error: Error|null}>}
 */
export async function setAgentKnowledgeDocs(userId, agentId, docIds = []) {
    const { error: deleteError } = await supabase
        .from('agent_knowledge_docs')
        .delete()
        .eq('user_id', userId)
        .eq('agent_id', agentId);

    if (deleteError) return { error: deleteError };
    if (!docIds.length) return { error: null };

    const rows = docIds.map((docId) => ({ user_id: userId, agent_id: agentId, doc_id: docId }));
    const { error: insertError } = await supabase
        .from('agent_knowledge_docs')
        .insert(rows);

    return { error: insertError ?? null };
}
