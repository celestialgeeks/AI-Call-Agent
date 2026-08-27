-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 0003: outbound phone calling (issue #24)
-- Adds outbound-call tracking to conversations. Single additive migration;
-- safe to re-run on existing projects.
-- ─────────────────────────────────────────────────────────────────────────────

-- LiveKit room backing this call (outbound-<conversation_id> convention).
ALTER TABLE public.conversations
  ADD COLUMN IF NOT EXISTS room_name TEXT;

-- 'ringing' covers the window between CreateSIPParticipant and answer;
-- existing CHECK allows resolved/escalated/missed/in_progress only, so we
-- widen it idempotently by dropping + re-adding with the new vocabulary.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'conversations_status_check'
      AND conrelid = 'public.conversations'::regclass
  ) THEN
    ALTER TABLE public.conversations DROP CONSTRAINT conversations_status_check;
  END IF;
END $$;

ALTER TABLE public.conversations
  ADD CONSTRAINT conversations_status_check
  CHECK (status IN ('resolved','escalated','missed','in_progress','ringing'));

-- Direction of the call: inbound (dashboard/webhook) vs outbound PSTN dial.
ALTER TABLE public.conversations
  ADD COLUMN IF NOT EXISTS direction TEXT DEFAULT 'inbound'
    CHECK (direction IN ('inbound','outbound'));

-- Channel the conversation arrived on.
ALTER TABLE public.conversations
  ADD COLUMN IF NOT EXISTS channel TEXT DEFAULT 'web'
    CHECK (channel IN ('web','phone','whatsapp'));

-- Index for status polling of live outbound calls.
CREATE INDEX IF NOT EXISTS idx_conversations_room_name
  ON public.conversations (room_name) WHERE room_name IS NOT NULL;
