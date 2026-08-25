"""
app/routers/campaigns.py
────────────────────────
Outreach campaign endpoints (issue #7). All JWT-auth'd — user_id is derived
from the token, never from body/query.

POST   /api/v1/campaigns                 create
GET    /api/v1/campaigns                 list (status filter + cursor pagination)
GET    /api/v1/campaigns/{id}            detail with live counters
PATCH  /api/v1/campaigns/{id}            update / pause / resume
POST   /api/v1/campaigns/{id}/contacts   CSV/text upload → parsed, deduped, per-row errors
GET    /api/v1/campaigns/{id}/contacts   paginated contact list w/ status
DELETE /api/v1/campaigns/{id}/contacts/{contact_id}   remove a contact from campaign
POST   /api/v1/campaigns/{id}/start      validate → set running (worker enqueues)
POST   /api/v1/campaigns/{id}/stop       pause queue drain
POST   /api/v1/campaigns/{id}/simulate   v1 demo mode — simulated browser calls
"""

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator

from app.auth import get_current_user_id
from app.config import CAMPAIGN_CSV_MAX_BYTES
from app.services import campaign_service, campaign_worker
from app.services.campaign_service import (
    CONTACT_CALL_STATUSES,
    CsvParseError,
    normalize_phone,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/campaigns", tags=["Campaigns"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class ScheduleIn(BaseModel):
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    calling_hours: Optional[dict[str, str]] = None
    timezone: Optional[str] = None


class RetryPolicyIn(BaseModel):
    max_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    retry_after_min: Optional[int] = Field(default=None, ge=0)


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    agent_id: str
    objective: Optional[str] = None
    schedule: Optional[ScheduleIn] = None
    retry_policy: Optional[RetryPolicyIn] = None


class CampaignPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    objective: Optional[str] = None
    status: Optional[str] = None  # pause → 'paused', resume → 'running'
    schedule: Optional[ScheduleIn] = None
    retry_policy: Optional[RetryPolicyIn] = None

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v):
        if v is not None and v not in campaign_service.CAMPAIGN_STATUSES:
            raise ValueError(f"status must be one of {campaign_service.CAMPAIGN_STATUSES}")
        return v


class CampaignOut(BaseModel):
    id: str
    user_id: str
    agent_id: Optional[str] = None
    name: str
    objective: Optional[str] = None
    status: str
    schedule: dict[str, Any] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _campaign_out(row: dict) -> CampaignOut:
    return CampaignOut(
        id=row["id"],
        user_id=row.get("user_id", ""),
        agent_id=row.get("agent_id"),
        name=row.get("name", ""),
        objective=row.get("objective"),
        status=row.get("status", "draft"),
        schedule={
            "start_at": row.get("schedule_start_at"),
            "end_at": row.get("schedule_end_at"),
            "calling_hours": row.get("calling_hours") or {},
            "timezone": row.get("timezone"),
        },
        retry_policy={
            "max_attempts": row.get("retry_max_attempts", 3),
            "retry_after_min": row.get("retry_after_min", 60),
        },
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CampaignCreate, user_id: str = Depends(get_current_user_id)):
    """Create a campaign. Agent must belong to the caller."""
    supabase = get_supabase_dep()
    try:
        agent = supabase.table("agents").select("id").eq("id", body.agent_id).eq(
            "user_id", user_id
        ).single().execute().data
    except Exception:
        agent = None
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found for this user")

    payload: dict[str, Any] = {
        "user_id": user_id,
        "agent_id": body.agent_id,
        "name": body.name,
        "objective": body.objective,
        "status": "draft",
    }
    if body.schedule:
        payload.update({
            "schedule_start_at": body.schedule.start_at,
            "schedule_end_at": body.schedule.end_at,
            "calling_hours": body.schedule.calling_hours,
            "timezone": body.schedule.timezone or "Asia/Kolkata",
        })
    if body.retry_policy:
        if body.retry_policy.max_attempts is not None:
            payload["retry_max_attempts"] = body.retry_policy.max_attempts
        if body.retry_policy.retry_after_min is not None:
            payload["retry_after_min"] = body.retry_policy.retry_after_min

    try:
        row = supabase.table("campaigns").insert(payload).select().single().execute().data
    except Exception as exc:
        logger.error("[Campaigns] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create campaign") from exc
    return _campaign_out(row)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    cursor: Optional[str] = Query(default=None, description="created_at of last item, exclusive"),
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
):
    """List caller's campaigns, newest first; optional status filter; keyset pagination."""
    if status_filter and status_filter not in campaign_service.CAMPAIGN_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {campaign_service.CAMPAIGN_STATUSES}",
        )
    q = get_supabase_dep().table("campaigns").select("*").eq("user_id", user_id)
    if status_filter:
        q = q.eq("status", status_filter)
    if cursor:
        q = q.lt("created_at", cursor)
    try:
        rows = q.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception as exc:
        logger.error("[Campaigns] list failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list campaigns") from exc
    return [_campaign_out(r) for r in rows]


async def _get_owned(user_id: str, campaign_id: str) -> dict:
    campaign = await campaign_service.get_campaign(user_id, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign_detail(campaign_id: str, user_id: str = Depends(get_current_user_id)):
    """Campaign detail with live counters."""
    campaign = await _get_owned(user_id, campaign_id)
    out = _campaign_out(campaign)
    try:
        counters = await campaign_service.campaign_counters(campaign_id)
    except Exception:
        counters = {}
    data = out.model_dump()
    data["counters"] = counters
    return data


@router.patch("/{campaign_id}", response_model=CampaignOut)
async def patch_campaign(
    campaign_id: str,
    body: CampaignPatch,
    user_id: str = Depends(get_current_user_id),
):
    """Update editable fields; status transitions handle pause/resume."""
    campaign = await _get_owned(user_id, campaign_id)

    update: dict[str, Any] = {}
    if body.name is not None:
        update["name"] = body.name
    if body.objective is not None:
        update["objective"] = body.objective
    if body.schedule is not None:
        if body.schedule.start_at is not None:
            update["schedule_start_at"] = body.schedule.start_at
        if body.schedule.end_at is not None:
            update["schedule_end_at"] = body.schedule.end_at
        if body.schedule.calling_hours is not None:
            update["calling_hours"] = body.schedule.calling_hours
        if body.schedule.timezone is not None:
            update["timezone"] = body.schedule.timezone

    # Status transition rules.
    if body.status is not None:
        requested = body.status
        current = campaign["status"]
        allowed_transitions = {
            "draft": {"running"},
            "running": {"paused", "completed"},
            "paused": {"running", "completed"},     # resume / complete
            "completed": set(),
        }
        if requested == current:
            pass
        elif requested in allowed_transitions.get(current, set()):
            if requested == "completed":
                remaining = await campaign_service.campaign_counters(campaign_id)
                if remaining.get("pending", 0) > 0:
                    raise HTTPException(
                        status_code=409,
                        detail="Cannot mark completed while contacts are still queued/dialing",
                    )
            update["status"] = requested
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Illegal status transition {current} → {requested}",
            )

    if not update:
        return _campaign_out(campaign)

    try:
        row = (
            get_supabase_dep()
            .table("campaigns")
            .update(update)
            .eq("id", campaign_id)
            .eq("user_id", user_id)
            .select()
            .single()
            .execute()
            .data
        )
    except Exception as exc:
        logger.error("[Campaigns] patch failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update campaign") from exc
    return _campaign_out(row)


# ── Contacts ingest / list ───────────────────────────────────────────────────


def _parse_upload(content: bytes) -> dict:
    try:
        return campaign_service.parse_contacts_csv(content)
    except CsvParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ContactsUploadResult(BaseModel):
    added: int
    duplicates_merged: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    total_rows_parsed: int


@router.post("/{campaign_id}/contacts", response_model=ContactsUploadResult)
async def upload_contacts(
    campaign_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
):
    """
    CSV/text upload. Server-side parse with column allow-list mapping, E.164
    normalization, per-row validation errors surfaced (never swallowed), SEC-06
    sanitization on every free-text cell, 5 MB cap. Valid rows upsert into
    contacts (UNIQUE(user_id, phone)) and link into the campaign.
    """
    await _get_owned(user_id, campaign_id)

    content = await file.read()
    parsed = _parse_upload(content[: CAMPAIGN_CSV_MAX_BYTES + 1])
    supabase = get_supabase_dep()

    added = 0
    merged = 0
    contact_ids: list[str] = []

    for row in parsed["rows"]:
        phone = row["phone"]
        # Upsert on UNIQUE(user_id, phone): existing contact reused, never duplicated.
        try:
            existing = (
                supabase.table("contacts")
                .select("id")
                .eq("user_id", user_id)
                .eq("phone", phone)
                .limit(1)
                .execute()
                .data
            )
            if existing:
                contact_id = existing[0]["id"]
                merged += 1
            else:
                ins = (
                    supabase.table("contacts")
                    .insert({
                        "user_id": user_id,
                        "phone": phone,
                        "name": row["name"],
                        "attributes": row["attributes"],
                    })
                    .select()
                    .single()
                    .execute()
                    .data
                )
                contact_id = ins["id"]
                added += 1
            contact_ids.append((contact_id, phone))
        except Exception as exc:
            logger.error("[Campaigns] contact upsert failed (%s): %s", phone, exc)
            parsed["errors"].append({
                "row": None,
                "phone": phone,
                "error": "failed to persist contact",
            })

    linked = 0
    already = 0
    dnd_skipped = 0
    for contact_id, phone in contact_ids:
        try:
            link = (
                supabase.table("campaign_contacts")
                .insert({"campaign_id": campaign_id, "contact_id": contact_id})
                .select()
                .execute()
            )
            linked += 1
        except Exception:
            # UNIQUE(campaign_id, contact_id) hit → already in this campaign.
            already += 1

    return ContactsUploadResult(
        added=added,
        duplicates_merged=merged,
        errors=parsed["errors"],
        total_rows_parsed=parsed["total"],
    )


@router.get("/{campaign_id}/contacts")
async def list_campaign_contacts(
    campaign_id: str,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
):
    """Paginated campaign contact list with call status + outcome."""
    await _get_owned(user_id, campaign_id)
    if status_filter and status_filter not in CONTACT_CALL_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"status must be one of {CONTACT_CALL_STATUSES}"
        )
    sel = (
        "id,status,attempts,last_attempted_at,outcome,outcome_notes,"
        "contacts(id,phone,name,dnd)"
    )
    q = (
        get_supabase_dep()
        .table("campaign_contacts")
        .select(sel)
        .eq("campaign_id", campaign_id)
    )
    if status_filter:
        q = q.eq("status", status_filter)
    try:
        res = q.order("created_at", desc=False).range(offset, offset + limit - 1).execute()
    except Exception as exc:
        logger.error("[Campaigns] contacts list failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list campaign contacts") from exc

    items = []
    for r in res.data or []:
        c = r.pop("contacts", None) or {}
        items.append({
            **r,
            "phone": c.get("phone"),
            "name": c.get("name"),
            "dnd": c.get("dnd", False),
        })
    return {"items": items, "limit": limit, "offset": offset}


@router.delete("/{campaign_id}/contacts/{contact_id}")
async def remove_campaign_contact(
    campaign_id: str,
    contact_id: str,
    user_id: str = Depends(get_current_user_id),
):
    await _get_owned(user_id, campaign_id)
    supabase = get_supabase_dep()
    supabase.table("campaign_contacts").delete().eq("campaign_id", campaign_id).eq(
        "contact_id", contact_id
    ).execute()
    return {"ok": True, "removed": contact_id}


# ── Lifecycle: start / stop / simulate ───────────────────────────────────────


def _require_transitionable(campaign: dict, allowed_from: tuple[str, ...], action: str) -> None:
    if campaign["status"] not in allowed_from:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {action} campaign in status '{campaign['status']}' "
                   f"(allowed from: {', '.join(allowed_from)})",
        )


@router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: str, user_id: str = Depends(get_current_user_id)):
    """
    Validate (agent published, >=1 contact, schedule window) → enqueue queued
    contacts → mark running. The in-process worker dequeues via SKIP LOCKED.
    """
    campaign = await _get_owned(user_id, campaign_id)
    _require_transitionable(campaign, ("draft", "paused"), "start")

    ok, reason = await campaign_service.validate_for_start(campaign)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)

    enqueued = await campaign_service.enqueue_campaign(campaign_id)
    get_supabase_dep().table("campaigns").update({"status": "running"}).eq(
        "id", campaign_id
    ).execute()
    campaign_worker.start_worker()
    logger.info("[Campaigns] started %s (enqueued=%s)", campaign_id, enqueued)
    return {"ok": True, "id": campaign_id, "status": "running", "queued": enqueued}


@router.post("/{campaign_id}/stop")
async def stop_campaign(campaign_id: str, user_id: str = Depends(get_current_user_id)):
    """Stop draining the queue (pause). Resume via PATCH status='running' or POST start."""
    campaign = await _get_owned(user_id, campaign_id)
    _require_transitionable(campaign, ("running",), "stop")
    get_supabase_dep().table("campaigns").update({"status": "paused"}).eq(
        "id", campaign_id
    ).execute()
    logger.info("[Campaigns] stopped %s", campaign_id)
    return {"ok": True, "id": campaign_id, "status": "paused"}


@router.post("/{campaign_id}/simulate")
async def simulate_campaign(campaign_id: str, user_id: str = Depends(get_current_user_id)):
    """
    v1 demo mode (ruling B4): run the campaign through SIMULATED browser calls
    over the same LLM pipeline as WS text_input — no PSTN/SIP. Drains the queue
    synchronously up to concurrency limits so demo results appear immediately;
    returns live counters when done.
    """
    campaign = await _get_owned(user_id, campaign_id)
    _require_transitionable(campaign, ("draft", "paused"), "simulate")

    ok, reason = await campaign_service.validate_for_start(campaign)
    if not ok:
        raise HTTPException(status_code=422, detail=reason)

    enqueued = await campaign_service.enqueue_campaign(campaign_id)
    get_supabase_dep().table("campaigns").update({"status": "running"}).eq(
        "id", campaign_id
    ).execute()

    sem = asyncio.Semaphore(3)
    worker_id = f"simulate-{user_id[:8]}"
    processed = 0
    outcomes: list[dict] = []

    while True:
        fresh = await campaign_service.get_campaign(user_id, campaign_id)
        if not fresh or fresh.get("status") != "running":
            break
        job = await campaign_service.dequeue_next(campaign_id, worker_id)
        if not job:
            break
        async with sem:
            outcome, notes = await campaign_worker._simulate_call(fresh, job)
        await campaign_service.write_call_result(job["campaign_contact_id"], outcome, notes)
        outcomes.append({"campaign_contact_id": job["campaign_contact_id"], "outcome": outcome})
        processed += 1

    await campaign_service.complete_campaign_if_drained(campaign_id)
    counters = await campaign_service.campaign_counters(campaign_id)
    final = await campaign_service.get_campaign(user_id, campaign_id)
    logger.info("[Campaigns] simulate %s done: %s calls", campaign_id, processed)
    return {
        "ok": True,
        "id": campaign_id,
        "simulated_calls": processed,
        "outcomes": outcomes,
        "counters": counters,
        "status": (final or {}).get("status"),
    }


# Local imports kept at bottom to avoid cycles (routers ↔ services).
import asyncio  # noqa: E402


def get_supabase_dep():
    from app.services.supabase_client import get_supabase

    return get_supabase()
