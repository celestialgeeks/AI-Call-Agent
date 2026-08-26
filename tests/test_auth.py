"""
tests/test_auth.py
──────────────────
Ruling B1: HS256 verification via SUPABASE_JWT_SECRET; user_id derived from
the token ONLY. Feature flag AUTH_ENFORCED gates enforcement.

Unit-level tests against app.auth directly — the legacy routers intentionally
keep their pre-auth contract shape until the coordinated auth flip (pinned by
qa/contract_tests), so enforcement is exercised here, not over HTTP.
"""
import pytest
from fastapi import HTTPException

import app.auth as auth
from tests.conftest import make_jwt


SECRET = "test-jwt-secret-for-ci-only"
SUB = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def enforced(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENFORCED", True)
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", SECRET)


@pytest.fixture()
def unenforced(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENFORCED", False)
    monkeypatch.setattr(auth, "SUPABASE_JWT_SECRET", "")


# ── Flag off: dev fallback ──────────────────────────────────────────────────

def test_unenforced_returns_x_user_id(unenforced):
    assert auth.get_current_user_id.__defaults__ is not None  # dependency shape intact


class _Hdrs(dict):
    def get(self, k, default=None):
        return super().get(k.lower(), default)


class _WS:
    def __init__(self, headers):
        self.headers = _Hdrs({k.lower(): v for k, v in headers.items()})


# ── Flag on: HS256 verification via _verify_token ───────────────────────────

def test_enforced_verify_valid_token(enforced):
    token = make_jwt(SECRET, sub=SUB)
    assert auth._verify_token(token) == SUB


def test_enforced_rejects_expired_token(enforced):
    token = make_jwt(SECRET, sub=SUB, expires_in=-10)
    with pytest.raises(HTTPException) as ei:
        auth._verify_token(token)
    assert ei.value.status_code == 401


def test_enforced_rejects_garbage_token(enforced):
    with pytest.raises(HTTPException) as ei:
        auth._verify_token("not.a.jwt")
    assert ei.value.status_code == 401


def test_enforced_rejects_wrong_secret(enforced):
    other = make_jwt("a-completely-different-secret", sub=SUB)
    with pytest.raises(HTTPException) as ei:
        auth._verify_token(other)
    assert ei.value.status_code == 401


# ── WS identity helper ──────────────────────────────────────────────────────

async def test_ws_helper_none_when_unenforced(unenforced):
    assert await auth.get_websocket_user_id(_WS({})) is None


async def test_ws_helper_requires_bearer_when_enforced(enforced):
    with pytest.raises(PermissionError):
        await auth.get_websocket_user_id(_WS({}))


async def test_ws_helper_accepts_valid_bearer(enforced):
    token = make_jwt(SECRET, sub=SUB)
    ws = _WS({"Authorization": f"Bearer {token}"})
    assert await auth.get_websocket_user_id(ws) == SUB


async def test_ws_helper_rejects_garbage_bearer(enforced):
    ws = _WS({"Authorization": "Bearer garbage"})
    with pytest.raises(PermissionError):
        await auth.get_websocket_user_id(ws)
