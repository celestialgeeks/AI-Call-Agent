"""
app/services/telephony.py
─────────────────────────
Outbound PSTN calling over LiveKit Cloud SIP.

Flow implemented here:
    1. Create a LiveKit room named after the conversation.
    2. Dial the callee via CreateSIPParticipant (LiveKit bridges PSTN ↔ room).
    3. Mint an access token so an AI participant joins and speaks.

The AI participant runs the EXISTING voice pipeline (persona from DB +
Sarvam STT → NIM/llama LLM → Sarvam TTS). When `livekit-agents` is
installable it hosts that loop inside the room as a worker; until then the
pipeline attaches via the same WebSocket path the dashboard playground uses
(agent_token lets an authorized client drive the pipeline in-room).

Honest limitation: livekit-agents is pinned `<3.14` in requirements.txt
(watchfiles builds fail on this machine's Python 3.14), so on Python 3.14
the in-worker participant is unavailable — reported, never faked.
"""

import logging
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException

from app.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL

logger = logging.getLogger(__name__)

try:
    from livekit import api
    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False


def livekit_ready() -> tuple[bool, str]:
    """(ready, reason) — reason names the first missing prerequisite."""
    if not _LIVEKIT_AVAILABLE:
        return False, "LiveKit SDK not installed on server."
    missing = [
        name
        for name, val in (
            ("LIVEKIT_URL", LIVEKIT_URL),
            ("LIVEKIT_API_KEY", LIVEKIT_API_KEY),
            ("LIVEKIT_API_SECRET", LIVEKIT_API_SECRET),
        )
        if not val
    ]
    if missing:
        return False, f"LiveKit credentials not configured in .env (missing: {', '.join(missing)})"
    return True, ""


def _lkapi():
    return api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)


async def create_room(room_name: str) -> None:
    """Create (or no-op when it already exists) the call room."""
    lk = _lkapi()
    try:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name, empty_timeout=300))
        logger.info("[Telephony] created room %s", room_name)
    finally:
        await lk.aclose()


async def dial_sip_participant(
    room_name: str,
    to_number: str,
    participant_identity: str,
    participant_name: str = "Caller",
    trunk_id: Optional[str] = None,
    ring_timeout_s: int = 45,
) -> dict:
    """
    Place the outbound PSTN call through a LiveKit SIP outbound trunk.

    trunk_id: optional explicit trunk; omitted → LiveKit uses the project's
    default outbound trunk (the normal setup for single-trunk projects).

    Returns {participant_id, sip_call_id} identity info from LiveKit.
    """
    lk = _lkapi()
    try:
        kwargs: dict = {
            "sip_call_to": to_number,  # E.164, e.g. "+919****3210"
            "room_name": room_name,
            "participant_identity": participant_identity,
            "participant_name": participant_name,
            "play_ringtone": True,
            "ringing_timeout": timedelta(seconds=ring_timeout_s),
        }
        if trunk_id:
            kwargs["sip_trunk_id"] = trunk_id
        req = api.CreateSIPParticipantRequest(**kwargs)
        participant = await lk.sip.create_sip_participant(req)
        logger.info("[Telephony] dialed %s into %s", to_number, room_name)
        return {"participant_id": getattr(participant, "participant_id", "") or "",
                "sip_call_id": getattr(participant, "sip_call_id", "") or ""}
    finally:
        await lk.aclose()


def mint_agent_token(room_name: str, identity: str, ttl_minutes: int = 60) -> str:
    """Access token for the AI voice-pipeline participant joining the call room."""
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("AI Agent")
        .with_grants(api.VideoGrants(room_join=True, room=room_name,
                                     can_publish=True, can_subscribe=True))
        .with_ttl(timedelta(minutes=ttl_minutes))
        .to_jwt()
    )
    return token


async def room_status(room_name: str) -> dict:
    """Lightweight room probe used for live call-status polling."""
    lk = _lkapi()
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
        if not rooms.rooms:
            return {"exists": False, "num_participants": 0}
        participants = await lk.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
        identities = [p.identity for p in participants.participants]
        return {
            "exists": True,
            "num_participants": len(participants.participants),
            "identities": identities,
            # dispatcher + callee both present ⇒ the call was answered
            "active": len(participants.participants) >= 2,
        }
    finally:
        await lk.aclose()


async def end_room(room_name: str) -> None:
    """Tear down the room (drops all remaining participants incl. the SIP leg)."""
    lk = _lkapi()
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        logger.info("[Telephony] deleted room %s", room_name)
    finally:
        await lk.aclose()


def ensure_ready_or_501() -> None:
    """Raise the canonical 501-with-reason when LiveKit isn't usable."""
    ready, reason = livekit_ready()
    if not ready:
        raise HTTPException(status_code=501, detail=reason)
