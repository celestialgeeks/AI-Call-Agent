"""
sahaiy-backend/migrations/0008_knowledge_ingest.sql
────────────────────────────────────────────────────
Issue #5 — real /knowledge/ingest: upload → parse → chunk → Supabase-backed
vector persistence (ADR-0003: HF Spaces disk is ephemeral; Supabase Postgres is
the system of record).

Additive only:
  - knowledge_doc_chunks: one row per embedded chunk (bytea-serialised FAISS
    vector + text + ordinal). Rebuilt into the in-memory per-user FAISS index on
    boot / first access.
  - knowledge_docs.status gains 'parsing' + 'failed' vocabulary so the
    pending → indexed lifecycle reflects ACTUAL ingestion state.
  - knowledge_docs.size_bytes semantics: real byte size of the source payload.

No existing column is dropped or retyped. knowledge_docs CHECK constraint is
replaced with a superset (old rows stay valid).
"""

-- ── 1. Chunk store ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.knowledge_doc_chunks (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  doc_id      UUID REFERENCES public.knowledge_docs(id) ON DELETE CASCADE NOT NULL,
  user_id     UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  chunk_index INTEGER NOT NULL,
  content     TEXT NOT NULL,
  embedding   BYTEA NOT NULL,             -- float32 little-endian, dim known via faiss metadata
  dim         INTEGER NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(doc_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_kdc_user ON public.knowledge_doc_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_kdc_doc  ON public.knowledge_doc_chunks(doc_id);

ALTER TABLE public.knowledge_doc_chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users CRUD own doc chunks" ON public.knowledge_doc_chunks
  FOR ALL USING (auth.uid() = user_id);

-- ── 2. Status vocabulary: real lifecycle pending → indexed (+ parsing/failed)
ALTER TABLE public.knowledge_docs DROP CONSTRAINT IF EXISTS knowledge_docs_status_check;
ALTER TABLE public.knowledge_docs ADD CONSTRAINT knowledge_docs_status_check
  CHECK (status IN ('pending', 'parsing', 'indexed', 'failed'));

-- Backfill honesty fix: docs stuck at 'indexed' with zero chunks never actually
-- ingested. Mark them pending so the dashboard stops lying.
UPDATE public.knowledge_docs kd
   SET status = 'pending'
 WHERE kd.status = 'indexed'
   AND NOT EXISTS (SELECT 1 FROM public.knowledge_doc_chunks c WHERE c.doc_id = kd.id);

-- ── 3. RPCs used by the ingest service ──────────────────────────────────────

-- Replace all chunks for a doc atomically (re-ingest safe).
CREATE OR REPLACE FUNCTION public.replace_knowledge_chunks(
  p_doc_id UUID, p_user_id UUID, p_chunks JSONB, p_embeddings BYTEA, p_dim INTEGER)
RETURNS INTEGER AS $$
DECLARE
  inserted INTEGER;
BEGIN
  DELETE FROM public.knowledge_doc_chunks WHERE doc_id = p_doc_id;
  INSERT INTO public.knowledge_doc_chunks(doc_id, user_id, chunk_index, content, embedding, dim)
  SELECT p_doc_id, p_user_id,
         (chunk->>'index')::INTEGER,
         chunk->>'content',
         decode(chunk->>'embedding_b64', 'base64'),
         p_dim
    FROM jsonb_array_elements(p_chunks) AS chunk;
  GET DIAGNOSTICS inserted = ROW_COUNT;
  RETURN inserted;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Ownership-scoped status update (service role calls it; user_id guard keeps it
-- safe if ever exposed).
CREATE OR REPLACE FUNCTION public.set_knowledge_doc_status(
  p_doc_id UUID, p_user_id UUID, p_status TEXT, p_size_bytes INTEGER DEFAULT NULL)
RETURNS VOID AS $$
BEGIN
  UPDATE public.knowledge_docs
     SET status = p_status,
         size_bytes = COALESCE(p_size_bytes, size_bytes),
         updated_at = NOW()
   WHERE id = p_doc_id AND user_id = p_user_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
