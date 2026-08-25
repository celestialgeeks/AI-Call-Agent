"""
tests/conftest.py
─────────────────
Shared fixtures: FastAPI TestClient with Supabase mocked out (no network).
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sahaiy-backend"))

# Ensure legacy mode for most tests unless a test overrides it.
os.environ.setdefault("AUTH_ENFORCED", "false")


@pytest.fixture()
def client():
    """TestClient with the Supabase client mocked (service layer untouched)."""
    from fastapi.testclient import TestClient

    with patch("app.services.supabase_client.create_client", return_value=MagicMock()):
        from app.main import app

        with TestClient(app) as test_client:
            yield test_client


def make_jwt(secret: str, sub: str = "11111111-1111-1111-1111-111111111111",
             expires_in: int = 3600):
    """Mint a real HS256 token like Supabase would."""
    import time
    import jwt as pyjwt

    now = int(time.time())
    payload = {"sub": sub, "aud": "authenticated", "role": "authenticated",
               "iat": now, "exp": now + expires_in}
    return pyjwt.encode(payload, secret, algorithm="HS256")
