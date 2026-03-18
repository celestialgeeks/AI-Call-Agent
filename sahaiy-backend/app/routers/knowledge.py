"""
app/routers/knowledge.py
─────────────────────────
Knowledge Base REST endpoints — document ingestion for RAG.

POST /knowledge/ingest   → ingest raw text into FAISS index
GET  /knowledge/status   → return per-user index stats
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import rag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])


class IngestRequest(BaseModel):
    user_id: str
    doc_id: str
    text: str
    name: Optional[str] = None


@router.post("/ingest")
async def ingest_doc(body: IngestRequest):
    """
    Ingest a document's text content into the RAG FAISS index.
    Safe to call multiple times — duplicate doc_ids are silently skipped.
    Call this after a new knowledge_doc is created in Supabase.
    """
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        await rag.ingest_doc(body.user_id, body.doc_id, body.text)
        logger.info("[Knowledge] Ingested doc %s for user %s", body.doc_id, body.user_id)
        return {"ok": True, "doc_id": body.doc_id}
    except Exception as exc:
        logger.error("[Knowledge] ingest error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/status")
async def index_status(user_id: str):
    """Return how many documents are indexed for a user."""
    count = len(rag._doc_ids.get(user_id, []))
    return {"user_id": user_id, "indexed_docs": count, "rag_available": rag._RAG_AVAILABLE}
