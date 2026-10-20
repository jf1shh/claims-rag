"""Regression tests for the 2026-10-20 low-severity hardening pass.

Each test class corresponds to one numbered finding fixed alongside it.
Postgres-backed behavior (findings 5 and 6) is covered with fake
connection/cursor objects rather than a real database, matching the rest of
this module's unit style (`tests/test_postgres_store.py` covers the real-DSN
integration path and skips without one).
"""

import base64
import logging
import os
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.authn import AuthenticationError, OIDCAuthenticator, ServiceAccountAuthenticator
from backend.document_limits import DocumentLimitError, check_container, parse_bounded


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


# --- Finding 1: non-ASCII API key must not raise TypeError ------------------


def test_given_non_ascii_api_key_when_authenticating_then_denied_not_500():
    authenticator = ServiceAccountAuthenticator(
        {"sa_real_key": {"subject": "worker", "tenant_id": "tenant-a", "roles": ["admin"]}}
    )
    with pytest.raises(AuthenticationError, match="invalid API key"):
        authenticator.authenticate(_FakeRequest({"X-API-Key": "café"}))


# --- Finding 2: zip expansion cap must not reject a photo-heavy DOCX --------


def _write_zip(path, entries):
    """entries: list of (name, data, compress_type)."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data, compress_type in entries:
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=compress_type)


def test_given_photo_heavy_docx_when_checked_then_container_passes(tmp_path):
    path = tmp_path / "report.docx"
    xml = b"<xml>" + b"content" * 100 + b"</xml>"
    media = os.urandom(12_000_000)  # random bytes don't compress away
    _write_zip(path, [
        ("word/document.xml", xml, zipfile.ZIP_DEFLATED),
        ("word/media/image1.jpg", media, zipfile.ZIP_STORED),
    ])
    # max_chars*4 only needs to cover the xml; max_bytes covers both the
    # physical file and (*10) the total declared size.
    check_container(path, max_bytes=20_000_000, max_chars=100_000)


def test_given_text_parts_over_cap_when_checked_then_rejected(tmp_path):
    path = tmp_path / "bomb.docx"
    xml = b"x" * 5000
    _write_zip(path, [("word/document.xml", xml, zipfile.ZIP_DEFLATED)])
    with pytest.raises(DocumentLimitError, match="expanded size limit"):
        check_container(path, max_bytes=10_000_000, max_chars=1000)


def test_given_total_expansion_over_cap_when_checked_then_rejected(tmp_path):
    path = tmp_path / "bomb.docx"
    # Zeros compress enormously well, so the file on disk stays tiny while
    # the declared (uncompressed) media size is a genuine zip-bomb shape.
    zeros = b"\x00" * 25_000_000
    _write_zip(path, [("word/media/huge.bin", zeros, zipfile.ZIP_DEFLATED)])
    with pytest.raises(DocumentLimitError, match="expanded size limit"):
        check_container(path, max_bytes=2_000_000, max_chars=10_000_000)


# --- CR-SEC-FIX-C.2: media exemption needs its own budget --------------------


def test_given_media_over_its_own_budget_when_checked_then_rejected(tmp_path):
    path = tmp_path / "bomb.docx"
    # Zeros compress away to a tiny file on disk (clearing the byte-limit and
    # *10 overall caps), but the declared size still exceeds the new
    # media-only budget (max_bytes * 4) for a recognized media extension.
    media = b"\x00" * 9_000_000
    _write_zip(path, [("word/media/image1.jpg", media, zipfile.ZIP_DEFLATED)])
    with pytest.raises(DocumentLimitError, match="expanded size limit"):
        check_container(path, max_bytes=2_000_000, max_chars=100_000)


def test_given_renamed_text_part_under_media_path_when_checked_then_still_counts_as_text(tmp_path):
    path = tmp_path / "bomb.docx"
    # word/document.xml disguised under /media/ with a non-media extension:
    # must still count against the text cap, not get the media exemption.
    disguised = b"x" * 5000
    _write_zip(path, [("word/media/document.dat", disguised, zipfile.ZIP_DEFLATED)])
    with pytest.raises(DocumentLimitError, match="expanded size limit"):
        check_container(path, max_bytes=10_000_000, max_chars=1000)


# --- Finding 3: .xls dropped from the upload allowlist ----------------------


def test_given_xls_upload_when_posted_then_rejected(isolated_api_runtime):
    from backend.app import app

    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("x.xls", b"not a real xls", "application/vnd.ms-excel")},
    )
    assert response.status_code == 400


# --- Finding 4: staged S3 blobs get the correct content type ----------------


class _FakeBlobStore:
    def __init__(self):
        self.calls = []

    def put(self, key, content, content_type):
        self.calls.append((key, content, content_type))


class _FakeStore:
    def __init__(self, blob_store):
        self.blob_store = blob_store


def test_given_pdf_when_staged_then_content_type_is_pdf():
    from backend.source_storage import stage_source

    blob = _FakeBlobStore()
    key, version = stage_source(_FakeStore(blob), "report.pdf", "pdf", "hello world")
    assert blob.calls[0][2] == "application/pdf"


def test_given_unknown_extension_when_staged_then_content_type_defaults_to_octet_stream():
    from backend.source_storage import stage_source

    blob = _FakeBlobStore()
    stage_source(_FakeStore(blob), "weird.bin", "bin", "hello world")
    assert blob.calls[0][2] == "application/octet-stream"


# --- Finding 5 & 6: Postgres store fakes -------------------------------------


class _FakeCursor:
    def __init__(self, rows=None, raises=None):
        self._rows = rows or []
        self._raises = raises

    def fetchall(self):
        if self._raises:
            raise self._raises
        return self._rows

    def fetchone(self):
        if self._raises:
            raise self._raises
        return self._rows[0] if self._rows else None


class _FakeTransaction:
    """Mimics psycopg's `conn.transaction()` savepoint context manager: an
    exception inside the block propagates out, but does not leave the fake
    connection in any special poisoned state (which is exactly the bug this
    finding fixes relative to a bare un-savepointed statement)."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePostgresConn:
    def __init__(self, vector_raises=None, fts_raises=None, vector_rows=None, meta_rows=None, fts_rows=None):
        self.vector_raises = vector_raises
        self.fts_raises = fts_raises
        self.vector_rows = vector_rows or []
        self.meta_rows = meta_rows or []
        self.fts_rows = fts_rows or []
        self.closed = False

    def transaction(self):
        return _FakeTransaction()

    def execute(self, sql, params=None):
        if "FROM child_chunks c" in sql:
            return _FakeCursor(self.vector_rows, raises=self.vector_raises)
        if "p.id = ANY" in sql:
            return _FakeCursor(self.meta_rows)
        if "websearch_to_tsquery" in sql:
            return _FakeCursor(self.fts_rows, raises=self.fts_raises)
        raise AssertionError(f"unexpected query: {sql}")

    def close(self):
        self.closed = True


def _store(monkeypatch, conn):
    from backend.postgres_store import PostgresVectorStore

    store = PostgresVectorStore.__new__(PostgresVectorStore)
    store.dsn = "postgresql://fake"
    store.tenant_id = "tenant-a"
    store.storage_dir = "/tmp/unused"
    store.embedding_dimensions = 384
    store.blob_store = None
    store.db_path = "postgresql://fake"
    monkeypatch.setattr(store, "_connect", lambda: conn)
    return store


def test_given_vector_leg_fails_when_searching_then_keyword_leg_still_used(monkeypatch, caplog):
    conn = _FakePostgresConn(
        vector_raises=RuntimeError("boom"),
        fts_rows=[(1, "content", "report.pdf", "pdf", 7, "sha256:abc")],
    )
    store = _store(monkeypatch, conn)
    with caplog.at_level(logging.WARNING):
        results = store.search_similarity(query_embedding=[0.0] * 384, query_text="glass")
    assert len(results) == 1
    assert results[0]["filename"] == "report.pdf"
    assert any("RuntimeError" in message for message in caplog.messages)


def test_given_keyword_leg_fails_when_searching_then_vector_leg_still_used(monkeypatch, caplog):
    conn = _FakePostgresConn(
        fts_raises=RuntimeError("boom"),
        vector_rows=[(1, 0.9)],
        meta_rows=[(1, "content", "report.pdf", "pdf", 7, "sha256:abc")],
    )
    store = _store(monkeypatch, conn)
    with caplog.at_level(logging.WARNING):
        results = store.search_similarity(query_embedding=[0.0] * 384, query_text="glass")
    assert len(results) == 1
    assert results[0]["filename"] == "report.pdf"
    assert any("RuntimeError" in message for message in caplog.messages)


def test_given_same_filename_in_another_scope_when_adding_document_then_only_own_scope_deleted(monkeypatch, tmp_path):
    """A document's identity is (claim_id, filename): adding "report.pdf" to the
    global scope while claim-b already holds a same-named file must not touch
    claim-b's row -- only the caller's own (claim_id, filename) row is deleted
    before the fresh insert."""
    from backend.postgres_store import PostgresVectorStore

    class _FakeEmbeddingEngine:
        def embed_chunks(self, chunks):
            return [[0.0] * 384 for _ in chunks]

    class _ScopedConn:
        def __init__(self):
            self.delete_params = []

        def execute(self, sql, params=None):
            stripped = sql.strip()
            if stripped.startswith("DELETE FROM documents"):
                self.delete_params.append(params)
                return _FakeCursor([])
            if stripped.startswith("INSERT INTO documents"):
                return _FakeCursor([(42,)])
            if stripped.startswith("INSERT INTO parent_chunks"):
                return _FakeCursor([(1,)])
            if stripped.startswith("INSERT INTO child_chunks"):
                return _FakeCursor([])
            if stripped.startswith("UPDATE documents SET storage_key"):
                return _FakeCursor([])
            raise AssertionError(f"unexpected query: {sql}")

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    store = PostgresVectorStore.__new__(PostgresVectorStore)
    store.dsn = "postgresql://fake"
    store.tenant_id = "tenant-a"
    store.storage_dir = str(tmp_path)
    store.embedding_dimensions = 384
    store.blob_store = None
    store.db_path = "postgresql://fake"
    conn = _ScopedConn()
    monkeypatch.setattr(store, "_connect", lambda: conn)

    doc_id, _ = store.add_document("report.pdf", "pdf", 10, "text", _FakeEmbeddingEngine(), claim_id=None)

    assert doc_id == 42
    # Exactly one scoped delete, matching this call's own (tenant, filename,
    # claim_id) scope -- never a delete reaching into claim-b's row.
    assert conn.delete_params == [("tenant-a", "report.pdf", None)]


# --- Finding 7: JWKS fetch failure must not lock users out ------------------


def _make_keypair(kid):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64(value):
        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
        "n": _b64(public_numbers.n), "e": _b64(public_numbers.e),
    }
    return private_key, jwk


ISSUER = "https://idp.example.com"
AUDIENCE = "claimsrag-app"


def _mint(private_key, kid):
    payload = {
        "sub": "user-42", "tenant_id": "tenant-a", "roles": ["adjuster"],
        "iss": ISSUER, "aud": AUDIENCE, "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def test_given_jwks_fetch_fails_after_ttl_when_authenticating_then_stale_keys_still_serve():
    private_key, jwk = _make_keypair("key-1")
    token = _mint(private_key, "key-1")
    clock_state = {"now": 0.0}
    calls = {"n": 0}

    def fetcher(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"keys": [jwk]}
        raise RuntimeError("network down")

    authenticator = OIDCAuthenticator(
        issuer=ISSUER, client_id=AUDIENCE, jwks_url=f"{ISSUER}/jwks.json",
        cache_ttl_seconds=300, jwks_fetcher=fetcher, clock=lambda: clock_state["now"],
    )
    request = _FakeRequest({"Authorization": f"Bearer {token}"})

    principal = authenticator.authenticate(request)
    assert principal.subject == "user-42"
    assert calls["n"] == 1

    clock_state["now"] = 301.0
    principal = authenticator.authenticate(request)
    assert principal.subject == "user-42"
    assert calls["n"] == 2  # refetch attempted and failed, but cache still served


def test_given_unknown_kid_when_authenticating_then_single_refetch_and_backoff():
    private_key_a, jwk_a = _make_keypair("key-a")
    private_key_b, jwk_b = _make_keypair("key-b")
    token_b = _mint(private_key_b, "key-b")
    clock_state = {"now": 0.0}
    fetch_calls = []

    def fetcher(url):
        fetch_calls.append(url)
        # First call (initial cache fill) only knows about key-a; the
        # key-b rotation is only visible once a refetch is triggered.
        if len(fetch_calls) == 1:
            return {"keys": [jwk_a]}
        return {"keys": [jwk_a, jwk_b]}

    authenticator = OIDCAuthenticator(
        issuer=ISSUER, client_id=AUDIENCE, jwks_url=f"{ISSUER}/jwks.json",
        cache_ttl_seconds=300, jwks_fetcher=fetcher, clock=lambda: clock_state["now"],
    )
    request = _FakeRequest({"Authorization": f"Bearer {token_b}"})

    principal = authenticator.authenticate(request)
    assert principal.subject == "user-42"
    assert len(fetch_calls) == 2  # initial fill + exactly one kid-triggered refetch

    # A second, still-unknown kid within the 60s window must not refetch again.
    other_token = _mint(private_key_a, "key-c")
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        authenticator.authenticate(_FakeRequest({"Authorization": f"Bearer {other_token}"}))
    assert len(fetch_calls) == 2


# --- Finding 8: parser subprocess gets a minimal environment ----------------


def test_given_secret_in_parent_env_when_parsing_then_subprocess_env_excludes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "secret")
    captured = {}

    def fake_run(args, cwd, timeout, check, stdout, stderr, env):
        captured["env"] = env
        Path(args[5]).write_text("parsed text", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    path = tmp_path / "doc.docx"
    path.write_bytes(b"not a real docx but container check only looks at zip-ness")

    parse_bounded(path, "docx", max_bytes=10_000_000, max_chars=1_000_000, timeout=30)

    assert "LLM_API_KEY" not in captured["env"]
    assert captured["env"]["PATH"] == os.environ["PATH"]
