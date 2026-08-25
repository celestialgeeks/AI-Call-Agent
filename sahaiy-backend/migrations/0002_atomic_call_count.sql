-- migrations/0002_atomic_call_count.sql
-- Issue #4 (SEC-04): atomic agent call_count increment, exactly-once per call.
--
-- Problem: call_end used `.update({"call_count": supabase.rpc("get_agent_call_count", …)})`
-- which referenced a nonexistent RPC → silent no-op; and a naive
-- `call_count = call_count + 1` on every end request would double-count when
-- clients retry / double-fire call_end.
--
-- Fix: `finalize_conversation` performs the terminal transition AND the atomic
-- increment in ONE statement guarded on the row still being `in_progress`.
-- Double call_end → second UPDATE matches 0 rows → increment happens exactly once.
--
-- Idempotent: CREATE OR REPLACE + IF NOT EXISTS guards. Safe to re-run.

CREATE OR REPLACE FUNCTION public.increment_agent_call_count(p_agent_id UUID)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE public.agents
     SET call_count = COALESCE(call_count, 0) + 1
   WHERE id = p_agent_id;
$$;

CREATE OR REPLACE FUNCTION public.finalize_conversation(
  p_conversation_id UUID,
  p_status          TEXT,
  p_duration_sec    INTEGER,
  p_transcript      TEXT,
  p_csat_score      INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_updated BOOLEAN;
  v_agent_id UUID;
BEGIN
  -- Validate terminal status against the conversations CHECK constraint set.
  IF p_status NOT IN ('resolved', 'escalated', 'missed') THEN
    RAISE EXCEPTION 'invalid terminal status: %', p_status;
  END IF;

  -- Single-statement transition + increment guard:
  -- only fires when the conversation is still in_progress.
  UPDATE public.conversations c
     SET status       = p_status,
         duration_sec = p_duration_sec,
         transcript   = p_transcript,
         csat_score   = COALESCE(p_csat_score, c.csat_score),
         updated_at   = NOW()
   WHERE c.id = p_conversation_id
     AND c.status = 'in_progress'
  RETURNING c.agent_id INTO v_agent_id;

  v_updated := FOUND;

  IF v_updated AND v_agent_id IS NOT NULL THEN
    PERFORM public.increment_agent_call_count(v_agent_id);
  END IF;

  RETURN jsonb_build_object('updated', v_updated);
END;
$$;
