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

from app.config import LIVEKIT_URL
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


# ════════════════════════════════════════════════════════════════════════════
#  Outbound PSTN calling (LiveKit SIP)
# ════════════════════════════════════════════════════════════════════════════

class OutboundCallRequest(BaseModel):
    user_id: str
    to_number: str = Field(..., pattern=r"^\+?[1-9]\d{7,14}$", description="E.164, e.g. +919876543210")
    caller_name: str = "AI Agent"


class OutboundCallResponse(BaseModel):
    conversation_id: str
    room_name: str
    agent_token: str
    server_url: str


class OutboundCallEndRequest(BaseModel):
    conversation_id: str
    status: str = "resolved"
    transcript: Optional[str] = None
    duration_sec: int = 0


@router.post("/{agent_id}/call/outbound", response_model=OutboundCallResponse)
async def outbound_call(agent_id: str, body: OutboundCallRequest):
    """
    Place an outbound PSTN call to body.to_number via LiveKit SIP:
      1. Create the LiveKit room.
      2. Dial the callee as a SIP participant.
      3. Mint an access token for the AI participant (dashboard client joins
         with it today; a livekit-agents worker can reuse it later).
    Conversation row tracks the call for the dashboard.
    """
    from app.services import telephony

    telephony.ensure_ready_or_501()  # 501-with-reason when unconfigured

    try:
        supabase = get_supabase()
        agent_res = supabase.table("agents").select("name").eq("id", agent_id).single().execute()
        agent_name = agent_res.data.get("name", "AI Agent") if agent_res.data else "AI Agent"

        conv = supabase.table("conversations").insert({
            "user_id": body.user_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "caller_name": agent_name,
            "caller_number": body.to_number,
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).select().single().execute()
        if not conv.data:
            raise HTTPException(status_code=500, detail="Failed to create conversation record")
        conv_id = conv.data["id"]

        # Room named after the conversation so /calls/outbound/{id}/status maps 1:1
        room_name = f"outbound-{conv_id}"
        await telephony.create_room(room_name)

        dial_info = await telephony.dial_sip_participant(
            room_name=room_name,
            to_number=body.to_number if body.to_number.startswith("+") else f"+{body.to_number}",
            participant_identity=f"sip-{conv_id}",
            participant_name=body.caller_name,
        )
        logger.info("[Calls] outbound %s → %s (dial=%s)", conv_id, body.to_number, dial_info)

        token = telephony.mint_agent_token(room_name, identity=f"ai-agent-{conv_id}")

        return OutboundCallResponse(
            conversation_id=conv_id,
            room_name=room_name,
            agent_token=token,
            server_url=LIVEKIT_URL,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Calls] outbound_call error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/outbound/{conversation_id}/end")
async def outbound_call_end(conversation_id: str, body: OutboundCallEndRequest):
    """Hang up an outbound call by deleting its LiveKit room and close the record."""
    from app.services import telephony

    telephony.ensure_ready_or_501()

    try:
        room_name = f"outbound-{conversation_id}"
        await telephony.end_room(room_name)

        get_supabase().table("conversations").update({
            "status": body.status,
            "transcript": body.transcript,
            "duration_sec": body.duration_sec,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", conversation_id).execute()

        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Calls] outbound_call_end error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/outbound/{conversation_id}/status")
async def outbound_call_status(conversation_id: str):
    """
    Live call status polled by the dashboard:
      {exists, active, num_participants} — active means ≥2 participants joined
    (SIP leg + AI participant), i.e. the callee picked up.
    """
    from app.services import telephony

    telephony.ensure_ready_or_501()

    try:
        status = await telephony.room_status(f"outbound-{conversation_id}")
        return {"conversation_id": conversation_id, **status}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Calls] outbound_status error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
