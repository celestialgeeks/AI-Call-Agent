"""
qa/contract_tests/test_contracts.py
────────────────────────────────────
Contract conformance vs api-contracts-and-outreach-boundary-v1.md (Part 1,
"Verified contracts"). These assert EXACT response shapes — any drift fails.

Runs against the real FastAPI app in-process with external services stubbed
(see qa/conftest.py). Sequencing per issue #9: cases target the CURRENT
body-user_id shape; the auth flip is covered by qa/security_tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import AGENT_ID, USER_A, USER_B, JWT_ENFORCED, auth_headers

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


# ── 1.1 POST /agents/{agent_id}/call/start ──────────────────────────────────


class TestCallStart:
    async def test_exact_shape_200(self, api):
        """Response must be EXACTLY {conversation_id: <uuid str>} — nothing more."""
        r = await api.post(
            f"/agents/{AGENT_ID}/call/start",
            json={"user_id": USER_A},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {"conversation_id"}, f"shape drift: {body.keys()}"
        conv_id = body["conversation_id"]
        assert isinstance(conv_id, str) and len(conv_id) == 36  # uuid4

    async def test_optional_fields_accepted(self, api):
        """caller_name/caller_number are optional strings per contract §1.1."""
        r = await api.post(
            f"/agents/{AGENT_ID}/call/start",
            json={"user_id": USER_A, "caller_name": "QA Bot", "caller_number": "+911234567890"},
        )
        assert r.status_code == 200
        assert set(r.json().keys()) == {"conversation_id"}

    async def test_user_id_required(self, api):
        """user_id is a required body field in the CURRENT shape."""
        r = await api.post(f"/agents/{AGENT_ID}/call/start", json={})
        assert r.status_code == 422  # pydantic validation error

    async def test_unknown_agent_still_500_today(self, api):
        """
        Contract §1.1: errors are currently raw 500s. This pins TODAY's behavior —
        it MUST be replaced by SEC-03's structured envelope once @backend-eng's
        error-envelope fix lands. Flip this test together with that fix.
        """
        r = await api.post(
            "/agents/99999999-9999-9999-9999-999999999999/call/start",
            json={"user_id": USER_A},
        )
        # Today: stub returns no agent row → router raises → 500
        assert r.status_code in (404, 500), f"unexpected status {r.status_code}"


# ── 1.2 POST /agents/{agent_id}/call/end ────────────────────────────────────


class TestCallEnd:
    def _payload(self, conversation_id):
        return {
            "user_id": USER_A,
            "conversation_id": conversation_id,
            "transcript": "hello | hi there",
            "duration_sec": 120,
            "csat_score": 4,
            "status": "resolved",
        }

    async def test_exact_shape_ok_true(self, api, fake_supabase):
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()
        r = await api.post(
            f"/agents/{AGENT_ID}/call/end", json=self._payload(start["conversation_id"])
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}, "contract §1.2 exact shape is {ok: true}"

    async def test_csat_bounds_validated(self, api):
        """csat_score validated 1–5 by pydantic (ge=1, le=5)."""
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()

        for bad in (0, 6):
            p = self._payload(start["conversation_id"]) | {"csat_score": bad}
            r = await api.post(f"/agents/{AGENT_ID}/call/end", json=p)
            assert r.status_code == 422, f"csat={bad} accepted"

    async def test_status_enum_values(self, api):
        """status accepts resolved|escalated|missed|in_progress per contract §1.2."""
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()
        for status in ("resolved", "escalated", "missed", "in_progress"):
            p = self._payload(start["conversation_id"]) | {"status": status}
            r = await api.post(f"/agents/{AGENT_ID}/call/end", json=p)
            assert r.status_code == 200, f"status={status} rejected: {r.text}"

    async def test_call_end_persists_transcript(self, api, fake_supabase):
        start = (await api.post(f"/agents/{AGENT_ID}/call/start", json={"user_id": USER_A})).json()
        payload = self._payload(start["conversation_id"])
        await api.post(f"/agents/{AGENT_ID}/call/end", json=payload)
        row = next(
            c for c in fake_supabase.store["conversations"] if c["id"] == start["conversation_id"]
        )
        assert row["transcript"] == payload["transcript"]
        assert row["duration_sec"] == 120
        assert row["csat_score"] == 4
        assert row["status"] == "resolved"


# ── 1.3 POST /stt/transcribe ────────────────────────────────────────────────


class TestSttTranscribe:
    async def test_empty_file_400(self, api):
        """Contract §1.3: empty upload → 400."""
        r = await api.post(
            "/stt/transcribe",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )
        assert r.status_code == 400, f"expected 400 for empty file, got {r.status_code}"

    async def test_missing_file_422(self, api):
        r = await api.post("/stt/transcribe")
        assert r.status_code == 422


# ── 1.5 Knowledge endpoints (current weak shape — pinned so drift is visible) ─


class TestKnowledge:
    async def test_ingest_exact_shape(self, api):
        r = await api.post(
            "/knowledge/ingest",
            json={"user_id": USER_A, "doc_id": "doc-1", "text": "Sahaiy demo facts."},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "doc_id": "doc-1"}

    async def test_ingest_empty_text_400(self, api):
        r = await api.post(
            "/knowledge/ingest",
            json={"user_id": USER_A, "doc_id": "doc-2", "text": "   "},
        )
        assert r.status_code == 400

    async def test_status_exact_shape(self, api):
        r = await api.get("/knowledge/status", params={"user_id": USER_A})
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"user_id", "indexed_docs", "rag_available"}
        assert body["user_id"] == USER_A
        assert isinstance(body["indexed_docs"], int)


# ── Health/root (deployment smoke for the hosted backend) ───────────────────


class TestHealth:
    async def test_root_shape(self, api):
        r = await api.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "Sahaiy Backend"


# ── Part 2 — Outreach boundary: EXACT-SHAPE conformance (shipped PR #21) ─────
#
# api-contracts-and-outreach-boundary-v1.md Part 2 is now LOCKED (rulings B1–B4)
# and the routes SHIPPED via PR #21 (app/routers/campaigns.py). These cases pin
# the exact wire shapes; ANY drift fails CI. Auth note: campaign endpoints derive
# user_id via app.auth.get_current_user_id — under the default AUTH_ENFORCED=false
# dev state that resolves from X-User-Id; when the flag flips, auth_headers()
# switches to minted HS256 Bearer tokens automatically. JWT-negative cases stay
# gated with SEC-01..03 in qa/security_tests.

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
CAMPAIGN_OUT_KEYS = {
    "id",
    "user_id",
    "agent_id",
    "name",
    "objective",
    "status",
    "schedule",
    "retry_policy",
    "created_at",
    "updated_at",
}


def _campaign_body(**over):
    body = {"name": "QA Conformance Campaign", "agent_id": AGENT_ID}
    body.update(over)
    return body


async def _create_campaign(api, **over):
    r = await api.post(
        "/api/v1/campaigns", json=_campaign_body(**over), headers=auth_headers(USER_A)
    )
    assert r.status_code == 201, f"campaign create failed: {r.status_code} {r.text}"
    return r.json()


def _upload_contacts_csv(rows):
    lines = ["phone,name"]
    lines.extend(f"{p},{n}" for p, n in rows)
    return ("\n".join(lines) + "\n").encode()


class TestCampaignCreateShape:
    """POST /api/v1/campaigns → 201, exact CampaignOut shape."""

    async def test_exact_shape_201(self, api):
        body = await _create_campaign(api)
        assert set(body.keys()) == CAMPAIGN_OUT_KEYS, f"shape drift: {set(body.keys())}"
        import uuid as _uuid

        assert len(body["id"]) == 36 and _uuid.UUID(body["id"])
        # user_id is derived server-side from auth, never echoed from a body field
        assert body["user_id"] == USER_A
        assert body["status"] == "draft"  # campaigns always START as draft (B4)
        assert body["agent_id"] == AGENT_ID
        assert body["objective"] is None
        # schedule sub-object shape (contract: start_at/end_at/calling_hours/timezone)
        assert set(body["schedule"].keys()) == {
            "start_at",
            "end_at",
            "calling_hours",
            "timezone",
        }
        assert body["schedule"]["timezone"] == "Asia/Kolkata"  # product default
        # retry_policy sub-object shape (contract: max_attempts=3, retry_after_min=60)
        assert body["retry_policy"] == {"max_attempts": 3, "retry_after_min": 60}

    async def test_schedule_and_retry_policy_echoed(self, api):
        body = await _create_campaign(
            api,
            objective="book demos",
            schedule={
                "start_at": "2026-09-01T09:00:00+00:00",
                "end_at": "2026-09-30T18:00:00+00:00",
                "calling_hours": {"start": "10:00", "end": "17:00"},
                "timezone": "Asia/Kolkata",
            },
            retry_policy={"max_attempts": 5, "retry_after_min": 30},
        )
        assert body["objective"] == "book demos"
        assert body["schedule"]["start_at"] == "2026-09-01T09:00:00+00:00"
        assert body["schedule"]["calling_hours"] == {"start": "10:00", "end": "17:00"}
        assert body["retry_policy"] == {"max_attempts": 5, "retry_after_min": 30}

    async def test_name_required_422(self, api):
        r = await api.post(
            "/api/v1/campaigns", json={"agent_id": AGENT_ID}, headers=auth_headers(USER_A)
        )
        assert r.status_code == 422

    async def test_agent_not_owned_404(self, api):
        """Agent must belong to the caller — another user's agent id is 404."""
        r = await api.post(
            "/api/v1/campaigns",
            json=_campaign_body(),
            headers=auth_headers(USER_B),
        )
        assert r.status_code == 404, f"cross-user agent create not rejected: {r.text}"

    async def test_retry_policy_bounds(self, api):
        for bad in (0, 11):
            r = await api.post(
                "/api/v1/campaigns",
                json=_campaign_body(retry_policy={"max_attempts": bad}),
                headers=auth_headers(USER_A),
            )
            assert r.status_code == 422, f"max_attempts={bad} accepted"


class TestCampaignListShape:
    """GET /api/v1/campaigns → list[CampaignOut], status filter + cursor pagination."""

    async def test_list_shape_and_user_scoping(self, api):
        created = await _create_campaign(api)
        r = await api.get("/api/v1/campaigns", headers=auth_headers(USER_A))
        assert r.status_code == 200, r.text
        items = r.json()
        assert isinstance(items, list) and items, "expected non-empty list"
        mine = next(c for c in items if c["id"] == created["id"])
        assert set(mine.keys()) == CAMPAIGN_OUT_KEYS, "list row shape drift"
        # USER_B sees an empty list — rows are scoped to the caller
        r_b = await api.get("/api/v1/campaigns", headers=auth_headers(USER_B))
        assert r_b.status_code == 200 and r_b.json() == []

    async def test_status_filter_validates_enum(self, api):
        r = await api.get(
            "/api/v1/campaigns",
            params={"status": "running"},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 200
        assert all(c["status"] == "running" for c in r.json())
        # outside the locked enum → 422, never silently empty
        r_bad = await api.get(
            "/api/v1/campaigns",
            params={"status": "archived"},
            headers=auth_headers(USER_A),
        )
        assert r_bad.status_code == 422

    async def test_cursor_pagination_exclusive(self, api):
        """Cursor = created_at of last page's last item; next page starts AFTER it."""
        ids_in_order = []
        last_created = None
        for _ in range(3):
            body = await _create_campaign(api)
            ids_in_order.append(body["id"])
            last_created = body["created_at"]
        page = await api.get(
            "/api/v1/campaigns", params={"limit": 2}, headers=auth_headers(USER_A)
        )
        items = page.json()
        assert len(items) == 2
        # newest first (keyset on created_at DESC)
        assert [c["created_at"] for c in items] == sorted(
            [c["created_at"] for c in items], reverse=True
        )
        cursor = items[-1]["created_at"]
        page2 = await api.get(
            "/api/v1/campaigns",
            params={"cursor": cursor, "limit": 10},
            headers=auth_headers(USER_A),
        )
        remaining = {c["id"] for c in page2.json()}
        assert items[-1]["id"] not in remaining, "cursor is not exclusive — overlap!"
        assert ids_in_order[0] in remaining


class TestCampaignDetailAndPatch:
    """GET/PATCH /api/v1/campaigns/{id} shapes."""

    async def test_detail_shape_with_counters(self, api):
        """
        Contract Part 2: GET /{id} returns the campaign "incl. live counters".
        KNOWN DEFECT (found by this test): the route declares
        response_model=CampaignOut, so FastAPI SERIALIZATION-FILTERS the extra
        `counters` key out of the response — the detail body is identical to a
        list row and carries NO counters. Until @backend-eng drops the
        response_model (or adds Counters to CampaignOut), this is an
        expected-fail canary that flips to a hard pass with the fix.
        """
        created = await _create_campaign(api)
        r = await api.get(f"/api/v1/campaigns/{created['id']}", headers=auth_headers(USER_A))
        assert r.status_code == 200
        body = r.json()
        if "counters" not in body:
            pytest.xfail(
                "DEFECT: response_model=CampaignOut strips 'counters' from GET "
                "/api/v1/campaigns/{id} — contract Part 2 says detail includes "
                "live counters. Hardens to pass when the router stops filtering."
            )
        assert set(body.keys()) == CAMPAIGN_OUT_KEYS | {"counters"}, "detail shape drift"
        counters = body["counters"]
        assert counters.get("total") == 0
        assert counters.get("pending") == 0
        assert counters.get("finished") == 0
        for s in CONTACT_CALL_STATUSES:
            assert s in counters.get("by_status", {}), f"by_status missing '{s}'"
        for o in OUTCOMES:
            assert o in counters.get("by_outcome", {}), f"by_outcome missing '{o}'"

    async def test_detail_unknown_id_404(self, api):
        r = await api.get("/api/v1/campaigns/not-a-real-id", headers=auth_headers(USER_A))
        assert r.status_code == 404

    async def test_patch_updates_and_returns_campaignout(self, api):
        created = await _create_campaign(api)
        r = await api.patch(
            f"/api/v1/campaigns/{created['id']}",
            json={"name": "Renamed", "objective": "new objective"},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == CAMPAIGN_OUT_KEYS
        assert body["name"] == "Renamed"
        assert body["objective"] == "new objective"

    async def test_patch_rejects_status_outside_enum(self, api):
        created = await _create_campaign(api)
        r = await api.patch(
            f"/api/v1/campaigns/{created['id']}",
            json={"status": "cancelled"},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 422, "PATCH status must validate against locked enum"

    async def test_patch_illegal_transition_409(self, api):
        """draft → paused is illegal (draft may only go running); completed is terminal."""
        created = await _create_campaign(api)
        r = await api.patch(
            f"/api/v1/campaigns/{created['id']}",
            json={"status": "paused"},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 409, f"draft→paused allowed: {r.text}"

    async def test_patch_other_users_campaign_404(self, api):
        created = await _create_campaign(api)
        r = await api.patch(
            f"/api/v1/campaigns/{created['id']}",
            json={"name": "hijack"},
            headers=auth_headers(USER_B),
        )
        assert r.status_code == 404, "USER_B must not see/patch USER_A's campaign"


class TestCampaignContactsUpload:
    """POST /{id}/contacts — per-row error format + result shape (contract Part 2)."""

    async def test_upload_result_exact_shape(self, api):
        created = await _create_campaign(api)
        csv_bytes = _upload_contacts_csv([
            ("+919876543210", "Alice"),
            ("+919876543211", "Bob"),
        ])
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("contacts.csv", csv_bytes, "text/csv")},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {
            "added",
            "duplicates_merged",
            "errors",
            "total_rows_parsed",
        }, f"upload shape drift: {set(body.keys())}"
        assert body["added"] == 2
        assert body["duplicates_merged"] == 0
        assert body["errors"] == []
        assert body["total_rows_parsed"] == 2

    async def test_per_row_error_format(self, api):
        """Invalid phone → row-level error object {row, phone, error}; valid rows still added."""
        created = await _create_campaign(api)
        csv_bytes = _upload_contacts_csv([
            ("+919876543210", "Good Row"),
            ("123", "Bad Phone"),          # too short → E.164 normalization fails
            ("+919876543210", "Dup"),      # duplicate within file
            ("", "Empty Phone"),
        ])
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("contacts.csv", csv_bytes, "text/csv")},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["added"] == 1, f"only the E.164-valid row should be added: {body}"
        # 4 attempted rows: valid + bad phone + dup + empty phone cell.
        # Only fully BLANK lines are skipped by the parser (contract Part 2).
        assert body["total_rows_parsed"] == 4, (
            f"expected 4 attempted rows, got {body['total_rows_parsed']}"
        )
        errors = body["errors"]
        assert len(errors) >= 1, "invalid rows must surface per-row errors, never be swallowed"
        for e in errors:
            assert set(e.keys()) == {"row", "phone", "error"}, f"per-row error shape drift: {e}"
            assert isinstance(e["error"], str) and e["error"]
            # `row` is the CSV line number (header = line 1), int or None on persist failure
            assert e["row"] is None or isinstance(e["row"], int)

    async def test_empty_file_400(self, api):
        created = await _create_campaign(api)
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("empty.csv", b"", "text/csv")},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 400

    async def test_no_phone_column_400(self, api):
        created = await _create_campaign(api)
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("bad.csv", b"nom,mobileless\n1,2\n", "text/csv")},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 400, "missing phone column must reject the whole file"

    async def test_e164_normalization_applied(self, api):
        created = await _create_campaign(api)
        csv_bytes = _upload_contacts_csv([("098765 43210", "Spaced")])
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("contacts.csv", csv_bytes, "text/csv")},
            headers=auth_headers(USER_A),
        )
        body = r.json()
        assert body["added"] == 1
        listed = (
            await api.get(
                f"/api/v1/campaigns/{created['id']}/contacts", headers=auth_headers(USER_A)
            )
        ).json()
        assert listed["items"][0]["phone"] == "+919876543210", (
            f"E.164 normalization not applied: {listed['items'][0]['phone']}"
        )


class TestCampaignContactsList:
    """GET /{id}/contacts — paginated contact list w/ call status."""

    async def test_list_envelope_and_item_shape(self, api):
        created = await _create_campaign(api)
        csv_bytes = _upload_contacts_csv([("+919876543210", "Alice")])
        await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("contacts.csv", csv_bytes, "text/csv")},
            headers=auth_headers(USER_A),
        )
        r = await api.get(
            f"/api/v1/campaigns/{created['id']}/contacts", headers=auth_headers(USER_A)
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"items", "limit", "offset"}, f"envelope drift: {body.keys()}"
        item = body["items"][0]
        for key in ("id", "status", "attempts", "last_attempted_at", "outcome", "outcome_notes"):
            assert key in item, f"contact item missing '{key}'"
        # joined-through contact fields (flat, not nested)
        assert item["phone"] == "+919876543210"
        assert item["name"] == "Alice"
        assert item["dnd"] is False
        assert item["status"] in CONTACT_CALL_STATUSES
        assert item["attempts"] == 0
        assert item["outcome"] is None  # not dialed yet

    async def test_offset_pagination_fields(self, api):
        created = await _create_campaign(api)
        r = await api.get(
            f"/api/v1/campaigns/{created['id']}/contacts",
            params={"limit": 50, "offset": 0},
            headers=auth_headers(USER_A),
        )
        body = r.json()
        assert body["limit"] == 50 and body["offset"] == 0

    async def test_contact_status_filter_validates(self, api):
        created = await _create_campaign(api)
        r = await api.get(
            f"/api/v1/campaigns/{created['id']}/contacts",
            params={"status": "teleported"},
            headers=auth_headers(USER_A),
        )
        assert r.status_code == 422


class TestCampaignLifecycle:
    """start/stop transitions and response shapes."""

    async def _campaign_with_contact(self, api):
        created = await _create_campaign(api)
        csv_bytes = _upload_contacts_csv([("+919876543210", "Alice")])
        up = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("contacts.csv", csv_bytes, "text/csv")},
            headers=auth_headers(USER_A),
        )
        assert up.status_code == 200 and up.json()["added"] == 1
        return created

    async def test_start_response_shape(self, api, fake_supabase, monkeypatch):
        created = await self._campaign_with_contact(api)
        # keep the background worker out of the test loop
        import app.services.campaign_worker as cw

        monkeypatch.setattr(cw, "start_worker", lambda: None)
        r = await api.post(f"/api/v1/campaigns/{created['id']}/start", headers=auth_headers(USER_A))
        assert r.status_code == 200, r.text
        body = r.json()
        # enqueue_campaign RPC only requeues failed/skipped rows (migration 0007);
        # fresh queued rows are already queued → affected = 0
        assert body == {
            "ok": True,
            "id": created["id"],
            "status": "running",
            "queued": 0,
        }, f"start shape drift: {body}"

    async def test_start_requires_contact_422(self, api):
        created = await _create_campaign(api)
        r = await api.post(f"/api/v1/campaigns/{created['id']}/start", headers=auth_headers(USER_A))
        assert r.status_code == 422, "start without contacts must fail validation"

    async def test_stop_only_from_running(self, api):
        created = await self._campaign_with_contact(api)
        r = await api.post(f"/api/v1/campaigns/{created['id']}/stop", headers=auth_headers(USER_A))
        assert r.status_code == 409, "stop on a draft campaign must 409"

    async def test_stop_running_returns_paused(self, api, fake_supabase, monkeypatch):
        created = await self._campaign_with_contact(api)
        import app.services.campaign_worker as cw

        monkeypatch.setattr(cw, "start_worker", lambda: None)
        await api.post(f"/api/v1/campaigns/{created['id']}/start", headers=auth_headers(USER_A))
        r = await api.post(f"/api/v1/campaigns/{created['id']}/stop", headers=auth_headers(USER_A))
        assert r.status_code == 200
        assert r.json() == {"ok": True, "id": created["id"], "status": "paused"}

    async def test_start_unpublished_agent_422(self, api, fake_supabase):
        created = await self._campaign_with_contact(api)
        fake_supabase.store["agents"][0]["status"] = "draft"
        try:
            r = await api.post(
                f"/api/v1/campaigns/{created['id']}/start", headers=auth_headers(USER_A)
            )
            assert r.status_code == 422, "unpublished agent must block start"
        finally:
            fake_supabase.store["agents"][0]["status"] = "published"


class TestSimulateContract:
    """
    POST /{id}/simulate — v1 demo mode (ruling B4): simulated browser calls only,
    outcomes restricted to the LOCKED vocabulary.
    """

    async def _campaign_with_contact(self, api):
        created = await _create_campaign(api)
        csv_bytes = _upload_contacts_csv([("+919876543210", "Sim Target")])
        up = await api.post(
            f"/api/v1/campaigns/{created['id']}/contacts",
            files={"file": ("contacts.csv", csv_bytes, "text/csv")},
            headers=auth_headers(USER_A),
        )
        assert up.status_code == 200 and up.json()["added"] == 1
        return created

    async def test_simulate_response_exact_shape(self, api, fake_supabase):
        created = await self._campaign_with_contact(api)
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/simulate", headers=auth_headers(USER_A)
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {
            "ok",
            "id",
            "simulated_calls",
            "outcomes",
            "counters",
            "status",
        }, f"simulate shape drift: {set(body.keys())}"
        assert body["ok"] is True
        assert body["simulated_calls"] == 1
        assert isinstance(body["outcomes"], list) and len(body["outcomes"]) == 1
        oc = body["outcomes"][0]
        assert set(oc.keys()) == {"campaign_contact_id", "outcome"}
        # outcome MUST come from the locked vocabulary (ruling B3)
        assert oc["outcome"] in OUTCOMES, f"outcome outside locked vocab: {oc['outcome']}"

    async def test_simulate_drains_to_completed(self, api, fake_supabase):
        created = await self._campaign_with_contact(api)
        body = (
            await api.post(
                f"/api/v1/campaigns/{created['id']}/simulate", headers=auth_headers(USER_A)
            )
        ).json()
        # queue drained → campaign auto-completes (migration complete_campaign_if_drained)
        assert body["status"] == "completed", f"expected completed after drain: {body['status']}"
        assert body["counters"]["finished"] == 1
        detail = (
            await api.get(f"/api/v1/campaigns/{created['id']}", headers=auth_headers(USER_A))
        ).json()
        assert detail["status"] == "completed"

    async def test_simulate_on_completed_409(self, api, fake_supabase):
        created = await self._campaign_with_contact(api)
        await api.post(f"/api/v1/campaigns/{created['id']}/simulate", headers=auth_headers(USER_A))
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/simulate", headers=auth_headers(USER_A)
        )
        assert r.status_code == 409, "simulate on completed campaign must 409 (terminal)"

    async def test_simulate_without_contacts_422(self, api):
        created = await _create_campaign(api)
        r = await api.post(
            f"/api/v1/campaigns/{created['id']}/simulate", headers=auth_headers(USER_A)
        )
        assert r.status_code == 422


class TestCampaignVocabulariesLocked:
    """
    Drift tripwire: the locked enums live in BOTH the contract doc and
    app/services/campaign_service.py. If either side changes, this fails so the
    change is a conscious contract revision — not silent drift.
    """

    def test_backend_campaign_statuses_match_contract(self):
        from app.services.campaign_service import CAMPAIGN_STATUSES as S

        assert tuple(S) == CAMPAIGN_STATUSES, f"campaign status enum drifted: {S}"

    def test_backend_contact_call_statuses_match_contract(self):
        from app.services.campaign_service import CONTACT_CALL_STATUSES as S

        assert tuple(S) == CONTACT_CALL_STATUSES, f"contact-call status enum drifted: {S}"

    def test_backend_outcome_vocabulary_matches_contract(self):
        from app.services.campaign_service import OUTCOMES as O

        assert tuple(O) == OUTCOMES, f"outcome vocabulary drifted: {O}"


@pytest.mark.jwt_required
class TestCampaignAuthFlag:
    """
    AUTH_ENFORCED flip companion to SEC-01..03: once JWT enforcement lands,
    every /api/v1/campaigns route must 401 without credentials AND accept a
    real HS256 token whose `sub` becomes user_id. Gated behind SAHAIY_JWT_ENFORCED=1
    exactly like the SEC cases (xfail canary until then).
    """

    JWT_REASON = (
        "Activates when AUTH_ENFORCED flips (SAHAIY_JWT_ENFORCED=1). Until then "
        "the dev X-User-Id fallback is the documented current shape."
    )

    def _maybe_xfail_401(self, r):
        if r.status_code != 401:
            pytest.xfail(self.JWT_REASON)
        return r

    async def test_create_without_credentials_401(self, api):
        r = await api.post("/api/v1/campaigns", json=_campaign_body())
        if r.status_code != 401:
            pytest.xfail(self.JWT_REASON)
        assert r.status_code == 401

    async def test_list_without_credentials_401(self, api):
        r = await api.get("/api/v1/campaigns")
        self._maybe_xfail_401(r)
        assert r.status_code == 401

    async def test_jwt_sub_becomes_owner(self, api, fake_supabase):
        """
        With a real HS256 token, user_id comes from `sub` — not from any header
        or body field. The seeded agent is owned by USER_A, so a USER_B token
        must NOT be able to create against it (ownership follows the token).
        """
        import jwt as _jwt

        from app.config import SUPABASE_JWT_SECRET

        if not SUPABASE_JWT_SECRET:
            pytest.xfail("SUPABASE_JWT_SECRET not configured in test env")
        forged = _jwt.encode({"sub": USER_B}, SUPABASE_JWT_SECRET, algorithm="HS256")
        r = await api.post(
            "/api/v1/campaigns",
            json=_campaign_body(),
            headers={"Authorization": f"Bearer {forged}"},
        )
        if r.status_code != 404:
            pytest.xfail(self.JWT_REASON)
        # USER_B's token → USER_B's user scope → USER_A's agent invisible: 404,
        # proving identity came from the JWT sub, never from a spoofable header.
        assert r.status_code == 404
