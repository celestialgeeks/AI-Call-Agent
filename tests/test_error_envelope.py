"""
tests/test_error_envelope.py
────────────────────────────
SEC-03: every error response uses the uniform envelope
    {"error": {"code", "message", "request_id"}}

No raw str(exc), stack traces, or SQL ever reach the client.
NOTE: adapted to main's auth convention (#21) — unauthenticated requests
get a 401 envelope before validation runs, so the 422 cases send X-User-Id.
"""
import pytest


@pytest.fixture()
def envelope_client(client):
    return client


def test_validation_error_is_enveloped(envelope_client):
    # Missing required multipart file on /stt/transcribe → 422 in the envelope.
    resp = envelope_client.post("/stt/transcribe", headers={"X-User-Id": "u1"})
    assert resp.status_code == 422
    body = resp.json()
    err = body["error"]
    assert set(err) >= {"code", "message", "request_id"}
    assert err["code"] == "validation_error"
    assert isinstance(err["request_id"], str) and err["request_id"]


def test_http_exception_converted_to_envelope(envelope_client):
    # Unknown route → 404 StarletteHTTPException, converted to envelope.
    resp = envelope_client.get("/definitely-not-a-route")
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "not_found"
    assert "request_id" in err


def test_request_id_header_matches_body(envelope_client):
    resp = envelope_client.get("/definitely-not-a-route")
    rid_header = resp.headers.get("x-request-id")
    rid_body = resp.json()["error"]["request_id"]
    assert rid_header and rid_header == rid_body


def test_no_raw_exception_text_in_404(envelope_client):
    resp = envelope_client.get("/definitely-not-a-route")
    text = resp.text.lower()
    for leak in ("traceback", "exception:", "sqlalchemy", "postgres"):
        assert leak not in text


def test_empty_file_is_enveloped_400(envelope_client):
    # Empty upload → 400 via ApiError in the uniform envelope (SEC-03 on STT).
    resp = envelope_client.post(
        "/stt/transcribe",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "empty_file"
    assert set(err) >= {"code", "message", "request_id"}
