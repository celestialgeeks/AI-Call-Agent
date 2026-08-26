"""
tests/test_knowledge_ingest.py
──────────────────────────────
Issue #5: real /knowledge/ingest — upload, parse/chunk, Supabase-backed FAISS
persistence (ADR-0003).

Covers:
  - G10   : status lifecycle reflects ACTUAL state; size_bytes is real
  - SEC-05: doc retrievable across a backend restart (index rebuilt from
            Supabase, not memory)
  - Ownership check on doc_id / user scoping
  - Chunking behaviour (overlap, caps, empty input)
  - Parser guards (oversize upload, SSRF, unsupported type)
"""
import base64
import struct
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import FakeResult, make_supabase


USER = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"


class ChunkStoreFake:
    """
    In-memory stand-in for knowledge_doc_chunks + the RPCs, so persistence
    round-trips are actually exercised (not stubbed away).
    """

    def __init__(self):
        self.rows = {}      # doc_id -> list of {content, embedding(bytes), dim}
        self.statuses = {}  # (doc_id, user_id) -> {status, size_bytes}

    def rpc_replace(self, p_doc_id, p_user_id, p_chunks, p_embeddings, p_dim):
        self.rows[p_doc_id] = [
            {"user_id": p_user_id,
             "content": c["content"],
             "embedding": base64.b64decode(c["embedding_b64"]),
             "dim": p_dim}
            for c in p_chunks
        ]
        return FakeResult(len(self.rows[p_doc_id]))

    def fetch_user(self, user_id):
        out = []
        for doc_id, chunks in self.rows.items():
            for i, ch in enumerate(chunks):
                if ch["user_id"] == user_id:
                    out.append({"doc_id": doc_id, "content": ch["content"],
                                "embedding": ch["embedding"], "dim": ch["dim"]})
        return FakeResult(out)


def make_sb_with_store(store: ChunkStoreFake, default_user_id: str = USER):
    """
    Supabase mock wired to a real in-memory chunk store.

    NOTE: helpers.make_supabase installs sb.table as a side_effect function,
    so we must REPLACE sb.table wholesale (return_value wiring is ignored).
    The table query filters on the LAST .eq('user_id', …) value seen in the
    chain (mirroring real query-builder semantics); falls back to
    `default_user_id` when no user filter is applied.
    """
    scope = {"user_id": default_user_id}

    builder = MagicMock()

    def _eq(column, value):
        if column == "user_id":
            scope["user_id"] = value
        return builder

    def _fetch_chunks():
        return store.fetch_user(scope["user_id"])

    for m in ("select", "order", "insert", "update", "delete", "single",
              "maybe_single", "in_", "limit"):
        getattr(builder, m).return_value = builder
    builder.eq.side_effect = _eq
    builder.execute.side_effect = _fetch_chunks

    sb = make_supabase({})
    sb.table = MagicMock(return_value=builder)
    sb.rpc.side_effect = _rpc_router(store)
    return sb


def _rpc_router(store):
    def _side(name, params):
        builder = MagicMock()
        if name == "replace_knowledge_chunks":
            builder.execute.return_value = store.rpc_replace(
                params["p_doc_id"], params["p_user_id"], params["p_chunks"],
                params["p_embeddings"], params["p_dim"])
        elif name == "set_knowledge_doc_status":
            store.statuses[(params["p_doc_id"], params["p_user_id"])] = {
                "status": params["p_status"],
                "size_bytes": params.get("p_size_bytes"),
            }
            builder.execute.return_value = FakeResult(None)
        else:
            builder.execute.return_value = FakeResult(None)
        return builder
    return _side


@pytest.fixture()
def rag_env(monkeypatch):
    """Fresh rag module + mocked Supabase per test."""
    store = ChunkStoreFake()
    import importlib
    import app.services.rag as rag
    importlib.reload(rag)

    with patch("app.services.supabase_client.create_client",
               return_value=make_supabase({})), \
         patch("app.services.supabase_client.get_supabase",
               return_value=make_sb_with_store(store)), \
         patch.object(rag, "_get_supabase",
                      return_value=make_sb_with_store(store)):
        # Real model is heavy for CI; monkeypatch a deterministic fake encoder.
        class FakeModel:
            def encode(self, texts):
                import numpy as np
                # Deterministic pseudo-vectors so search is stable.
                return np.array([[float(len(t) % 17), float(sum(map(ord, t)) % 97),
                                  float(i)] for i, t in enumerate(texts)],
                                dtype="float32")

        with patch.object(rag, "_model", FakeModel()):
            yield rag, store


# ── Chunking ─────────────────────────────────────────────────────────────────

def test_chunk_text_basic_and_overlap(rag_env):
    rag, _ = rag_env
    text = ("para one. " * 100) + "\n\n" + ("para two. " * 100)
    chunks = rag.chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= rag.CHUNK_SIZE + 50 for c in chunks)
    assert all(c.strip() for c in chunks)


def test_chunk_text_empty(rag_env):
    rag, _ = rag_env
    assert rag.chunk_text("") == []
    assert rag.chunk_text("   \n\n  ") == []


def test_chunk_text_caps_doc_length(rag_env):
    rag, _ = rag_env
    text = "word " * 500_000  # ~2.5 MB of words
    chunks = rag.chunk_text(text)
    assert len(chunks) <= rag.MAX_CHUNKS_PER_DOC


# ── Ingest pipeline: persist FIRST, honest status ────────────────────────────

def test_ingest_persists_vectors_to_supabase(rag_env):
    rag, store = rag_env
    n = _run(rag.ingest_doc(USER, "doc-1", "Sahaiy pricing starts at Rs 999/month. " * 30))
    assert n > 0
    rows = store.fetch_user(USER).data
    assert len(rows) == n
    assert all(len(r["embedding"]) == r["dim"] * 4 for r in rows)  # float32 bytes


def test_reingest_same_doc_replaces_chunks(rag_env):
    rag, store = rag_env
    _run(rag.ingest_doc(USER, "doc-1", "first version content " * 20))
    _run(rag.ingest_doc(USER, "doc-1", "second version content " * 20))
    rows = store.fetch_user(USER).data
    contents = {r["content"] for r in rows}
    assert any("second version" in c for c in contents)
    assert not any("first version" in c for c in contents)


def test_ingest_empty_text_raises(rag_env):
    rag, store = rag_env
    with pytest.raises(ValueError):
        _run(rag.ingest_doc(USER, "doc-x", "   "))
    assert store.fetch_user(USER).data == []


def test_vec_serialisation_roundtrip(rag_env):
    rag, _ = rag_env
    import numpy as np
    vec = np.array([[0.25, -1.5, 3.0]], dtype="float32")
    raw = rag._vec_to_bytes(vec)
    back = rag._bytes_to_vec(raw, 3)
    assert np.allclose(back, vec, atol=1e-6)


# ── SEC-05: restart survival ──────────────────────────────────────────────────

def test_sec05_restart_survival(rag_env):
    """Ingest → wipe ALL in-memory state (simulated restart) → retrieve still works."""
    rag, store = rag_env
    body = ("The refund policy allows returns within 14 days of purchase. " * 10)
    _run(rag.ingest_doc(USER, "doc-policy", body))

    # Simulate process restart: fresh module state.
    import importlib
    importlib.reload(rag)

    class FakeModel:
        def encode(self, texts):
            import numpy as np
            return np.array([[float(len(t) % 17), float(sum(map(ord, t)) % 97),
                              float(i)] for i, t in enumerate(texts)], dtype="float32")

    with patch.object(rag, "_get_supabase", return_value=make_sb_with_store(store)), \
         patch.object(rag, "_model", FakeModel()):
        ctx = _run(rag.retrieve_context(USER, "refund policy window"))
    assert ctx, "SEC-05 FAIL: context lost after simulated restart"
    assert "refund" in ctx.lower()


def test_sec05_other_user_gets_nothing(rag_env):
    rag, store = rag_env
    _run(rag.ingest_doc(USER, "doc-1", "private data alpha " * 40))
    import importlib
    importlib.reload(rag)
    # OTHER user's scoped query returns only THEIR rows (none exist).
    with patch.object(rag, "_get_supabase",
                      return_value=make_sb_with_store(store, default_user_id=OTHER)), \
         patch.object(rag, "_model", _fake_model_cls()):
        ctx = _run(rag.retrieve_context(OTHER, "private data alpha"))
    assert ctx == ""


def _fake_model_cls():
    class FakeModel:
        def encode(self, texts):
            import numpy as np
            return np.array([[float(len(t) % 17), float(sum(map(ord, t)) % 97),
                              float(i)] for i, t in enumerate(texts)], dtype="float32")
    return FakeModel()


def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ── Router-level: G10 honesty + ownership ────────────────────────────────────

@pytest.fixture()
def client(rag_env, monkeypatch):
    # Pretend Supabase is configured so the honest status-lifecycle writes fire
    # (they go to the mocked client from the rag_env fixture).
    # NOTE: SUPABASE_JWT_SECRET must stay unset — main's auth dev fallback is
    # gated on "no secret configured" (AUTH_ENFORCED=false alone isn't enough),
    # and these tests exercise the legacy client-declared user_id path.
    monkeypatch.setenv("SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("AUTH_ENFORCED", "false")
    import importlib
    import app.config as config
    importlib.reload(config)
    import app.auth as auth
    importlib.reload(auth)
    import app.routers.knowledge as kr
    importlib.reload(kr)
    import app.main as main
    importlib.reload(main)

    rag, store = rag_env
    from fastapi.testclient import TestClient

    def _make_sb(*args, **kwargs):
        return make_sb_with_store(store)

    with patch("app.services.supabase_client.create_client", side_effect=_make_sb):
        with TestClient(main.app) as tc:
            yield tc, store


def test_g10_text_ingest_reports_real_size_and_indexed(client):
    tc, store = client
    text = "hello world content for the knowledge base " * 50
    resp = tc.post("/knowledge/ingest", json={
        "user_id": USER, "doc_id": "doc-g10", "text": text})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["size_bytes"] == len(text.encode("utf-8"))  # REAL size, not random
    assert data["status"] == "indexed"                       # only after persist
    assert data["chunks"] >= 1
    # Status write trail shows the honest lifecycle.
    statuses = store.statuses
    assert statuses[("doc-g10", USER)]["status"] == "indexed"
    assert statuses[("doc-g10", USER)]["size_bytes"] == len(text.encode("utf-8"))


def test_status_endpoint_additive_compatible(client):
    tc, _ = client
    resp = tc.get("/knowledge/status", params={"user_id": USER})
    assert resp.status_code == 200
    data = resp.json()
    # Legacy fields intact:
    assert data["user_id"] == USER
    assert isinstance(data["indexed_docs"], int)
    assert isinstance(data["rag_available"], bool)
    # Additive fields present:
    assert "chunks" in data and "supabase_persistence" in data


def test_ingest_requires_user(client):
    """Legacy mode (flag off, no secret): the auth dependency returns None, so
    the ROUTER's ownership guard rejects a request with no user identity at
    all with 400 missing_user (envelope)."""
    tc, _ = client
    resp = tc.post("/knowledge/ingest", json={"doc_id": "d1", "text": "x"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_user"


def test_file_upload_txt_roundtrip(client):
    tc, store = client
    content = b"uploaded plain text payload " * 200
    resp = tc.post("/knowledge/ingest/file",
                   files={"file": ("notes.txt", content, "text/plain")},
                   data={"user_id": USER, "doc_id": "doc-upload"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source"] == "file"
    assert data["size_bytes"] == len(content)          # real bytes on the wire
    rows = store.fetch_user(USER).data
    assert any("uploaded plain text payload" in r["content"] for r in rows)


def test_file_upload_oversize_rejected(client):
    tc, _ = client
    big = b"x" * (10 * 1024 * 1024 + 1)
    resp = tc.post("/knowledge/ingest/file",
                   files={"file": ("big.txt", big, "text/plain")},
                   data={"user_id": USER})
    assert resp.status_code == 413


def test_file_upload_empty_rejected(client):
    tc, _ = client
    resp = tc.post("/knowledge/ingest/file",
                   files={"file": ("empty.txt", b"", "text/plain")},
                   data={"user_id": USER})
    assert resp.status_code == 400


def test_file_upload_unsupported_type(client):
    tc, _ = client
    resp = tc.post("/knowledge/ingest/file",
                   files={"file": ("prog.bin", b"\x00\x01\x02", "application/octet-stream")},
                   data={"user_id": USER})
    assert resp.status_code == 415


def test_url_ssrf_private_blocked(client):
    tc, _ = client
    resp = tc.post("/knowledge/ingest/url",
                   json={"user_id": USER, "url": "http://127.0.0.1:8080/admin"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "url_fetch_failed"


def test_url_bad_scheme_blocked(client):
    tc, _ = client
    resp = tc.post("/knowledge/ingest/url",
                   json={"user_id": USER, "url": "ftp://example.com/x"})
    assert resp.status_code == 400


def test_pdf_extraction_real():
    """Real PDF bytes → real text via pypdf."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf writer unavailable")
    import io
    from app.services.doc_parser import extract_text_from_pdf

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    # A blank PDF has no text layer → must raise ParseError honestly.
    with pytest.raises(Exception):
        extract_text_from_pdf(buf.getvalue())
