"""
tests/helpers.py
────────────────
Shared Supabase-mock builders for router tests.

The routers talk to supabase-py through chained query builders:
    supabase.table("agents").select("name").eq("id", x).single().execute()
We build a MagicMock whose every chained method returns the same builder and
whose terminal .execute() returns a configurable FakeResult.
"""

from unittest.mock import MagicMock


class FakeResult:
    def __init__(self, data=None):
        self.data = data


def make_table_builder(data=None):
    """Chainable mock whose .execute() returns FakeResult(data)."""
    builder = MagicMock()
    for method in ("select", "insert", "update", "delete", "eq",
                   "single", "maybe_single", "in_", "order", "limit"):
        getattr(builder, method).return_value = builder
    builder.execute.return_value = FakeResult(data)
    return builder


def make_rpc_result(payload):
    """Mock client whose .rpc(...).execute() returns FakeResult(payload)."""
    sb = MagicMock()
    rpc_builder = MagicMock()
    rpc_builder.execute.return_value = FakeResult(payload)
    sb.rpc.return_value = rpc_builder
    return sb


def make_supabase(table_results: dict):
    """
    Build a mock supabase client.

    table_results maps table name -> data returned by any chain on that table.
    Tables not listed return data=None. Terminal .execute() is always available.
    """
    sb = MagicMock()

    def table(name):
        return make_table_builder(table_results.get(name))

    sb.table.side_effect = table
    return sb
