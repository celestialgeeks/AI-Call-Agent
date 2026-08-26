"""
qa/contract_tests/test_campaigns_contracts.py
──────────────────────────────────────────────
Exact-shape conformance for the outreach boundary —
api-contracts-and-outreach-boundary-v1.md **Part 2**, endpoints table.

Part 2 shipped in backend PR #21 (commit 6eeb72c): POST /api/v1/campaigns is
live, so the old TestOutreachBoundaryDraft canary in test_contracts.py
(asserting 404/405) is replaced by these cases per the canary's own
instruction. All campaign endpoints are JWT-scoped: user_id is derived from
the Authorization bearer token — never from body/query (ruling B1). In the
unenforced CI environment (AUTH_ENFORCED=false, no SUPABASE_JWT_SECRET) the
real auth dependency falls back to an X-User-Id header; tests send it
explicitly so every request still exercises the true dependency path.

Runs against the real FastAPI app in-process with external services stubbed
(see qa/conftest.py).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import AGENT_ID, USER_A

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _agent_owned_by_user_a(fake_supabase):
    """
    The canned agent row in conftest's store predates the campaigns ownership
    model (it carries no user_id). Campaign create requires an agent owned by
    the caller — tag the canned row with USER_A so Part 2 flows can use it.
    """
    fake_supabase.store["agents"][0]["user_id"] = USER_A


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
SCHEDULE_KEYS = {"start_at", "end_at", "calling_hours", "timezone"}
RETRY_KEYS = {"max_attempts", "retry_after_min"}


def _auth(user_id=USER_A):
    """Header set consumed by get_current_user_id's unenforced dev fallback."""
    return {"X-User-Id": user_id}


# ── Part 2: POST /api/v1/campaigns ───────────────────────────────────────────


class TestCampaignsCreate:
    async def test_exact_shape_201(self, api):
        """
        Response must be EXACTLY the CampaignOut shape — no extra keys.
        status starts at 'draft' per contract Part 2 lifecycle.
        """
        r = await api.post(
            "/api/v1/campaigns",
            json={"name": "QA Campaign", "agent_id": AGENT_ID},
            headers=_auth(),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert set(body.keys()) == CAMPAIGN_OUT_KEYS, f"shape drift: {sorted(body.keys())}"
        assert body["name"] == "QA Campaign"
        assert body["agent_id"] == AGENT_ID
        assert body["status"] == "draft"
        assert body["user_id"] == USER_A  # derived from auth, echoed back
        assert isinstance(body["id"], str) and len(body["id"]) > 0
        # Nested shapes present even when unset (empty dicts / None members).
        assert set(body["schedule"].keys()) == SCHEDULE_KEYS
        assert set(body["retry_policy"].keys()) == RETRY_KEYS

    async def test_user_scoped_to_caller_not_body(self, api):
        """user_id in the response comes from auth context, never from the body."""
        r = await api.post(
            "/api/v1/campaigns",
            json={"name": "QA Spoof", "agent_id": AGENT_ID, "user_id": "evil-user"},
            headers=_auth(USER_A),
        )
        assert r.status_code == 201, r.text
        assert r.json()["user_id"] == USER_A

    async def test_missing_name_422(self, api):
        """name is required (min_length=1) per contract Part 2 create."""
        r = await api.post("/api/v1/campaigns", json={"agent_id": AGENT_ID}, headers=_auth())
        assert r.status_code == 422

    async def test_missing_agent_id_422(self, api):
        """agent_id is required per contract Part 2 ('create campaign (agent_id required)')."""
        r = await api.post("/api/v1/campaigns", json={"name": "No Agent"}, headers=_auth())
        assert r.status_code == 422

    async def test_unknown_agent_404(self, api, fake_supabase):
        """Agent must belong to the caller; a missing agent row → 404."""
        r = await api.post(
            "/api/v1/campaigns",
            json={"name": "QA Orphan", "agent_id": "99999999-9999-9999-9999-999999999999"},
            headers=_auth(),
        )
        assert r.status_code == 404, r.text

    async def test_no_auth_401(self, api):
        """JWT contract: no credentials → 401 Unauthorized, never a silent create."""
        r = await api.post(
            "/api/v1/campaigns", json={"name": "Anon", "agent_id": AGENT_ID}
        )
        assert r.status_code == 401, r.text


# ── Part 2: GET /api/v1/campaigns ────────────────────────────────────────────


class TestCampaignsList:
    async def test_exact_shape_array_of_campaign_out(self, api, fake_supabase):
        """Response must be EXACTLY a JSON array of CampaignOut objects."""
        created = []
        for i in range(2):
            r = await api.post(
                "/api/v1/campaigns",
                json={"name": f"List {i}", "agent_id": AGENT_ID},
                headers=_auth(),
            )
            assert r.status_code == 201, r.text
            created.append(r.json())

        r = await api.get("/api/v1/campaigns", headers=_auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, list)
        assert len(body) >= 2
        for item in body:
            assert set(item.keys()) == CAMPAIGN_OUT_KEYS, (
                f"shape drift: {sorted(item.keys())}"
            )

    async def test_status_filter_validated(self, api):
        """Unknown status filter → 422; vocabulary is the locked Part 2 enum."""
        r = await api.get("/api/v1/campaigns", params={"status": "bogus"}, headers=_auth())
        assert r.status_code == 422

    async def test_limit_bounds(self, api):
        """limit is validated server-side (ge=1, le=100)."""
        r = await api.get("/api/v1/campaigns", params={"limit": 0}, headers=_auth())
        assert r.status_code == 422

    async def test_no_auth_401(self, api):
        r = await api.get("/api/v1/campaigns")
        assert r.status_code == 401, r.text


# ── Boundary tripwire: routes that must NOT silently appear ──────────────────


class TestOutreachUnshippedRoutes:
    """Part 2 DRAFT surface beyond PR #21 — canary that wrong shapes don't ship."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("DELETE", "/api/v1/campaigns/abc"),
            ("POST", "/api/v1/outbound"),
        ],
    )
    async def test_unshipped_routes_stay_absent(self, api, method, path):
        r = await api.request(method, path, headers=_auth())
        assert r.status_code in (404, 405), (
            f"{method} {path} appeared with {r.status_code} — update contract "
            "tests per api-contracts-and-outreach-boundary-v1.md Part 2"
        )
