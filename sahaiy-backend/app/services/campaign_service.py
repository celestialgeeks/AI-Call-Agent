"""
app/services/campaign_service.py
────────────────────────────────
Outreach campaign domain logic (issue #7).

Owns:
  * E.164 phone normalization
  * CSV parsing (5 MB cap, column allow-list, per-row errors, SEC-06
    formula-injection neutralization)
  * Campaign validation for start (agent published, >=1 contact)
  * Postgres FOR UPDATE SKIP LOCKED queue primitives via RPC (ruling B2)
  * Outcome writes + conversation linking

All Supabase access is service-role server-side; user scoping is enforced by
filtering on the JWT-derived user_id at every call site.
"""

import csv
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

# ── Locked vocabularies (rulings B3/B4) ──────────────────────────────────────
CAMPAIGN_STATUSES = ("draft", "running", "paused", "completed")
CONTACT_CALL_STATUSES = ("queued", "dialing", "completed", "failed", "skipped", "dnd")
OUTCOMES = (
    "connected",
    "no_answer",
    "busy",
    "voicemail",
    "callback_requested",
    "not_interested",
    "dnd",
    "failed",
)

# Outcomes that count as a finished call vs retryable failures.
RETRYABLE_OUTCOMES = {"no_answer", "busy"}

# ── CSV safety (@code-reviewer SEC-06 + contract Part 2) ────────────────────
CSV_COLUMN_ALIASES = {
    "name": {"name", "full name", "fullname", "contact", "contact name"},
    "phone": {"phone", "phone number", "phonenumber", "mobile", "number", "contact number"},
}
CSV_MAX_BYTES = 5 * 1024 * 1024  # 5 MB cap

# SEC-06: a cell starting with = + - @ can execute as a spreadsheet formula.
_FORMULA_CHARS = ("=", "+", "-", "@")
# Characters that enable HTML/script injection when the value is later rendered.
_HTML_ESCAPE_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#x27;",
}


def sanitize_csv_cell(value: str) -> str:
    """
    Neutralize CSV formula / HTML injection (SEC-06).

    A cell that begins with = + - @ gets a leading apostrophe (standard
    spreadsheet-defense convention) and HTML-significant characters are escaped,
    so nothing executable or renderable-as-markup passes through to clients.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return text
    # Escape HTML first, then guard formula chars on the escaped result.
    for ch, repl in _HTML_ESCAPE_MAP.items():
        text = text.replace(ch, repl)
    if text.startswith(_FORMULA_CHARS):
        text = "'" + text
    return text


# ── Phone normalization (E.164) ──────────────────────────────────────────────
_PHONE_ALLOWED = re.compile(r"[0-9+]")
_DEFAULT_COUNTRY_CODE = "91"  # product default; numbers without '+' are IN numbers


def normalize_phone(raw: str) -> Optional[str]:
    """
    Normalize to E.164 (+<digits>) or return None if unusable.

    Accepts: '+919876543210', '098765 43210', '+91 98765-43210'.
    Rules:
      * digits, spaces, dashes, parens and one leading '+' survive;
      * '00' international prefix → '+';
      * missing '+' → assume default country code (IN);
      * total digits must be 8–15 per ITU-T E.164.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    plus = text.startswith("+")
    if text.startswith("00"):
        plus = True
        text = text[2:]
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits or len(digits) < 8 or len(digits) > 15:
        return None
    if not plus:
        digits = _DEFAULT_COUNTRY_CODE + digits.lstrip("0")
    return "+" + digits


class CsvParseError(Exception):
    """Raised when the upload is unusable as a whole (wrong shape, too big…)."""


def parse_contacts_csv(content: bytes) -> dict:
    """
    Parse uploaded CSV/text contact data.

    Returns:
        {
          "rows":   [{"name": ..., "phone": ..., "attributes": {...}}, ...]  valid only
          "errors": [{"row": n, "phone": raw, "error": msg}, ...]
          "total":  data-row count attempted
        }
    Raises:
        CsvParseError on file-level problems (empty, too big, no phone column).
    """
    if not content or not content.strip():
        raise CsvParseError("Uploaded file is empty")
    if len(content) > CSV_MAX_BYTES:
        raise CsvParseError(
            f"File exceeds {CSV_MAX_BYTES // (1024 * 1024)} MB limit ({len(content)} bytes)"
        )

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvParseError("File must be UTF-8 encoded") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CsvParseError("CSV has no header row") from exc

    header_norm = [h.strip().lower() for h in header]
    phone_idx = name_idx = None
    extra_cols: list[tuple[int, str]] = []
    for i, col in enumerate(header_norm):
        if phone_idx is None and col in CSV_COLUMN_ALIASES["phone"]:
            phone_idx = i
        elif name_idx is None and col in CSV_COLUMN_ALIASES["name"]:
            name_idx = i
        else:
            extra_cols.append((i, col))
    if phone_idx is None:
        raise CsvParseError(
            "No phone column found — expected a header like 'phone' "
            f"(got: {', '.join(header) or 'none'})"
        )

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_in_file: set[str] = set()
    total = 0

    for line_no, record in enumerate(reader, start=2):  # row 1 = header
        if not record or all(not c.strip() for c in record):
            continue  # skip blank lines entirely
        total += 1
        raw_phone = record[phone_idx].strip() if phone_idx < len(record) else ""
        raw_name = record[name_idx].strip() if name_idx is not None and name_idx < len(record) else ""

        phone = normalize_phone(raw_phone)
        if not phone:
            errors.append({
                "row": line_no,
                "phone": sanitize_csv_cell(raw_phone),
                "error": "invalid phone number (expected E.164-normalizable, 8–15 digits)",
            })
            continue
        if phone in seen_in_file:
            errors.append({
                "row": line_no,
                "phone": phone,
                "error": "duplicate within uploaded file",
            })
            continue
        seen_in_file.add(phone)

        attributes = {}
        for i, col in extra_cols:
            if i < len(record) and record[i].strip():
                attributes[col] = sanitize_csv_cell(record[i])

        rows.append({
            # SEC-06: name/attributes are sanitized before storage/echo.
            "name": sanitize_csv_cell(raw_name),
            "phone": phone,
            "attributes": attributes,
        })

    return {"rows": rows, "errors": errors, "total": total}


# ── Supabase helpers ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_campaign(user_id: str, campaign_id: str) -> Optional[dict]:
    """Fetch a campaign scoped to the token-derived user."""
    try:
        res = (
            get_supabase()
            .table("campaigns")
            .select("*")
            .eq("id", campaign_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return res.data
    except Exception as exc:
        logger.info("[Campaigns] get_campaign(%s) for %s: %s", campaign_id, user_id, exc)
        return None


async def validate_for_start(campaign: dict) -> tuple[bool, str]:
    """
    Start-time validation per issue #7 flow: agent published, >=1 contact,
    schedule window sanity. Returns (ok, reason).
    """
    agent_id = campaign.get("agent_id")
    if not agent_id:
        return False, "campaign has no agent assigned"

    try:
        supabase = get_supabase()
        agent = (
            supabase.table("agents")
            .select("status,name")
            .eq("id", agent_id)
            .single()
            .execute()
        ).data
        if not agent:
            return False, "assigned agent not found"
        if agent.get("status") != "published":
            return False, f"agent must be published to start (current: {agent.get('status')})"

        count_res = (
            supabase.table("campaign_contacts")
            .select("id", count="exact")
            .eq("campaign_id", campaign["id"])
            .execute()
        )
        contact_count = count_res.count if count_res.count is not None else len(count_res.data or [])
        if contact_count < 1:
            return False, "campaign has no contacts — upload at least one before starting"

        end_at = campaign.get("schedule_end_at")
        if end_at:
            end_dt = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt <= datetime.now(timezone.utc):
                return False, "campaign schedule window has already ended"
    except Exception as exc:
        logger.error("[Campaigns] validate_for_start error: %s", exc)
        return False, "validation failed unexpectedly"

    return True, ""


async def enqueue_campaign(campaign_id: str) -> int:
    """(Re)queue eligible contact-calls via RPC; returns affected row count."""
    res = get_supabase().rpc("enqueue_campaign", {"p_campaign_id": campaign_id}).execute()
    if isinstance(res.data, int):
        return res.data
    if isinstance(res.data, list) and res.data:
        return int(res.data[0]) if not isinstance(res.data[0], dict) else int(res.data[0].get("enqueue_campaign", 0))
    return 0


async def dequeue_next(campaign_id: str, worker_id: str) -> Optional[dict]:
    """
    Atomically claim the next contact-call (FOR UPDATE SKIP LOCKED, ruling B2).
    Marks it dialing and bumps attempts inside the dequeue RPC.
    """
    res = (
        get_supabase()
        .rpc("dequeue_campaign_contact", {"p_campaign_id": campaign_id, "p_worker_id": worker_id})
        .execute()
    )
    data = res.data
    if not data:
        return None
    row = data[0] if isinstance(data, list) else data
    return {
        "campaign_contact_id": row.get("cc_id"),
        "contact_id": row.get("contact_id"),
        "phone": row.get("phone"),
        "name": row.get("contact_name"),
        "attempts": row.get("attempts"),
    }


async def complete_campaign_if_drained(campaign_id: str) -> bool:
    """Flip campaign to completed once no queued/dialing rows remain."""
    try:
        res = (
            get_supabase()
            .rpc("complete_campaign_if_drained", {"p_campaign_id": campaign_id})
            .execute()
        )
        data = res.data
        if isinstance(data, bool):
            return data
        if isinstance(data, list) and data:
            return bool(data[0])
    except Exception as exc:
        logger.error("[Campaigns] drained-check failed for %s: %s", campaign_id, exc)
    return False


async def write_call_result(
    campaign_contact_id: str,
    outcome: str,
    notes: str = "",
    conversation_id: Optional[str] = None,
) -> None:
    """
    Persist a call outcome, link the conversation row, and requeue/retry per policy.

    Outcome must be in the locked vocab (B3). Retryable outcomes go back to
    queued while attempts remain; terminal ones complete the contact-call.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome '{outcome}' outside locked vocabulary")

    supabase = get_supabase()
    cc = (
        supabase.table("campaign_contacts")
        .select("attempts,campaign_id,status")
        .eq("id", campaign_contact_id)
        .single()
        .execute()
    ).data
    if not cc:
        logger.error("[Campaigns] write_call_result: cc %s vanished", campaign_contact_id)
        return

    update: dict[str, Any] = {
        "outcome": outcome,
        "outcome_notes": notes or None,
        "status": "completed",
        "last_attempted_at": _now_iso(),
    }

    # Retry policy: retryable failure outcomes may go back to queued.
    if outcome in RETRYABLE_OUTCOMES:
        campaign = (
            supabase.table("campaigns")
            .select("retry_max_attempts")
            .eq("id", cc["campaign_id"])
            .single()
            .execute()
        ).data
        max_attempts = (campaign or {}).get("retry_max_attempts", 3)
        if cc.get("attempts", 0) < max_attempts:
            update["status"] = "queued"
        else:
            update["status"] = "failed"

    if outcome == "dnd":
        update["status"] = "dnd"
        # Honor DND on the contact itself so future campaigns skip them.
        try:
            cc_row = (
                supabase.table("campaign_contacts")
                .select("contact_id")
                .eq("id", campaign_contact_id)
                .single()
                .execute()
            ).data
            if cc_row and cc_row.get("contact_id"):
                supabase.table("contacts").update({"dnd": True}).eq(
                    "id", cc_row["contact_id"]
                ).execute()
        except Exception as exc:
            logger.warning("[Campaigns] dnd propagation failed: %s", exc)

    supabase.table("campaign_contacts").update(update).eq("id", campaign_contact_id).execute()

    if conversation_id:
        supabase.table("conversations").update(
            {"campaign_contact_id": campaign_contact_id}
        ).eq("id", conversation_id).execute()


async def campaign_counters(campaign_id: str) -> dict:
    """Live counters for GET /campaigns/{id}."""
    supabase = get_supabase()
    res = (
        supabase.table("campaign_contacts")
        .select("status,outcome")
        .eq("campaign_id", campaign_id)
        .execute()
    )
    rows = res.data or []
    by_status = {s: 0 for s in CONTACT_CALL_STATUSES}
    by_outcome = {o: 0 for o in OUTCOMES}
    for r in rows:
        s = r.get("status")
        o = r.get("outcome")
        if s in by_status:
            by_status[s] += 1
        if o in by_outcome:
            by_outcome[o] += 1
    completed = by_status["completed"] + by_status["failed"] + by_status["skipped"] + by_status["dnd"]
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_outcome": by_outcome,
        "pending": by_status["queued"] + by_status["dialing"],
        "finished": completed,
        "connected_pct": round(100.0 * by_outcome["connected"] / completed, 1) if completed else 0.0,
    }
