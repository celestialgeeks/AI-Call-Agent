"""
app/routers/phone_numbers.py
─────────────────────────────
Phone number management endpoints for the dashboard UI.

GET    /phone-numbers              → list user phone numbers
DELETE /phone-numbers/{id}         → delete one number (scoped to user_id)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/phone-numbers", tags=["Phone Numbers"])


class PhoneNumberResponse(BaseModel):
    id: str
    user_id: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    number: str
    country: Optional[str] = None
    city: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)
    status: str = "active"
    call_count: int = 0
    created_at: Optional[str] = None


class DeletePhoneNumberResponse(BaseModel):
    ok: bool = True
    id: str


@router.get("", response_model=list[PhoneNumberResponse])
async def list_phone_numbers(user_id: str = Query(..., min_length=1)):
    """Return all phone numbers for a given user, newest first."""
    try:
        supabase = get_supabase()
        result = (
            supabase.table("phone_numbers")
            .select("id,user_id,agent_id,number,country,city,capabilities,status,call_count,created_at,agents(name)")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )

        rows = result.data or []
        return [
            PhoneNumberResponse(
                id=row.get("id"),
                user_id=row.get("user_id"),
                agent_id=row.get("agent_id"),
                agent_name=(row.get("agents") or {}).get("name"),
                number=row.get("number"),
                country=row.get("country"),
                city=row.get("city"),
                capabilities=row.get("capabilities") or [],
                status=row.get("status") or "active",
                call_count=row.get("call_count") or 0,
                created_at=row.get("created_at"),
            )
            for row in rows
        ]
    except Exception as exc:
        logger.error("[PhoneNumbers] list error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{phone_number_id}", response_model=DeletePhoneNumberResponse)
async def delete_phone_number(phone_number_id: str, user_id: str = Query(..., min_length=1)):
    """Delete a phone number only if it belongs to the provided user_id."""
    try:
        supabase = get_supabase()
        existing = (
            supabase.table("phone_numbers")
            .select("id")
            .eq("id", phone_number_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Phone number not found")

        (
            supabase.table("phone_numbers")
            .delete()
            .eq("id", phone_number_id)
            .eq("user_id", user_id)
            .execute()
        )

        logger.info("[PhoneNumbers] Deleted %s for user %s", phone_number_id, user_id)
        return DeletePhoneNumberResponse(id=phone_number_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[PhoneNumbers] delete error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
