"""
app/services/rag.py
────────────────────
Retrieval-Augmented Generation (RAG) using FAISS + sentence-transformers.

ADR-0003: the app host's disk is EPHEMERAL (HF Spaces). Supabase Postgres is
the system of record for vectors:

    ingest_doc(user_id, doc_id, text)   → chunk → embed → FAISS + Supabase bytea
    retrieve_context(user_id, query)    → top-k chunks from the in-memory index
    ensure_user_loaded(user_id)         → rebuild in-memory FAISS from Supabase

The per-user in-memory FAISS index is a CACHE; on boot / first touch it is
rebuilt from knowledge_doc_chunks. SEC-05 proves a document stays retrievable
across a backend restart.
"""

import asyncio
import logging
import struct
import base64
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports so the app starts without these heavy deps if RAG is unused
try:
    import numpy as np
    import faiss
    from sentence_transformers import SentenceTransformer
    _RAG_AVAILABLE = True
except ImportError:
    _RAG_AVAILABLE = False
    logger.warning(
        "[RAG] faiss-cpu or sentence-transformers not installed — RAG disabled. "
        "Run: pip install faiss-cpu sentence-transformers"
    )

_MODEL_NAME = "all-MiniLM-L6-v2"
_model: Optional[object] = None

# ── Chunking constants ────────────────────────────────────────────────────────
CHUNK_SIZE = 1200          # characters per chunk (~300 tokens for MiniLM)
CHUNK_OVERLAP = 150        # character overlap so sentences aren't cut mid-thought
MAX_CHUNKS_PER_DOC = 500   # hard cap — protects the DB from pathological input
MAX_TEXT_CHARS = 2_000_000  # ~2 MB of text per doc, enforced before chunking

# user_id -> {
#   "index": faiss.IndexFlatL2,
#   "chunks": list[str],              parallel to FAISS rows (all docs merged)
#   "doc_ids": list[uuid.UUID|str],   doc owning each FAISS row
#   "loaded": bool,                   rebuilt from Supabase at least once
# }
_indices: dict = {}
_lock = threading.Lock()

_supabase = None


def _get_model():
    global _model
    if _model is None and _RAG_AVAILABLE:
        logger.info("[RAG] Loading sentence-transformer model '%s' …", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("[RAG] Model loaded.")
    return _model


def _get_supabase():
    """Service-role client; imported lazily so tests can mock it."""
    global _supabase
    if _supabase is None:
        from app.services.supabase_client import get_supabase
        _supabase = get_supabase()
    return _supabase


def chunk_text(text: str) -> list[str]:
    """
    Split text into overlapping character-window chunks.

    Paragraph-first strategy: accumulate paragraphs up to CHUNK_SIZE; a single
    oversized paragraph is split on sentence boundaries; last resort is a hard
    window split with overlap. Never returns empty chunks.
    """
    text = (text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""

    def _flush():
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    def _split_long(block: str) -> list[str]:
        # Sentence-boundary split first, then hard windows with overlap.
        parts, cur = [], ""
        for sentence in block.replace("!.", "!.").split(". "):
            piece = sentence if not cur else cur + ". " + sentence
            if len(piece) <= CHUNK_SIZE:
                cur = piece
                continue
            if cur:
                parts.append(cur)
            cur = sentence[:CHUNK_SIZE]
            while len(sentence) > CHUNK_SIZE:
                parts.append(sentence[:CHUNK_SIZE])
                sentence = sentence[CHUNK_SIZE - CHUNK_OVERLAP:]
            cur = sentence
        if cur:
            parts.append(cur)
        return parts

    for para in paragraphs:
        if len(para) > CHUNK_SIZE:
            _flush()
            chunks.extend(_split_long(para))
            continue
        if len(buf) + len(para) + 2 > CHUNK_SIZE and buf:
            _flush()
        buf = f"{buf}\n\n{para}" if buf else para
    _flush()

    return chunks[:MAX_CHUNKS_PER_DOC]


def _vec_to_bytes(vec: "np.ndarray") -> bytes:
    """float32 little-endian serialisation of one embedding row."""
    return struct.pack(f"<{vec.size}f", *vec.astype("float32").ravel().tolist())


def _bytes_to_vec(raw: bytes, dim: int) -> "np.ndarray":
    return np.frombuffer(raw, dtype="<f4").astype("float32").reshape(1, dim)


# ── Persistence layer (Supabase) ─────────────────────────────────────────────

async def _persist_chunks(user_id: str, doc_id: str, chunks: list[str],
                          vectors: "np.ndarray", dim: int) -> None:
    """Write all chunks + embeddings for one doc via RPC (atomic replace)."""
    payload = [
        {"index": i, "content": c, "embedding_b64": base64.b64encode(
            _vec_to_bytes(vectors[i])).decode("ascii")}
        for i, c in enumerate(chunks)
    ]
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: (
        _get_supabase()
        .rpc("replace_knowledge_chunks",
             {"p_doc_id": doc_id, "p_user_id": user_id,
              "p_chunks": payload, "p_embeddings": b"", "p_dim": dim})
        .execute()
    ))


async def _load_user_from_supabase(user_id: str) -> bool:
    """
    Rebuild the in-memory FAISS index for a user from knowledge_doc_chunks.

    Returns True when the index holds at least one vector afterwards.
    Degrades to an empty cache when RAG deps are missing or Supabase fails —
    retrieval then simply returns no context instead of crashing calls.
    """
    if not _RAG_AVAILABLE:
        return False

    def _fetch():
        return (_get_supabase().table("knowledge_doc_chunks")
                .select("doc_id,content,embedding,dim")
                .eq("user_id", user_id)
                .order("id")
                .execute())

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        logger.error("[RAG] Supabase load failed for user %s: %s", user_id, exc)
        return False

    rows = getattr(result, "data", None) or []
    with _lock:
        state = _indices.setdefault(user_id, {"chunks": [], "doc_ids": [], "loaded": False})
        index = state.get("index")
        chunks, doc_ids = state["chunks"], state["doc_ids"]

    new_rows = []
    for row in rows:
        try:
            dim = int(row["dim"])
            vec = _bytes_to_vec(row["embedding"], dim)
        except Exception:
            continue
        new_rows.append((row["doc_id"], row["content"], vec, dim))

    with _lock:
        if new_rows:
            dim = new_rows[0][3]
            if index is None:
                index = faiss.IndexFlatL2(dim)
                state["index"] = index
            matrix = np.vstack([r[2] for r in new_rows])
            index.add(matrix)
            chunks.extend(r[1] for r in new_rows)
            doc_ids.extend(r[0] for r in new_rows)
        state["loaded"] = True

    logger.info("[RAG] Rebuilt index for user %s from Supabase (%d vectors)",
                user_id, len(new_rows))
    return index is not None and index.ntotal > 0


async def ensure_user_loaded(user_id: str) -> None:
    """Load a user's persisted vectors once per process lifetime."""
    state = _indices.get(user_id)
    if state and state.get("loaded"):
        return
    async with asyncio.Lock():
        state = _indices.get(user_id)
        if state and state.get("loaded"):
            return
        await _load_user_from_supabase(user_id)


# ── Public API (used by audio_ws.py) ─────────────────────────────────────────

async def ingest_doc(user_id: str, doc_id: str, text: str) -> int:
    """
    Chunk → embed → add to the per-user FAISS index AND persist to Supabase.

    Idempotent per doc: re-ingesting a doc_id replaces its previous chunks.
    Returns the number of chunks ingested. Raises on persistence failure so the
    router can mark the doc 'failed' honestly.
    """
    if not _RAG_AVAILABLE:
        raise RuntimeError("RAG dependencies not installed")

    text = (text or "").strip()
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        logger.warning("[RAG] doc %s truncated to %d chars", doc_id, MAX_TEXT_CHARS)

    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("text produced no chunks")

    loop = asyncio.get_event_loop()
    model = _get_model()
    vectors = await loop.run_in_executor(None, lambda: np.array(
        model.encode(chunks), dtype="float32"))
    dim = vectors.shape[1]

    # Persist FIRST (system of record); only then update the in-memory cache.
    await _persist_chunks(user_id, doc_id, chunks, vectors, dim)

    with _lock:
        state = _indices.setdefault(
            user_id, {"chunks": [], "doc_ids": [], "loaded": True})
        state["loaded"] = True
        index = state.get("index")
        if index is None:
            index = faiss.IndexFlatL2(dim)
            state["index"] = index
        # Re-ingest of the same doc: drop its old rows so ids stay aligned.
        keep = [i for i, d in enumerate(state["doc_ids"]) if d != doc_id]
        if len(keep) != len(state["doc_ids"]) or (state["doc_ids"] and doc_id in state["doc_ids"]):
            _rebuild_index_locked(state, keep, dim)
        state["index"].add(vectors)
        state["chunks"].extend(chunks)
        state["doc_ids"].extend([doc_id] * len(chunks))

    logger.info("[RAG] Ingested doc %s for user %s (%d chunks)",
                doc_id, user_id, len(chunks))
    return len(chunks)


def _rebuild_index_locked(state: dict, keep_rows: list[int], dim: int) -> None:
    """Caller holds _lock. Compact the index to the kept rows."""
    if not keep_rows:
        state["index"] = faiss.IndexFlatL2(dim)
        state["chunks"], state["doc_ids"] = [], []
        return
    kept_vectors = np.vstack([
        state["index"].reconstruct(i).reshape(1, -1) for i in keep_rows])
    state["index"] = faiss.IndexFlatL2(dim)
    state["index"].add(kept_vectors)
    state["chunks"] = [state["chunks"][i] for i in keep_rows]
    state["doc_ids"] = [state["doc_ids"][i] for i in keep_rows]


async def remove_doc(user_id: str, doc_id: str) -> None:
    """Drop a doc's rows from memory AND Supabase."""
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: (
            _get_supabase().table("knowledge_doc_chunks")
            .delete().eq("doc_id", doc_id).eq("user_id", user_id).execute()))
    except Exception as exc:
        logger.error("[RAG] Supabase delete failed for doc %s: %s", doc_id, exc)

    with _lock:
        state = _indices.get(user_id)
        if not state or not state.get("index"):
            return
        keep = [i for i, d in enumerate(state["doc_ids"]) if d != doc_id]
        if len(keep) == len(state["doc_ids"]):
            return
        _rebuild_index_locked(state, keep, state["index"].d)


async def retrieve_context(user_id: str, query: str, top_k: int = 3) -> str:
    """
    Retrieve the top-k most relevant chunks for a query.

    Lazily rebuilds the index from Supabase on first access after a restart —
    this is what makes SEC-05 (restart survival) pass.
    """
    if not _RAG_AVAILABLE or not user_id:
        return ""

    await ensure_user_loaded(user_id)

    with _lock:
        state = _indices.get(user_id)
        index = state.get("index") if state else None
        n_docs = index.ntotal if index else 0
        if n_docs == 0:
            return ""
        k = min(top_k, n_docs)

    loop = asyncio.get_event_loop()
    model = _get_model()
    q_arr = np.array(await loop.run_in_executor(None, lambda: model.encode([query])),
                     dtype="float32")
    _, hit = index.search(q_arr, k)

    with _lock:
        snippets = [state["chunks"][i] for i in hit[0] if 0 <= i < len(state["chunks"])]
    return "\n---\n".join(snippets)


def clear_user_index(user_id: str) -> None:
    """Remove the cached index for a user (e.g., on sign-out). Supabase rows stay."""
    _indices.pop(user_id, None)
