"""
app/routers/calls.py
─────────────────────
REST endpoints for call lifecycle management.
Writes conversation records to Supabase for the dashboard.

POST /agents/{agent_id}/call/start  → creates conversation, returns conversation_id
POST /agents/{agent_id}/call/end    → updates conversation + atomically bumps agent call_count

Auth: user_id comes from the verified JWT when AUTH_ENFORCED=true (Ruling B1);
the client-declared body user_id is ignored once the flag flips.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional

from app.auth import get_current_user_id
from app.errors import ApiError, new_request_id
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Calls"])


class CallStartRequest(BaseModel):
    # Ignored when AUTH_ENFORCED=true — identity is derived from the token.
    user_id: Optional[str] = None
    caller_name: Optional[str] = None
    caller_number: Optional[str] = None


class CallStartResponse(BaseModel):
    conversation_id: str


class CallEndRequest(BaseModel):
    # Ignored when AUTH_ENFORCED=true — identity is derived from the token.
    user_id: Optional[str] = None
    conversation_id: str
    transcript: Optional[str] = None
    duration_sec: int = 0
    csat_score: Optional[int] = Field(None, ge=1, le=5)
    status: str = "resolved"


@router.post("/{agent_id}/call/start", response_model=CallStartResponse)
async def call_start(
    request: Request,
    agent_id: str,
    body: CallStartRequest,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
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
            "user_id": current_user_id or body.user_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "caller_name": body.caller_name,
            "caller_number": body.caller_number,
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).select().single().execute()

        if not result.data:
            raise ApiError(500, "conversation_create_failed",
                           "Failed to create conversation record.")

        conv_id = result.data["id"]
        logger.info("[Calls] Started conversation %s for agent %s", conv_id, agent_id)
        return CallStartResponse(conversation_id=conv_id)

    except ApiError:
        raise
    except Exception as exc:
        rid = getattr(request.state, "request_id", new_request_id())
        logger.error("[Calls] call_start error (request_id=%s): %s", rid, exc)
        raise ApiError(500, "internal_error", "Failed to start call.") from exc


@router.post("/{agent_id}/call/end")
async def call_end(
    request: Request,
    agent_id: str,
    body: CallEndRequest,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Update the conversation record when a call ends.
    Saves transcript, duration, CSAT score, and final status.

    SEC-04: the agent call_count is bumped atomically server-side via the
    `finalize_conversation` Postgres function, which performs the terminal
    status transition AND the increment in one guarded statement — calling end
    twice on the same conversation increments exactly once. The old broken
    pattern `.update({"call_count": supabase.rpc("get_agent_call_count", …)})`
    referenced a nonexistent RPC and silently no-oped; it is removed.
    """
    try:
        supabase = get_supabase()

        rpc_result = (
            supabase.rpc("finalize_conversation", {
                "p_conversation_id": body.conversation_id,
                "p_status": body.status,
                "p_duration_sec": body.duration_sec,
                "p_transcript": body.transcript,
                "p_csat_score": body.csat_score,
            })
            .execute()
        )
        updated = bool((getattr(rpc_result, "data", None) or {}).get("updated"))

        if not updated:
            # Conversation not found or already finalized — idempotent no-op.
            logger.info("[Calls] call_end on non-in_progress conversation %s — no-op",
                        body.conversation_id)
            return {"ok": True}

        logger.info("[Calls] Ended conversation %s (%ds, status=%s)",
                    body.conversation_id, body.duration_sec, body.status)
        return {"ok": True}

    except ApiError:
        raise
    except Exception as exc:
        rid = getattr(request.state, "request_id", new_request_id())
        logger.error("[Calls] call_end error (request_id=%s): %s", rid, exc)
        raise ApiError(500, "internal_error", "Failed to end call.") from exc
