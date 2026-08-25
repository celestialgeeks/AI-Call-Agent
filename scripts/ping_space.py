"""Keep-alive ping for the sahaiy-backend HF Space (ADR-0002/0003 keep-alive ruling).

Run as a GitHub Actions cron step or locally. Pings /health so the free CPU Basic
Space doesn't hit the 48h idle sleep.

Usage: python3 scripts/ping_space.py [URL]
Env override: SAHAIY_BACKEND_URL
"""
import json
import os
import sys
import urllib.request

DEFAULT_URL = os.environ.get(
    "SAHAIY_BACKEND_URL",
    "https://shreyashsingh-sahaiy-backend.hf.space",
).rstrip("/")


def main() -> int:
    url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_URL
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=30) as resp:
            body = json.load(resp)
            print(f"OK {resp.status} {url}/health → {body}")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"WARN ping failed for {url}/health: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
