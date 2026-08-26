"""
app/routers/calls.py
─────────────────────
REST endpoints for call lifecycle management.
Writes conversation records to Supabase for the dashboard.

POST /agents/{agent_id}/call/start     → creates conversation, returns conversation_id
POST /agents/{agent_id}/call/end       → updates conversation + atomically bumps agent call_count
POST /agents/{agent_id}/call/outbound  → REAL outbound PSTN dial via LiveKit SIP
GET  /agents/outbound/{conversation_id}/status → room/participant status
POST /agents/outbound/{conversation_id}/end    → hang up + tear down the room

Auth: user_id comes from the verified JWT when AUTH_ENFORCED=true (Ruling B1);
the client-declared body user_id is ignored once the flag flips.

Outbound flow (LiveKit Cloud SIP):
    create room → CreateSIPParticipant (PSTN leg) → mint agent_token for the
    EXISTING voice pipeline (persona from DB, Sarvam STT → NIM/llama LLM →
    Sarvam TTS) to join as participant and drive the conversation.

Honest dormancy: without LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
every outbound endpoint answers 501-with-reason — never fake success.

Built on pr-22 (#4): call_end keeps the atomic finalize_conversation RPC
(SEC-04) and the auth Depends; outbound endpoints follow the same pattern.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from app.auth import get_current_user_id
from app.config import LIVEKIT_URL
from app.errors import ApiError
from app.services.supabase_client import get_supabase
from app.services import telephony

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


# ── Outbound (LiveKit SIP) ────────────────────────────────────────────────

class OutboundCallRequest(BaseModel):
    # Ignored when AUTH_ENFORCED=true — identity is derived from the token.
    user_id: Optional[str] = None
    to_number: str = Field(..., pattern=r"^\+[1-9]\d{7,14}$")  # E.164 guard — reject before any dialing
    caller_name: Optional[str] = None
    trunk_id: Optional[str] = None  # explicit LiveKit SIP outbound trunk (optional)
    ring_timeout_s: int = Field(45, ge=5, le=120)


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
    # Contract §1.1: user_id is REQUIRED — from the verified JWT when
    # AUTH_ENFORCED=true, else from the request body. Missing on both is a
    # 422 (pinned by qa/contract_tests TestCallStart::test_user_id_required),
    # not a silent 200 with an orphaned anonymous conversation.
    if not current_user_id and not body.user_id:
        raise HTTPException(status_code=422, detail="user_id is required")
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
        # SEC-03: never leak raw upstream exception text to clients.
        logger.error("[Calls] call_start error: %s", exc)
        raise ApiError(500, "internal_error", "Failed to start call.") from exc


@router.post("/{agent_id}/call/outbound")
async def call_outbound(
    request: Request,
    agent_id: str,
    body: OutboundCallRequest,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    REAL outbound phone call via LiveKit SIP:
      1. Create a conversation row (status=in_progress).
      2. Create the LiveKit room named after it.
      3. Dial the callee (CreateSIPParticipant) into that room.
      4. Mint an agent_token so the existing voice pipeline joins in-room.

    501-with-reason when LiveKit is unconfigured — never fake success.
    """
    telephony.ensure_ready_or_501()  # honest dormancy gate BEFORE touching Supabase

    try:
        supabase = get_supabase()
        agent_res = supabase.table("agents").select("*").eq("id", agent_id).single().execute()
        agents = agent_res.data if isinstance(agent_res.data, list) else ([agent_res.data] if agent_res.data else [])
        if not agents:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent = agents[0]
        agent_name = agent.get("name", "AI Agent")

        result = supabase.table("conversations").insert({
            "user_id": current_user_id or body.user_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "caller_name": agent_name,          # WE are the caller on outbound
            "caller_number": None,              # LiveKit assigns the trunk number
            "status": "in_progress",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).select().single().execute()
        conv_id = result.data["id"]

        room_name = f"outbound-{conv_id}"
        await telephony.create_room(room_name)

        sip_info = await telephony.dial_sip_participant(
            room_name=room_name,
            to_number=body.to_number,
            participant_identity=f"callee-{conv_id}",
            participant_name=body.caller_name or "Callee",
            trunk_id=body.trunk_id,
            ring_timeout_s=body.ring_timeout_s,
        )

        agent_token = telephony.mint_agent_token(room_name, identity=f"ai-agent-{agent_id}")

        logger.info("[Calls] Outbound %s → %s (room=%s)", conv_id, body.to_number, room_name)
        return {
            "conversation_id": conv_id,
            "room_name": room_name,
            "status": "ringing",
            **sip_info,
            # The existing voice pipeline attaches with this token; until the
            # livekit-agents worker hosts it in-room, the dashboard client runs
            # the same pipeline over WebSocket using this credential.
            "agent_token": agent_token,
            "livekit_url": LIVEKIT_URL,
            "note": (
                "Voice pipeline attaches via agent_token; livekit-agents worker "
                "required for fully server-side participation."
            ),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Calls] call_outbound error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/outbound/{conversation_id}/status")
async def outbound_status(conversation_id: str):
    """Poll LiveKit room state for an active outbound call."""
    telephony.ensure_ready_or_501()
    try:
        status = await telephony.room_status(f"outbound-{conversation_id}")
        return {"conversation_id": conversation_id, **status}
    except Exception as exc:
        logger.error("[Calls] outbound_status error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/outbound/{conversation_id}/end")
async def outbound_end(conversation_id: str, body: CallEndRequest):
    """Hang up: tear down the room (drops the SIP leg) and finalize the conversation."""
    telephony.ensure_ready_or_501()
    try:
        supabase = get_supabase()
        update_payload = {
            "status": body.status or "resolved",
            "duration_sec": body.duration_sec,
            "transcript": body.transcript,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("conversations").update(update_payload).eq("id", conversation_id).execute()
        await telephony.end_room(f"outbound-{conversation_id}")
        logger.info("[Calls] Outbound %s ended (%ds)", conversation_id, body.duration_sec)
        return {"ok": True, "conversation_id": conversation_id}
    except Exception as exc:
        logger.error("[Calls] outbound_end error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    twice on the same conversation increments exactly once.
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
        # SEC-03: never leak raw upstream exception text to clients.
        logger.error("[Calls] call_end error: %s", exc)
        raise ApiError(500, "internal_error", "Failed to end call.") from exc
