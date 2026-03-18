"""
app/routers/calls.py
─────────────────────
REST endpoints for call lifecycle management.
Writes conversation records to Supabase for the dashboard.

POST /agents/{agent_id}/call/start  → creates conversation, returns conversation_id
POST /agents/{agent_id}/call/end    → updates conversation with transcript + duration + CSAT
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Calls"])


class CallStartRequest(BaseModel):
    user_id: str
    caller_name: Optional[str] = None
    caller_number: Optional[str] = None


class CallStartResponse(BaseModel):
    conversation_id: str


class CallEndRequest(BaseModel):
    user_id: str
    conversation_id: str
    transcript: Optional[str] = None
    duration_sec: int = 0
    csat_score: Optional[int] = Field(None, ge=1, le=5)
    status: str = "resolved"


@router.post("/{agent_id}/call/start", response_model=CallStartResponse)
async def call_start(agent_id: str, body: CallStartRequest):
    """
    Create a new conversation record in Supabase when a call begins.
    Returns the conversation_id to track the call.
    """
    try:
        supabase = get_supabase()
        # Fetch agent name for the record
        agent_res = supabase.table("agents").select("name").eq("id", agent_id).single().execute()
        agent_name = agent_res.data.get("name", "AI Agent") if agent_res.data else "AI Agent"

        result = supabase.table("conversations").insert({
            "user_id": body.user_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "caller_name": body.caller_name,
            "caller_number": body.caller_number,
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).select().single().execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create conversation record")

        conv_id = result.data["id"]
        logger.info("[Calls] Started conversation %s for agent %s", conv_id, agent_id)
        return CallStartResponse(conversation_id=conv_id)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Calls] call_start error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{agent_id}/call/end")
async def call_end(agent_id: str, body: CallEndRequest):
    """
    Update the conversation record when a call ends.
    Saves transcript, duration, CSAT score, and final status.
    """
    try:
        supabase = get_supabase()
        update_payload = {
            "status": body.status,
            "duration_sec": body.duration_sec,
            "transcript": body.transcript,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if body.csat_score is not None:
            update_payload["csat_score"] = body.csat_score

        supabase.table("conversations").update(update_payload).eq("id", body.conversation_id).execute()

        # Increment agent call_count
        supabase.table("agents").update({"call_count": supabase.rpc("get_agent_call_count", {"p_id": agent_id})}).eq("id", agent_id)

        logger.info("[Calls] Ended conversation %s (%ds, status=%s)", body.conversation_id, body.duration_sec, body.status)
        return {"ok": True}

    except Exception as exc:
        logger.error("[Calls] call_end error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
