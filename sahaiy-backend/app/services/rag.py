"""
app/services/rag.py
────────────────────
Retrieval-Augmented Generation (RAG) using FAISS + sentence-transformers.
Per-user in-memory FAISS indices built from knowledge docs stored in Supabase.

Usage:
    await ingest_doc(user_id, doc_id, text)          # index one doc
    context = await retrieve_context(user_id, query)  # get top-k context
"""

import asyncio
import logging
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
_indices: dict = {}        # user_id -> faiss.IndexFlatL2
_doc_store: dict = {}      # user_id -> list[str]  (parallel to FAISS index)
_doc_ids: dict = {}        # user_id -> list[str]  (Supabase doc UUIDs)


def _get_model():
    global _model
    if _model is None and _RAG_AVAILABLE:
        logger.info("[RAG] Loading sentence-transformer model '%s' …", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("[RAG] Model loaded.")
    return _model


async def ingest_doc(user_id: str, doc_id: str, text: str) -> None:
    """
    Encode text and add it to the per-user FAISS index.
    Safe to call multiple times — duplicate doc_ids are skipped.
    """
    if not _RAG_AVAILABLE:
        return

    if user_id not in _doc_ids:
        _doc_ids[user_id] = []
        _doc_store[user_id] = []

    if doc_id in _doc_ids[user_id]:
        logger.debug("[RAG] doc %s already indexed for user %s", doc_id, user_id)
        return

    loop = asyncio.get_event_loop()
    model = _get_model()
    embedding = await loop.run_in_executor(None, lambda: model.encode([text]))
    vec = np.array(embedding, dtype="float32")

    if user_id not in _indices:
        dim = vec.shape[1]
        _indices[user_id] = faiss.IndexFlatL2(dim)

    _indices[user_id].add(vec)
    _doc_store[user_id].append(text)
    _doc_ids[user_id].append(doc_id)
    logger.debug("[RAG] Indexed doc %s for user %s (index size=%d)", doc_id, user_id, len(_doc_ids[user_id]))


async def retrieve_context(user_id: str, query: str, top_k: int = 3) -> str:
    """
    Retrieve the top-k most relevant document snippets for a query.

    Returns:
        Concatenated context string, or empty string if no index exists.
    """
    if not _RAG_AVAILABLE or user_id not in _indices:
        return ""

    loop = asyncio.get_event_loop()
    model = _get_model()
    q_vec = await loop.run_in_executor(None, lambda: model.encode([query]))
    q_arr = np.array(q_vec, dtype="float32")

    index = _indices[user_id]
    n_docs = len(_doc_store[user_id])
    k = min(top_k, n_docs)
    if k == 0:
        return ""

    _, indices = index.search(q_arr, k)
    snippets = [_doc_store[user_id][i] for i in indices[0] if i < n_docs]
    return "\n---\n".join(snippets)


def clear_user_index(user_id: str) -> None:
    """Remove all indexed documents for a user (e.g., on sign-out)."""
    _indices.pop(user_id, None)
    _doc_store.pop(user_id, None)
    _doc_ids.pop(user_id, None)
