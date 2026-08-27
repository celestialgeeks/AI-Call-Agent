"""
Smoke test: boot the real app with RAG enabled and a mocked Supabase, then
exercise POST /knowledge/ingest (text + file) and GET /knowledge/status.
Run:  /opt/anaconda3/bin/python scripts/smoke_knowledge_ingest.py
"""
import io
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "sahaiy-backend")
sys.path.insert(0, ".")

import os
os.environ.setdefault("AUTH_ENFORCED", "false")

from tests.test_knowledge_ingest import ChunkStoreFake, make_sb_with_store

store = ChunkStoreFake()


class FakeModel:
    def encode(self, texts):
        import numpy as np
        return np.array([[float(len(t) % 17), float(sum(map(ord, t)) % 97),
                          float(i)] for i, t in enumerate(texts)], dtype="float32")


def main():
    with patch("app.services.supabase_client.create_client",
               side_effect=lambda *a, **k: make_sb_with_store(store)), \
         patch("app.services.rag._model", FakeModel()), \
         patch("app.services.rag._get_supabase",
               return_value=make_sb_with_store(store)):
        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as tc:
            print("== health ==")
            r = tc.get("/health")
            print(r.status_code, r.json())

            print("\n== POST /knowledge/ingest (text) ==")
            text = "Sahaiy handles inbound and outbound AI voice calls in Hindi and English. " * 30
            r = tc.post("/knowledge/ingest", json={
                "user_id": "smoke-user", "doc_id": "smoke-doc-1", "text": text})
            print(r.status_code, r.json())
            assert r.status_code == 200
            body = r.json()
            assert body["size_bytes"] == len(text.encode()), "size_bytes must be REAL"
            assert body["status"] == "indexed"

            print("\n== POST /knowledge/ingest/file (txt upload) ==")
            content = b"Uploaded pricing sheet: Starter Rs 999/mo, Growth Rs 2999/mo. " * 40
            r = tc.post("/knowledge/ingest/file",
                        files={"file": ("pricing.txt", content, "text/plain")},
                        data={"user_id": "smoke-user"})
            print(r.status_code, r.json())
            assert r.status_code == 200
            assert r.json()["size_bytes"] == len(content)

            print("\n== GET /knowledge/status ==")
            r = tc.get("/knowledge/status", params={"user_id": "smoke-user"})
            print(r.status_code, r.json())

            print("\n== retrieval across 'restart' (fresh index from store) ==")
    import asyncio
    import importlib
    import app.services.rag as rag
    importlib.reload(rag)
    with patch.object(rag, "_get_supabase",
                      return_value=make_sb_with_store(store)), \
         patch.object(rag, "_model", FakeModel()):
        ctx = asyncio.run(
            rag.retrieve_context("smoke-user", "pricing starter growth"))
        print("context:", repr(ctx[:120]))
        assert ctx, "SEC-05: context must survive restart"

    print("\nSMOKE OK — real size_bytes, honest indexed status, restart-survivable.")


if __name__ == "__main__":
    main()