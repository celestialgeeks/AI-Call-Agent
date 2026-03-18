"""
app/services/agent_service.py
──────────────────────────────
Agent CRUD helpers used by the backend.
Fetches agent config from Supabase (server-side, service role).
"""

import logging
from typing import Optional
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)


async def get_agent(agent_id: str) -> Optional[dict]:
    """
    Fetch a single agent row from Supabase by ID.

    Returns:
        Agent row as a dict, or None if not found.
    """
    try:
        supabase = get_supabase()
        result = supabase.table("agents").select("*").eq("id", agent_id).single().execute()
        return result.data
    except Exception as exc:
        logger.error("[AgentService] get_agent(%s) failed: %s", agent_id, exc)
        return None


async def increment_call_count(agent_id: str) -> None:
    """Atomically increment the call_count for an agent."""
    try:
        supabase = get_supabase()
        supabase.rpc("increment_agent_call_count", {"p_agent_id": agent_id}).execute()
    except Exception as exc:
        logger.warning("[AgentService] increment_call_count failed: %s", exc)


async def get_agent_knowledge_doc_texts(agent_id: str) -> list[str]:
    """
    Fetch the text content of all knowledge docs linked to an agent.
    Returns a list of doc name strings (used for vector search / context).
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("agent_knowledge_docs")
            .select("knowledge_docs(name, url, type)")
            .eq("agent_id", agent_id)
            .execute()
        )
        texts = []
        for row in result.data or []:
            doc = row.get("knowledge_docs")
            if doc:
                label = doc.get("name") or doc.get("url") or ""
                if label:
                    texts.append(label)
        return texts
    except Exception as exc:
        logger.error("[AgentService] get_agent_knowledge_doc_texts failed: %s", exc)
        return []
