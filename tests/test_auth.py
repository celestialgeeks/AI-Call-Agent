"""
tests/test_auth.py
──────────────────
Ruling B1: HS256 verification via SUPABASE_JWT_SECRET; user_id derived from
the token ONLY. Feature flag AUTH_ENFORCED gates enforcement.
"""
import os
from unittest.mock import patch

import jwt as pyjwt
import pytest

from tests.helpers import make_supabase, make_rpc_result
from tests.conftest import make_jwt


SECRET = "test-jwt-secret-for-ci-only"
SUB = "11111111-1111-1111-1111-111111111111"


def build_client():
    """Import the app fresh with a mocked Supabase client."""
    with patch("app.services.supabase_client.create_client", return_value=make_supabase({})):
        from app.main import app
    return app


@pytest.fixture()
def enforced_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENFORCED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    # Reload config + modules that read it at import time.
    import importlib
    import app.config as config
    importlib.reload(config)
    import app.errors  # noqa: F401 — ensure module present
    import app.auth as auth
    importlib.reload(auth)
    import app.routers.calls as calls
    importlib.reload(calls)
    import app.main as main
    importlib.reload(main)
    yield main.app
    # Restore legacy state for other tests.
    monkeypatch.setenv("AUTH_ENFORCED", "false")
    importlib.reload(config)
    importlib.reload(auth)
    importlib.reload(main)


# ── Flag off (default): legacy behaviour preserved ──────────────────────────

def test_flag_off_allows_missing_token(client):
    # AUTH_ENFORCED=false → no auth required; validation error only because
    # the multipart file is missing, NOT 401.
    resp = client.post("/stt/transcribe")
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
    assert resp.json()["error"]["code"] != "missing_token"


# ── Flag on: HS256 verification ─────────────────────────────────────────────

def test_flag_on_rejects_missing_token(enforced_env):
    from fastapi.testclient import TestClient
    with TestClient(enforced_env) as tc:
        resp = tc.post("/stt/transcribe")
        assert resp.status_code == 401
        err = resp.json()["error"]
        assert err["code"] == "missing_token"
        assert err["request_id"]


def test_flag_on_rejects_garbage_token(enforced_env):
    from fastapi.testclient import TestClient
    with TestClient(enforced_env) as tc:
        resp = tc.post("/stt/transcribe",
                       headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_token"


def test_flag_on_rejects_expired_token(enforced_env):
    token = make_jwt(SECRET, expires_in=-10)
    from fastapi.testclient import TestClient
    with TestClient(enforced_env) as tc:
        resp = tc.post("/stt/transcribe", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "token_expired"


def test_flag_on_accepts_valid_hs256_token(enforced_env, monkeypatch):
    # Valid token passes the auth gate; request proceeds to rate-limit/STT path
    # (fails later at whisper upstream, which is fine — it's past auth).
    token = make_jwt(SECRET, sub=SUB)
    from fastapi.testclient import TestClient
    with TestClient(enforced_env) as tc:
        resp = tc.post(
            "/stt/transcribe",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("a.wav", b"RIFFfake", "audio/wav")},
        )
        assert resp.status_code != 401, f"unexpected 401: {resp.text}"


def test_wrong_secret_token_rejected(enforced_env):
    other = make_jwt("a-completely-different-secret", sub=SUB)
    from fastapi.testclient import TestClient
    with TestClient(enforced_env) as tc:
        resp = tc.post("/stt/transcribe", headers={"Authorization": f"Bearer {other}"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] in {"invalid_token", "signature_expired"}
