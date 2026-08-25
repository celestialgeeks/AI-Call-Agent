"""
app/routers/knowledge.py
─────────────────────────
Knowledge Base REST endpoints — document ingestion for RAG.

POST /knowledge/ingest   → ingest raw text into FAISS index
GET  /knowledge/status   → return per-user index stats
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.errors import ApiError, new_request_id
from app.services import rag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])


class IngestRequest(BaseModel):
    # Ignored when AUTH_ENFORCED=true — identity is derived from the token.
    user_id: Optional[str] = None
    doc_id: str
    text: str
    name: Optional[str] = None


@router.post("/ingest")
async def ingest_doc(
    request: Request,
    body: IngestRequest,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Ingest a document's text content into the RAG FAISS index.
    Safe to call multiple times — duplicate doc_ids are silently skipped.
    Call this after a new knowledge_doc is created in Supabase.
    """
    owner_id = current_user_id or body.user_id
    if not owner_id:
        raise ApiError(400, "missing_user", "user_id is required.")
    if not body.text.strip():
        raise ApiError(400, "empty_text", "text must not be empty.")

    try:
        await rag.ingest_doc(owner_id, body.doc_id, body.text)
        logger.info("[Knowledge] Ingested doc %s for user %s", body.doc_id, owner_id)
        return {"ok": True, "doc_id": body.doc_id}
    except ApiError:
        raise
    except Exception as exc:
        rid = getattr(request.state, "request_id", new_request_id())
        logger.error("[Knowledge] ingest error (request_id=%s): %s", rid, exc)
        raise ApiError(500, "internal_error", "Failed to ingest document.") from exc


@router.get("/status")
async def index_status(user_id: str):
    """Return how many documents are indexed for a user."""
    count = len(rag._doc_ids.get(user_id, []))
    return {"user_id": user_id, "indexed_docs": count, "rag_available": rag._RAG_AVAILABLE}
