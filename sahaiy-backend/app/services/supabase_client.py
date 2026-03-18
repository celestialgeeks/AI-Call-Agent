"""
app/services/supabase_client.py
────────────────────────────────
Server-side Supabase client using the service role key.
This bypasses RLS and should ONLY be used server-side, never exposed to clients.
"""

import logging
from functools import lru_cache
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_SERVICE_KEY

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """
    Returns a cached Supabase service-role client.
    Called once at startup; subsequent calls return the same instance.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning(
            "[SupabaseClient] SUPABASE_URL or SUPABASE_SERVICE_KEY not set — "
            "Supabase operations will fail."
        )
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
