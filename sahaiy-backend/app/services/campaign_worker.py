"""
app/services/campaign_worker.py
───────────────────────────────
In-process campaign dialer (ruling B2: NO new service).

A single asyncio task polls running campaigns and dequeues contact-calls via
Postgres `FOR UPDATE SKIP LOCKED` RPCs so multiple workers/instances never grab
the same row.

v1 call execution (per ruling B4): SIMULATED browser dialing only. Each call is
a short scripted conversation driven through the SAME pipeline as WS
`text_input` turns (LLM fragments), with no PSTN/SIP involvement. SIP trunk is
v2 and gated on the demo gate passing 3/3.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timezone

from app.config import (
    CAMPAIGN_MAX_CONCURRENT_CALLS,
    CAMPAIGN_SIM_CONVERSATION_TURNS,
    CAMPAIGN_WORKER_POLL_SEC,
)
from app.services import agent_service, campaign_service
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


# ── Calling-hours guard ──────────────────────────────────────────────────────


def _within_calling_hours(campaign: dict) -> bool:
    """Check local calling-hours window (JSONB {start:'09:00', end:'18:00'})."""
    hours = campaign.get("calling_hours") or {}
    try:
        start = str(hours.get("start") or "00:00")
        end = str(hours.get("end") or "23:59")
        now = datetime.now(timezone.utc).time()  # server clock; timezone-aware refinement is v2
        h, m = map(int, start.split(":"))
        start_t = datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0).time()
        h, m = map(int, end.split(":"))
        end_t = datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0).time()
        return start_t <= now <= end_t
    except Exception:
        return True  # malformed hours → fail open; per-call validation still applies


# ── Simulated call (v1) ──────────────────────────────────────────────────────


async def _simulate_call(campaign: dict, job: dict) -> tuple[str, str]:
    """
    Run one simulated browser call through the LLM pipeline (WS text_input path).

    Returns (outcome, notes). The scripted "contact" answers a couple of turns;
    if the LLM pipeline produces any response we score the call `connected`,
    otherwise it degrades to a retryable `no_answer`.
    """
    from app.services.llm import build_prompt, stream_llm
    import httpx

    agent_id = campaign.get("agent_id")
    agent = await agent_service.get_agent(agent_id) if agent_id else None
    if not agent:
        return "failed", "agent not found at call time"

    contact_name = job.get("name") or "there"
    opener = f"Hi {contact_name}, this is an automated outreach call from {campaign.get('name')}."
    replies = [
        "Yes, tell me more.",
        "Okay, that works for me.",
    ]

    outcome_notes_parts: list[str] = []
    answered = False
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            user_turns = [opener] + replies[: max(0, int(CAMPAIGN_SIM_CONVERSATION_TURNS))]
            for turn in user_turns:
                context = await _rag_context(agent, turn)
                prompt = build_prompt(agent, turn, context)
                got_reply = False
                async for fragment in stream_llm(prompt, agent, client=client):
                    got_reply = True
                    outcome_notes_parts.append(fragment)
                if got_reply:
                    answered = True
    except Exception as exc:
        logger.warning("[CampaignWorker] sim call %s error: %s", job.get("campaign_contact_id"), exc)
        return "no_answer", f"pipeline error: {exc}"

    if answered:
        transcript_excerpt = " ".join(outcome_notes_parts)[:400]
        return "connected", transcript_excerpt
    return "no_answer", "no agent reply in simulated session"


async def _rag_context(agent: dict, text: str) -> str:
    """Best-effort RAG context; failures must not kill the call."""
    from app.services import rag

    try:
        return await rag.retrieve_context(agent.get("user_id", ""), text)
    except Exception:
        return ""


async def _run_campaign_once(campaign: dict, worker_id: str, sem: asyncio.Semaphore) -> None:
    """Drain as much of one campaign's queue as concurrency allows, then yield."""
    campaign_id = campaign["id"]
    pending: list[asyncio.Task] = []

    async def do_call(job: dict):
        async with sem:
            outcome, notes = await _simulate_call(campaign, job)
            try:
                await campaign_service.write_call_result(
                    job["campaign_contact_id"], outcome, notes
                )
                logger.info(
                    "[CampaignWorker] cc=%s outcome=%s attempts=%s",
                    job["campaign_contact_id"], outcome, job.get("attempts"),
                )
            except Exception as exc:
                logger.error("[CampaignWorker] write result failed cc=%s: %s",
                             job.get("campaign_contact_id"), exc)

    while True:
        # Re-read campaign state each iteration so stop/pause takes effect fast.
        fresh = await campaign_service.get_campaign(campaign["user_id"], campaign_id)
        if not fresh or fresh.get("status") != "running":
            break
        if not _within_calling_hours(fresh):
            break

        job = await campaign_service.dequeue_next(campaign_id, worker_id)
        if not job:
            break  # queue empty for now
        pending.append(asyncio.create_task(do_call(job)))
        if len(pending) >= CAMPAIGN_MAX_CONCURRENT_CALLS * 4:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.exception() and logger.error("[CampaignWorker] task error: %s", t.exception())

    if pending:
        results = await asyncio.gather(*pending, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error("[CampaignWorker] call task failed: %s", r)

    await campaign_service.complete_campaign_if_drained(campaign_id)


async def _worker_loop() -> None:
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    sem = asyncio.Semaphore(CAMPAIGN_MAX_CONCURRENT_CALLS)
    logger.info("[CampaignWorker] started (%s)", worker_id)
    while True:
        try:
            res = get_supabase().table("campaigns").select("*").eq("status", "running").execute()
            campaigns = res.data or []
            for campaign in campaigns:
                await _run_campaign_once(campaign, worker_id, sem)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[CampaignWorker] loop error: %s", exc)
        await asyncio.sleep(CAMPAIGN_WORKER_POLL_SEC)


# ── Lifecycle hooks (wired into app lifespan) ────────────────────────────────


def start_worker() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
        _task = None
