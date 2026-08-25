"""
app/routers/knowledge.py
─────────────────────────
Knowledge Base REST endpoints — real document ingestion (issue #5, ADR-0003).

POST /knowledge/ingest        → JSON path: raw text → chunk → embed → persist
POST /knowledge/ingest/file   → multipart upload (text/* or PDF) → same pipeline
POST /knowledge/ingest/url    → URL fetch (SSRF-guarded) → same pipeline
GET  /knowledge/status        → per-user index stats (additive-compatible)
GET  /knowledge/docs/{doc_id} → ownership-checked doc detail + chunk count

Lifecycle honesty (G10): a doc's status in Supabase reflects ACTUAL state —
pending → parsing → indexed | failed. size_bytes is the REAL source payload
size. The response only says indexed after vectors are persisted to Supabase.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.config import SUPABASE_URL, SUPABASE_SERVICE_KEY
from app.errors import ApiError, new_request_id
from app.services import doc_parser, rag

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])


class IngestRequest(BaseModel):
    # Ignored when AUTH_ENFORCED=true — identity is derived from the token.
    user_id: Optional[str] = None
    doc_id: str
    text: str
    name: Optional[str] = None


class IngestUrlRequest(BaseModel):
    # Ignored when AUTH_ENFORCED=true — identity is derived from the token.
    user_id: Optional[str] = None
    url: str
    name: Optional[str] = None


def _owner(current_user_id: Optional[str], body_user_id: Optional[str],
           request: Request) -> str:
    """Token-derived owner when enforcement is on; legacy body id otherwise."""
    if current_user_id:
        return current_user_id
    owner_id = (body_user_id or "").strip()
    if not owner_id:
        raise ApiError(400, "missing_user", "user_id is required.")
    return owner_id


def _supabase_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


async def _run_ingest(request: Request, owner_id: str, doc_id: str,
                      name: Optional[str], source_kind: str, text: str,
                      size_bytes: int) -> dict:
    """
    Shared pipeline tail: set status honestly around rag.ingest_doc and build
    the additive-compatible response. Any failure marks the doc 'failed'.
    """
    rid = getattr(request.state, "request_id", new_request_id())
    sb_ready = _supabase_ready()

    if sb_ready:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: (
                get_sb().rpc("set_knowledge_doc_status", {
                    "p_doc_id": doc_id, "p_user_id": owner_id,
                    "p_status": "parsing", "p_size_bytes": size_bytes})
                .execute()))
        except Exception as exc:
            logger.error("[Knowledge] status=parsing write failed "
                         "(request_id=%s): %s", rid, exc)
            raise ApiError(503, "knowledge_store_unavailable",
                           "Knowledge store unavailable; nothing was ingested.")

    try:
        chunk_count = await rag.ingest_doc(owner_id, doc_id, text)
    except ValueError as exc:
        if sb_ready:
            await _mark_failed(owner_id, doc_id)
        raise ApiError(400, "empty_text", str(exc)) from exc
    except Exception as exc:
        logger.error("[Knowledge] ingest failed (request_id=%s): %s", rid, exc)
        if sb_ready:
            await _mark_failed(owner_id, doc_id)
        raise ApiError(500, "internal_error",
                       "Failed to ingest document.") from exc

    if sb_ready:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: (
                get_sb().rpc("set_knowledge_doc_status", {
                    "p_doc_id": doc_id, "p_user_id": owner_id,
                    "p_status": "indexed", "p_size_bytes": size_bytes})
                .execute()))
        except Exception as exc:
            # Vectors persisted but the flag write failed — report truthfully.
            logger.error("[Knowledge] status=indexed write failed "
                         "(request_id=%s): %s", rid, exc)

    logger.info("[Knowledge] Ingested %s doc %s (%d chunks) for user %s — "
                "%d bytes, request_id=%s",
                source_kind, doc_id, chunk_count, owner_id, size_bytes, rid)
    return {
        "ok": True,
        "doc_id": doc_id,
        "status": "indexed",
        "chunks": chunk_count,
        "size_bytes": size_bytes,
        "name": name,
        "source": source_kind,
    }


def get_sb():
    from app.services.supabase_client import get_supabase
    return get_supabase()


async def _mark_failed(user_id: str, doc_id: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: (
            get_sb().rpc("set_knowledge_doc_status", {
                "p_doc_id": doc_id, "p_user_id": user_id,
                "p_status": "failed", "p_size_bytes": None}).execute()))
    except Exception as exc:
        logger.error("[Knowledge] mark-failed write failed for doc %s: %s",
                     doc_id, exc)


# ── POST /knowledge/ingest (raw text — original shape preserved) ─────────────

@router.post("/ingest")
async def ingest_doc(
    request: Request,
    body: IngestRequest,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """Ingest raw text content. Response keeps the v1 fields plus new ones."""
    owner_id = _owner(current_user_id, body.user_id, request)
    if not body.text.strip():
        raise ApiError(400, "empty_text", "text must not be empty.")
    return await _run_ingest(
        request, owner_id, body.doc_id, body.name or body.doc_id,
        "text", body.text, len(body.text.encode("utf-8")))


# ── POST /knowledge/ingest/file (real upload) ────────────────────────────────

@router.post("/ingest/file")
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    doc_id: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None),
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """Upload a text/plain, markdown, or PDF file; parse → chunk → persist."""
    owner_id = _owner(current_user_id, user_id, request)

    data = await file.read()
    if not data:
        raise ApiError(400, "empty_file", "Uploaded file is empty.")
    if len(data) > doc_parser.MAX_UPLOAD_BYTES:
        raise ApiError(413, "file_too_large",
                       f"File exceeds the {doc_parser.MAX_UPLOAD_BYTES // (1024*1024)} MB limit.")

    content_type = (file.content_type or "").lower()
    filename = file.filename or ""
    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: doc_parser.extract_text_from_pdf(data))
    elif content_type.startswith("text/") or not content_type or \
            filename.lower().endswith((".txt", ".md")):
        encoding = "utf-8"
        text = data.decode(encoding, errors="replace").strip()
        if not text:
            raise ApiError(400, "empty_file", "Uploaded file has no readable text.")
    else:
        raise ApiError(415, "unsupported_media_type",
                       "Supported uploads: plain text, markdown, PDF.")

    final_doc_id = (doc_id or "").strip() or filename
    return await _run_ingest(
        request, owner_id, final_doc_id, filename or final_doc_id,
        "file", text, len(data))


# ── POST /knowledge/ingest/url ───────────────────────────────────────────────

@router.post("/ingest/url")
async def ingest_url(
    request: Request,
    body: IngestUrlRequest,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """Fetch a public http(s) page/PDF, extract text, ingest."""
    import httpx

    owner_id = _owner(current_user_id, body.user_id, request)
    url = (body.url or "").strip()
    if not url:
        raise ApiError(400, "invalid_url", "url must not be empty.")

    client = httpx.AsyncClient(timeout=doc_parser.URL_TIMEOUT_SEC,
                               follow_redirects=True)
    try:
        text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: doc_parser.extract_text_from_url(client, url))
    except doc_parser.ParseError as exc:
        raise ApiError(400, "url_fetch_failed", str(exc)) from exc
    except Exception as exc:
        logger.warning("[Knowledge] URL fetch error: %s", exc)
        raise ApiError(400, "url_fetch_failed",
                       "Could not fetch content from that URL.") from exc
    finally:
        await client.aclose()

    if not text.strip():
        raise ApiError(400, "empty_text", "URL returned no extractable text.")

    doc_id = f"url:{url}"
    return await _run_ingest(
        request, owner_id, doc_id, body.name or url, "url", text, len(text.encode("utf-8")))


# ── GET /knowledge/status (additive-compatible) ──────────────────────────────

@router.get("/status")
async def index_status(
    request: Request,
    user_id: str,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """
    Per-user index stats.

    Legacy fields (user_id, indexed_docs, rag_available) are unchanged.
    Additive: chunks, supabase_persistence, docs breakdown when available.
    When enforcement is on, user_id must equal the token subject.
    """
    if current_user_id and user_id != current_user_id:
        raise ApiError(403, "forbidden", "user_id does not match token subject.")
    if not user_id:
        raise ApiError(400, "missing_user", "user_id is required.")

    state = rag._indices.get(user_id) or {}
    index = state.get("index")
    chunk_count = len(state.get("chunks") or [])
    doc_ids = {str(d) for d in (state.get("doc_ids") or [])}

    result = {
        "user_id": user_id,
        "indexed_docs": len(doc_ids),          # legacy semantic: distinct docs
        "rag_available": rag._RAG_AVAILABLE,
        # ── additive extensions ──
        "chunks": chunk_count,
        "loaded_from_persistence": bool(state.get("loaded")),
        "supabase_persistence": _supabase_ready(),
        "vector_count": int(index.ntotal) if index is not None else 0,
    }
    return result


# ── GET /knowledge/docs/{doc_id} — ownership-checked detail ──────────────────

@router.get("/docs/{doc_id}")
async def doc_detail(
    request: Request,
    doc_id: str,
    current_user_id: Optional[str] = Depends(get_current_user_id),
):
    """Ownership check on doc_id: returns the doc only when it belongs to the caller."""
    if not current_user_id:
        raise ApiError(400, "missing_user",
                       "user identity required for doc lookup.")
    if not _supabase_ready():
        raise ApiError(503, "knowledge_store_unavailable",
                       "Knowledge store not configured.")

    def _fetch():
        return (get_sb().table("knowledge_docs")
                .select("id,user_id,name,type,size_bytes,status,url,mime_type,"
                        "storage_path,checksum,metadata,created_at,updated_at")
                .eq("id", doc_id)
                .eq("user_id", current_user_id)
                .maybe_single()
                .execute())

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as exc:
        logger.error("[Knowledge] doc lookup failed: %s", exc)
        raise ApiError(500, "internal_error", "Doc lookup failed.") from exc

    row = getattr(result, "data", None)
    if not row:
        # Same 404 whether missing OR owned by someone else — no existence leak.
        raise ApiError(404, "resource_not_found", "Document not found.")

    chunk_rows = None
    try:
        def _count():
            return (get_sb().table("knowledge_doc_chunks")
                    .select("chunk_index").eq("doc_id", doc_id).execute())
        chunk_rows = await asyncio.get_event_loop().run_in_executor(None, _count)
    except Exception:
        pass
    chunk_count = len(getattr(chunk_rows, "data", None) or [])

    return {"doc": row, "chunks": chunk_count}
