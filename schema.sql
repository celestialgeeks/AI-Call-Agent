-- ═══════════════════════════════════════════════════════════
--  SAHAIY — Supabase Database Schema
--  Run this in your Supabase SQL Editor:
--  https://supabase.com/dashboard → SQL Editor → New Query
-- ═══════════════════════════════════════════════════════════

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Profiles (extends auth.users) ──────────────────────────
CREATE TABLE IF NOT EXISTS public.profiles (
  id           UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
  full_name    TEXT,
  email        TEXT,
  avatar_url   TEXT,
  workspace_name TEXT GENERATED ALWAYS AS (
    SPLIT_PART(full_name, ' ', 1) || '''s Workspace'
  ) STORED,
  plan         TEXT DEFAULT 'free' CHECK (plan IN ('free','pro','enterprise')),
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-create profile on new user signup
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, full_name, email, avatar_url)
  VALUES (
    new.id,
    COALESCE(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', SPLIT_PART(new.email,'@',1)),
    new.email,
    new.raw_user_meta_data->>'avatar_url'
  );
  RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ── Agents ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.agents (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  name         TEXT NOT NULL,
  description  TEXT,
  system_prompt TEXT DEFAULT 'You are a helpful AI call agent.',
  first_message TEXT DEFAULT 'Hello! How can I help you today?',
  voice_name   TEXT DEFAULT 'Priya',
  voice_lang   TEXT DEFAULT 'Hindi/English',
  voice_gender TEXT DEFAULT 'Female',
  llm_model    TEXT DEFAULT 'Gemini 2.5 Flash',
  language     TEXT DEFAULT 'Hindi / English (Hinglish)',
  status       TEXT DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  icon         TEXT DEFAULT '🤖',
  template     TEXT DEFAULT 'blank',
  call_count   INTEGER DEFAULT 0,
  max_duration INTEGER DEFAULT 600,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── Conversations ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.conversations (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  agent_id     UUID REFERENCES public.agents(id) ON DELETE SET NULL,
  agent_name   TEXT,
  caller_name  TEXT,
  caller_number TEXT,
  duration_sec  INTEGER DEFAULT 0,
  status       TEXT DEFAULT 'resolved' CHECK (status IN ('resolved','escalated','missed','in_progress')),
  csat_score   INTEGER CHECK (csat_score BETWEEN 1 AND 5),
  transcript   TEXT,
  metadata     JSONB DEFAULT '{}',
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Backfill-safe additive changes for existing projects
ALTER TABLE public.conversations
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- ── Phone Numbers ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.phone_numbers (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  agent_id     UUID REFERENCES public.agents(id) ON DELETE SET NULL,
  number       TEXT NOT NULL,
  country      TEXT DEFAULT 'India',
  city         TEXT,
  capabilities TEXT[] DEFAULT ARRAY['inbound','outbound'],
  status       TEXT DEFAULT 'active' CHECK (status IN ('active','inactive')),
  call_count   INTEGER DEFAULT 0,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── Knowledge Base Docs ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.knowledge_docs (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  name         TEXT NOT NULL,
  type         TEXT DEFAULT 'file' CHECK (type IN ('file','url')),
  size_bytes   INTEGER,
  url          TEXT,
  status       TEXT DEFAULT 'indexed' CHECK (status IN ('indexed','pending','failed')),
  mime_type    TEXT,
  storage_path TEXT,
  checksum     TEXT,
  metadata     JSONB DEFAULT '{}'::JSONB,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Backfill-safe additive changes for existing projects
ALTER TABLE public.knowledge_docs
  ADD COLUMN IF NOT EXISTS mime_type TEXT,
  ADD COLUMN IF NOT EXISTS storage_path TEXT,
  ADD COLUMN IF NOT EXISTS checksum TEXT,
  ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::JSONB,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- ── Agent ↔ Knowledge Mapping ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.agent_knowledge_docs (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  agent_id     UUID REFERENCES public.agents(id) ON DELETE CASCADE NOT NULL,
  doc_id       UUID REFERENCES public.knowledge_docs(id) ON DELETE CASCADE NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(agent_id, doc_id)
);

-- ── Conversation Messages ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.conversation_messages (
  id             UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id        UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  conversation_id UUID REFERENCES public.conversations(id) ON DELETE CASCADE NOT NULL,
  agent_id       UUID REFERENCES public.agents(id) ON DELETE SET NULL,
  role           TEXT NOT NULL CHECK (role IN ('system','agent','user','tool')),
  content        TEXT NOT NULL,
  metadata       JSONB DEFAULT '{}'::JSONB,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── Tools ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tools (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  agent_id     UUID REFERENCES public.agents(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  description  TEXT,
  method       TEXT DEFAULT 'GET',
  endpoint     TEXT,
  status       TEXT DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── Stats snapshots (for analytics charts) ─────────────────
CREATE TABLE IF NOT EXISTS public.daily_stats (
  id           UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id      UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  date         DATE NOT NULL,
  total_calls  INTEGER DEFAULT 0,
  resolved     INTEGER DEFAULT 0,
  escalated    INTEGER DEFAULT 0,
  missed       INTEGER DEFAULT 0,
  avg_duration_sec INTEGER DEFAULT 0,
  total_cost_inr NUMERIC(10,2) DEFAULT 0,
  UNIQUE(user_id, date)
);

-- ════════════════════════════════════════════════════════════
--  ROW LEVEL SECURITY — users can only see their own data
-- ════════════════════════════════════════════════════════════
ALTER TABLE public.profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agents         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phone_numbers  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_knowledge_docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tools          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_stats    ENABLE ROW LEVEL SECURITY;

-- Profiles
CREATE POLICY "Users can view own profile"   ON public.profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "Users can update own profile" ON public.profiles FOR UPDATE USING (auth.uid() = id);

-- Agents
CREATE POLICY "Users CRUD own agents" ON public.agents FOR ALL USING (auth.uid() = user_id);

-- Conversations
CREATE POLICY "Users CRUD own convs" ON public.conversations FOR ALL USING (auth.uid() = user_id);

-- Phone numbers
CREATE POLICY "Users CRUD own numbers" ON public.phone_numbers FOR ALL USING (auth.uid() = user_id);

-- Knowledge docs
CREATE POLICY "Users CRUD own docs" ON public.knowledge_docs FOR ALL USING (auth.uid() = user_id);

-- Agent ↔ knowledge docs
CREATE POLICY "Users CRUD own agent docs" ON public.agent_knowledge_docs FOR ALL USING (auth.uid() = user_id);

-- Conversation messages
CREATE POLICY "Users CRUD own conv messages" ON public.conversation_messages FOR ALL USING (auth.uid() = user_id);

-- Tools
CREATE POLICY "Users CRUD own tools" ON public.tools FOR ALL USING (auth.uid() = user_id);

-- Daily stats
CREATE POLICY "Users view own stats" ON public.daily_stats FOR ALL USING (auth.uid() = user_id);

-- ════════════════════════════════════════════════════════════
--  INDEXES for common query patterns
-- ════════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_agents_user_created_at
  ON public.agents(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_user_created_at
  ON public.conversations(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_agent_created_at
  ON public.conversations(agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_docs_user_created_at
  ON public.knowledge_docs(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_knowledge_docs_agent
  ON public.agent_knowledge_docs(agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_knowledge_docs_doc
  ON public.agent_knowledge_docs(doc_id);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_created_at
  ON public.conversation_messages(conversation_id, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_created_at
  ON public.conversation_messages(user_id, created_at DESC);

-- ════════════════════════════════════════════════════════════
--  ENABLE REALTIME on key tables
-- ════════════════════════════════════════════════════════════
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'conversations'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.conversations;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'agents'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.agents;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'daily_stats'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.daily_stats;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'knowledge_docs'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.knowledge_docs;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'conversation_messages'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.conversation_messages;
  END IF;
END $$;

-- ════════════════════════════════════════════════════════════
--  SEED FUNCTION — auto-seed default agents for new users
-- ════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.seed_user_data(p_user_id UUID)
RETURNS void AS $$
DECLARE
  agent1_id UUID;
  agent2_id UUID;
BEGIN
  -- Only seed if no data exists
  IF (SELECT COUNT(*) FROM public.agents WHERE user_id = p_user_id) > 0 THEN RETURN; END IF;

  -- Create default agents
  INSERT INTO public.agents (user_id, name, description, system_prompt, first_message, voice_name, status, icon, template, call_count)
  VALUES (
    p_user_id,
    'Customer Support Agent',
    'Handles inbound support calls, returns and FAQs',
    'You are Priya, a friendly and helpful customer support AI agent. Help customers with order status, returns, and product queries. Always be warm, professional, and concise.',
    'Namaste! I''m Priya from customer support. How can I help you today?',
    'Priya', 'published', '🎧', 'customer_support', 1847
  ) RETURNING id INTO agent1_id;

  INSERT INTO public.agents (user_id, name, description, system_prompt, first_message, voice_name, status, icon, template, call_count)
  VALUES (
    p_user_id,
    'Sales Outreach Agent',
    'Qualifies leads and books demos via outbound calls',
    'You are Rahul, a professional sales agent. Your goal is to qualify leads and schedule product demos. Be confident, concise and respectful of the prospect''s time.',
    'Hello! I''m Rahul calling from Sahaiy. Is this a good time to speak for 2 minutes?',
    'Rahul', 'published', '📈', 'sales', 2000
  ) RETURNING id INTO agent2_id;

  -- Seed some conversations
  INSERT INTO public.conversations (user_id, agent_id, agent_name, caller_name, caller_number, duration_sec, status, csat_score, created_at)
  VALUES
    (p_user_id, agent1_id, 'Customer Support Agent', 'Rahul Sharma',  '+91 98765 43210', 108, 'resolved',  5, NOW() - INTERVAL '10 minutes'),
    (p_user_id, agent1_id, 'Customer Support Agent', 'Ananya Patel',  '+91 87654 32109', 132, 'resolved',  5, NOW() - INTERVAL '22 minutes'),
    (p_user_id, agent2_id, 'Sales Outreach Agent',   'Vikram Nair',   '+91 76543 21098', 185, 'escalated', 3, NOW() - INTERVAL '35 minutes'),
    (p_user_id, agent1_id, 'Customer Support Agent', 'Priti Desai',   '+91 65432 10987', 55,  'resolved',  5, NOW() - INTERVAL '50 minutes'),
    (p_user_id, agent2_id, 'Sales Outreach Agent',   'Arjun Reddy',   '+91 54321 09876', 262, 'resolved',  4, NOW() - INTERVAL '70 minutes'),
    (p_user_id, agent1_id, 'Customer Support Agent', 'Sunita Gupta',  '+91 43210 98765', 0,   'missed',    NULL, NOW() - INTERVAL '85 minutes'),
    (p_user_id, agent2_id, 'Sales Outreach Agent',   'Manish Kumar',  '+91 32109 87654', 170, 'resolved',  4, NOW() - INTERVAL '19 hours');

  -- Seed a phone number
  INSERT INTO public.phone_numbers (user_id, agent_id, number, country, city, capabilities, call_count)
  VALUES (p_user_id, agent1_id, '+91 80 4590 7823', 'India', 'Mumbai', ARRAY['inbound','outbound'], 847);

  -- Seed knowledge docs
  INSERT INTO public.knowledge_docs (user_id, name, type, size_bytes, url, status)
  VALUES
    (p_user_id, 'Customer FAQ — Returns & Refunds.pdf', 'file', 819200,  NULL, 'indexed'),
    (p_user_id, 'Product Catalog Q1 2025.docx',          'file', 1258291, NULL, 'indexed'),
    (p_user_id, 'https://help.mystore.in/shipping-policy', 'url', NULL, 'https://help.mystore.in/shipping-policy', 'indexed');

  INSERT INTO public.agent_knowledge_docs (user_id, agent_id, doc_id)
  SELECT p_user_id, agent1_id, d.id
  FROM public.knowledge_docs d
  WHERE d.user_id = p_user_id
    AND d.name IN ('Customer FAQ — Returns & Refunds.pdf', 'https://help.mystore.in/shipping-policy')
  ON CONFLICT (agent_id, doc_id) DO NOTHING;

  INSERT INTO public.agent_knowledge_docs (user_id, agent_id, doc_id)
  SELECT p_user_id, agent2_id, d.id
  FROM public.knowledge_docs d
  WHERE d.user_id = p_user_id
    AND d.name = 'Product Catalog Q1 2025.docx'
  ON CONFLICT (agent_id, doc_id) DO NOTHING;

  -- Seed a tool
  INSERT INTO public.tools (user_id, agent_id, name, description, method, endpoint, status)
  VALUES (p_user_id, agent1_id, 'Get Order Status', 'Fetches order status by ID', 'GET', 'https://api.mystore.in/orders/{orderId}', 'active');

  -- Seed 30 days of stats
  INSERT INTO public.daily_stats (user_id, date, total_calls, resolved, escalated, missed, avg_duration_sec, total_cost_inr)
  SELECT
    p_user_id,
    CURRENT_DATE - s.i,
    (180 + random() * 420)::INTEGER,
    (160 + random() * 380)::INTEGER,
    (5  + random() * 25)::INTEGER,
    (3  + random() * 15)::INTEGER,
    (90 + random() * 120)::INTEGER,
    (40 + random() * 120)::NUMERIC(10,2)
  FROM generate_series(0, 29) AS s(i);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
