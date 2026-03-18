"""
server.py — Sahaiy Backend Entry Point
Run with: uvicorn server:app --reload --port 8000
Or:        python server.py
"""

import uvicorn
from app.main import app  # noqa: F401 — re-exported for uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )