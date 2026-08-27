"""
app/services/doc_parser.py
──────────────────────────
Source → text extraction for /knowledge/ingest (issue #5).

Supported sources:
  - raw/plain text   (text/* or empty content type)
  - PDF              (pypdf, text layer only)
  - URL              (httpx fetch → HTML stripped to readable text)

Hard limits enforced BEFORE any parse work:
  MAX_UPLOAD_BYTES  — multipart body cap
  MAX_TEXT_CHARS    — extracted text cap
URL fetches are SSRF-guarded: only http/https, no private/loopback hosts.
"""

import io
import ipaddress
import logging
from socket import gaierror
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader
    _PDF_AVAILABLE = True
except ImportError:
    _PDF_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

MAX_UPLOAD_BYTES = 10 * 1024 * 1024      # 10 MB
MAX_URL_FETCH_BYTES = 5 * 1024 * 1024    # 5 MB of response body
MAX_TEXT_CHARS = 2_000_000               # matches rag.MAX_TEXT_CHARS
URL_TIMEOUT_SEC = 15.0

_ALLOWED_SCHEMES = {"http", "https"}


class ParseError(Exception):
    """Raised for unsupported/invalid sources; message is client-safe."""


def extract_text_from_pdf(data: bytes) -> str:
    """Extract the text layer of a PDF. Raises ParseError on scanned/empty PDFs."""
    if not _PDF_AVAILABLE:
        raise ParseError("PDF parsing unavailable — pypdf not installed.")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
        text = "\n\n".join(pages).strip()
    except Exception as exc:
        logger.warning("[DocParser] PDF parse failure: %s", exc)
        raise ParseError("Could not parse PDF file.") from exc
    if not text:
        raise ParseError(
            "PDF has no extractable text layer (scanned/image PDFs are not supported).")
    return text


def extract_text_from_html(html: str) -> str:
    """Strip scripts/styles/tags down to readable text."""
    if not _BS4_AVAILABLE:
        raise ParseError("HTML parsing unavailable — beautifulsoup4 not installed.")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _assert_public_url(url: str) -> None:
    """SSRF guard: scheme allow-list + reject private/loopback/link-local IPs."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ParseError("Only http(s) URLs are supported.")

    host = parsed.hostname or ""
    try:
        infos = __import__("socket").getaddrinfo(host, None)
    except gaierror as exc:
        raise ParseError("Could not resolve URL host.") from exc
    except Exception as exc:
        raise ParseError("Invalid URL host.") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise ParseError("Refusing to fetch private/internal URLs.")


def extract_text_from_url(client: httpx.AsyncClient, url: str) -> str:
    """Blocking fetch+extract; run via run_in_executor from async callers."""

    def _work() -> str:
        _assert_public_url(url)
        resp = client.get(url, timeout=URL_TIMEOUT_SEC,
                          follow_redirects=True,
                          headers={"User-Agent": "SahaiyKnowledgeIngest/1.0"})
        if resp.status_code >= 400:
            raise ParseError(f"URL returned HTTP {resp.status_code}.")
        body = resp.content[:MAX_URL_FETCH_BYTES]
        content_type = (resp.headers.get("content-type") or "").lower()
        if "pdf" in content_type or body[:5] == b"%PDF-":
            return extract_text_from_pdf(body)
        encoding = resp.encoding or "utf-8"
        try:
            html = body.decode(encoding, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        if "html" in content_type or "<html" in html[:1000].lower():
            return extract_text_from_html(html)
        return html.strip()

    return _work()
