"""
tests/test_call_count.py
────────────────────────
SEC-04: call_end bumps agent call_count atomically, exactly once per
conversation even when call_end fires twice.

The router must use the `finalize_conversation` RPC (single guarded
transition+increment), NOT the old broken `.update({"call_count": rpc(...)})`
pattern referencing the nonexistent get_agent_call_count.
"""
from unittest.mock import MagicMock

import pytest

from tests.helpers import make_supabase, make_rpc_result


AGENT_ROW = {"id": "a1a1a1a1-0000-0000-0000-000000000001", "name": "Receptionist"}
CONV_ID = "b2b2b2b2-0000-0000-0000-000000000002"
USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def calls_client():
    """TestClient with a mock Supabase that records every rpc() invocation."""
    with patch_supabase():
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            yield tc


def patch_supabase():
    import unittest.mock as um

    sb = make_rpc_result({"updated": True})

    def _factory(*a, **k):
        return sb

    cm = um.patch("app.services.supabase_client.create_client", new=_factory)
    cm.__enter__()
    # get_supabase is @lru_cache'd; the campaign worker started in app lifespan
    # may have populated it during an earlier test's startup. Clear so this
    # test's patched create_client is actually used.
    from app.services.supabase_client import get_supabase
    get_supabase.cache_clear()
    # stash for assertions via module-level holder
    global _LAST_SB
    _LAST_SB = sb

    class _Ctx:
        def __enter__(self):
            return sb

        def __exit__(self, *a):
            return False

    return _Ctx()


def test_call_end_uses_finalize_conversation_rpc(calls_client):
    resp = calls_client.post(
        f"/agents/{AGENT_ROW['id']}/call/end",
        json={"user_id": USER_ID, "conversation_id": CONV_ID,
              "duration_sec": 42, "transcript": "hello", "status": "resolved"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    sb = _LAST_SB
    sb.rpc.assert_called_once()
    name, params = sb.rpc.call_args[0]
    assert name == "finalize_conversation"
    assert params["p_conversation_id"] == CONV_ID


def test_call_end_never_calls_get_agent_call_count(calls_client):
    """The broken legacy RPC pattern must be gone entirely."""
    calls_client.post(
        f"/agents/{AGENT_ROW['id']}/call/end",
        json={"user_id": USER_ID, "conversation_id": CONV_ID, "status": "resolved"},
    )
    sb = _LAST_SB
    for call in sb.mock_calls:
        assert "get_agent_call_count" not in str(call)


def test_double_call_end_increment_exactly_once_contract(calls_client):
    """
    SEC-04 guard: second call_end on the same conversation is an idempotent
    no-op — the RPC returns updated=False and the router still answers 200.
    (The exactly-once increment itself is enforced by the SQL function's
    status='in_progress' guard — see migrations/0002_atomic_call_count.sql.)
    """
    sb = _LAST_SB
    sb.rpc.return_value.execute.return_value.data = {"updated": True}

    body = {"user_id": USER_ID, "conversation_id": CONV_ID,
            "duration_sec": 10, "status": "resolved"}

    r1 = calls_client.post(f"/agents/{AGENT_ROW['id']}/call/end", json=body)
    assert r1.status_code == 200 and r1.json() == {"ok": True}

    # Simulate the DB having already finalized: guard matches 0 rows.
    sb.rpc.return_value.execute.return_value.data = {"updated": False}
    r2 = calls_client.post(f"/agents/{AGENT_ROW['id']}/call/end", json=body)
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}


def test_migration_sql_guards_on_in_progress():
    """Static check: the migration's UPDATE only fires while in_progress."""
    import os
    here = os.path.dirname(__file__)
    path = os.path.join(here, "..", "sahaiy-backend", "migrations",
                        "0002_atomic_call_count.sql")
    sql = open(path).read()
    assert "status = 'in_progress'" in sql.replace('"', "'").lower() or \
           "c.status = 'in_progress'" in sql
    assert "COALESCE(call_count, 0) + 1" in sql
