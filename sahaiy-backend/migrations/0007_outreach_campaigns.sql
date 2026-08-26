-- ═══════════════════════════════════════════════════════════
--  Migration 0007 — Outreach Campaigns (issue #7)
--  Additive only: no changes to existing tables except one new
--  nullable column on conversations (campaign_contact_id).
--
--  Run in Supabase SQL Editor, or: psql -f migrations/0007_outreach_campaigns.sql
--
--  Rulings B2/B3/B4 (binding):
--    B2  Queue = Postgres FOR UPDATE SKIP LOCKED (no new service)
--    B3  Outcome vocab LOCKED:
--        connected|no_answer|busy|voicemail|callback_requested|not_interested|dnd|failed
--    B4  campaign status: draft|running|paused|completed
--        contact-call status: queued|dialing|completed|failed|skipped|dnd
-- ═══════════════════════════════════════════════════════════

-- ── Campaigns ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.campaigns (
  id            UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id       UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  agent_id      UUID REFERENCES public.agents(id) ON DELETE SET NULL,
  name          TEXT NOT NULL,
  objective     TEXT,
  status        TEXT DEFAULT 'draft'
                CHECK (status IN ('draft','running','paused','completed')),
  schedule_start_at TIMESTAMPTZ,
  schedule_end_at   TIMESTAMPTZ,
  calling_hours     JSONB DEFAULT '{"start":"09:00","end":"18:00"}'::JSONB,
  timezone          TEXT DEFAULT 'Asia/Kolkata',
  retry_max_attempts INTEGER DEFAULT 3 CHECK (retry_max_attempts BETWEEN 1 AND 10),
  retry_after_min    INTEGER DEFAULT 60 CHECK (retry_after_min >= 0),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_user_status
  ON public.campaigns (user_id, status, created_at DESC);

-- ── Contacts ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.contacts (
  id         UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id    UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  phone      TEXT NOT NULL,           -- E.164 normalized, e.g. +919876543210
  name       TEXT,
  attributes JSONB DEFAULT '{}'::JSONB,
  dnd        BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (user_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_contacts_user ON public.contacts (user_id);

-- ── Campaign ↔ Contact join (the call queue rows) ──────────
CREATE TABLE IF NOT EXISTS public.campaign_contacts (
  id                UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  campaign_id       UUID REFERENCES public.campaigns(id) ON DELETE CASCADE NOT NULL,
  contact_id        UUID REFERENCES public.contacts(id) ON DELETE CASCADE NOT NULL,
  status            TEXT DEFAULT 'queued'
                    CHECK (status IN ('queued','dialing','completed','failed','skipped','dnd')),
  attempts          INTEGER DEFAULT 0,
  last_attempted_at TIMESTAMPTZ,
  outcome           TEXT
                    CHECK (outcome IS NULL OR outcome IN (
                      'connected','no_answer','busy','voicemail',
                      'callback_requested','not_interested','dnd','failed')),
  outcome_notes     TEXT,
  recording_url     TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (campaign_id, contact_id)
);

-- Queue index for FOR UPDATE SKIP LOCKED dequeue (B2):
-- worker selects queued rows of a running campaign ordered by id.
CREATE INDEX IF NOT EXISTS idx_campaign_contacts_queue
  ON public.campaign_contacts (campaign_id, status, id);

CREATE INDEX IF NOT EXISTS idx_campaign_contacts_contact
  ON public.campaign_contacts (contact_id);

-- ── Conversations: link calls back to the campaign contact ─
ALTER TABLE public.conversations
  ADD COLUMN IF NOT EXISTS campaign_contact_id UUID
    REFERENCES public.campaign_contacts(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_campaign_contact
  ON public.conversations (campaign_contact_id);

-- ── updated_at touch trigger on campaigns ──────────────────
CREATE OR REPLACE FUNCTION public.touch_campaign_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_campaigns_touch ON public.campaigns;
CREATE TRIGGER trg_campaigns_touch
  BEFORE UPDATE ON public.campaigns
  FOR EACH ROW EXECUTE FUNCTION public.touch_campaign_updated_at();

-- ═══════════════════════════════════════════════════════════
--  Queue primitives (B2): atomic enqueue/dequeue via RPC.
--  The FastAPI worker dequeues with FOR UPDATE SKIP LOCKED so
--  N workers never grab the same contact-call.
-- ═══════════════════════════════════════════════════════════

-- Enqueue all queued contacts of a campaign (idempotent-ish: only queued rows).
CREATE OR REPLACE FUNCTION public.enqueue_campaign(p_campaign_id UUID)
RETURNS INTEGER AS $$
DECLARE
  affected INTEGER;
BEGIN
  UPDATE public.campaign_contacts
     SET status = 'queued'
   WHERE campaign_id = p_campaign_id
     AND status IN ('failed', 'skipped')
     AND attempts < (SELECT retry_max_attempts FROM public.campaigns WHERE id = p_campaign_id);
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Dequeue next contact-call (FOR UPDATE SKIP LOCKED — B2).
-- Marks it dialing and bumps attempts atomically; skips DND contacts.
CREATE OR REPLACE FUNCTION public.dequeue_campaign_contact(
  p_campaign_id UUID,
  p_worker_id   TEXT
)
RETURNS TABLE (
  cc_id             UUID,
  contact_id        UUID,
  phone             TEXT,
  contact_name      TEXT,
  attempts          INTEGER
) AS $$
DECLARE
  picked UUID;
BEGIN
  -- One row at a time, skipping rows locked by other workers.
  SELECT cc.id INTO picked
  FROM public.campaign_contacts cc
  JOIN public.contacts c ON c.id = cc.contact_id
  WHERE cc.campaign_id = p_campaign_id
    AND cc.status = 'queued'
    AND c.dnd = FALSE          -- hard DND guard at dequeue time
  ORDER BY cc.attempts ASC, cc.id ASC
  LIMIT 1
  FOR UPDATE OF cc SKIP LOCKED;

  IF picked IS NULL THEN
    RETURN;
  END IF;

  UPDATE public.campaign_contacts
     SET status = 'dialing',
         attempts = attempts + 1,
         last_attempted_at = NOW()
   WHERE id = picked;

  RETURN QUERY
  SELECT cc.id, cc.contact_id, c.phone, c.name, cc.attempts
  FROM public.campaign_contacts cc
  JOIN public.contacts c ON c.id = cc.contact_id
  WHERE cc.id = picked;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Atomic campaign completion check: flip to completed when queue drains.
CREATE OR REPLACE FUNCTION public.complete_campaign_if_drained(p_campaign_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
  remaining INTEGER;
BEGIN
  SELECT COUNT(*) INTO remaining
  FROM public.campaign_contacts
  WHERE campaign_id = p_campaign_id
    AND status IN ('queued', 'dialing');

  IF remaining = 0 THEN
    UPDATE public.campaigns
       SET status = 'completed'
     WHERE id = p_campaign_id AND status IN ('running', 'paused');
    RETURN TRUE;
  END IF;
  RETURN FALSE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
