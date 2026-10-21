"""API-level tests for M12c-E claim purge (enterprise audit finding #6):
DELETE /api/claims/{claim_id} deletes a claim's documents and job rows,
admin-only (claims:purge), refuses with 409 while an ingestion job is in
flight, and the audit event carries counts but never a filename."""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.app import runtime as app_module
from backend.app import app
from backend.audit import JsonlAuditSink
from backend.authn import OIDCAuthenticator
from backend.ingestion import IngestionJob, IngestionStatus
from backend.job_store import SqliteJobStore
from backend.rag_engine import SQLiteVectorStore
from backend.rbac import ClaimAccessPolicy

ISSUER = "https://idp.example.com"
AUDIENCE = "claimsrag-app"


class _FakeEmbedder:
    def _vec(self, text):
        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


def _make_keypair(kid="test-key-1"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64(value):
        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
                "n": _b64(public_numbers.n), "e": _b64(public_numbers.e),
            }
        ]
    }
    return private_key, jwks


def _token(private_key, *, subject, roles, tenant="tenant-a"):
    payload = {
        "sub": subject, "tenant_id": tenant, "roles": roles,
        "iss": ISSUER, "aud": AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key-1"})


@pytest.fixture
def harness(tmp_path, monkeypatch):
    store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"))
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    sink_path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(sink_path)

    private_key, jwks = _make_keypair()
    oidc = OIDCAuthenticator(
        issuer=ISSUER, client_id=AUDIENCE, jwks_url=f"{ISSUER}/.well-known/jwks.json",
        jwks_fetcher=lambda url: jwks, clock=lambda: 1000.0,
    )
    from dataclasses import replace
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, tenant_id="tenant-a"))
    monkeypatch.setattr(app_module, "vector_store", store)
    monkeypatch.setattr(app_module, "ingestion_job_store", job_store)
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(app_module, "_authenticator", oidc)
    monkeypatch.setattr(app_module, "_claim_access_policy", ClaimAccessPolicy())
    monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: _FakeEmbedder())

    def token(subject, roles):
        return _token(private_key, subject=subject, roles=roles)

    return TestClient(app), store, job_store, sink_path, token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _purge_path(claim_id: str) -> str:
    return f"/api/claims/{quote(claim_id, safe='')}"


def _events(sink_path):
    return [json.loads(line) for line in sink_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _add_job(job_store, claim_id, job_id="job_1", status=IngestionStatus.indexed, tenant_id="tenant-a"):
    now = datetime.now(timezone.utc)
    job_store.create(
        IngestionJob(
            job_id=job_id, tenant_id=tenant_id, document_id="doc_1", claim_id=claim_id,
            filename="report.pdf", checksum="sha256:x", status=status,
            created_at=now, updated_at=now,
        )
    )


def test_given_admin_when_purging_claim_then_200_with_counts(harness):
    client, store, job_store, sink_path, token = harness
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1")

    response = client.delete(_purge_path("claim-1"), headers=_auth(token("admin-1", ["admin"])))

    assert response.status_code == 200
    body = response.json()
    assert body == {"claim_id": "claim-1", "documents_deleted": 1, "jobs_deleted": 1}
    assert store.get_claim_documents("claim-1") == []

    purge_events = [e for e in _events(sink_path) if e["event"] == "claim_purge"]
    assert len(purge_events) == 1
    assert purge_events[0]["outcome"] == "purged"
    assert purge_events[0]["document_count"] == 1
    assert purge_events[0]["job_count"] == 1
    assert "filename" not in purge_events[0]


@pytest.mark.parametrize("roles", [["supervisor"], ["adjuster"], ["siu"]])
def test_given_non_admin_when_purging_claim_then_403(harness, roles):
    client, store, job_store, sink_path, token = harness
    response = client.delete(_purge_path("claim-1"), headers=_auth(token("user-1", roles)))
    assert response.status_code == 403
    denied = [e for e in _events(sink_path) if e["event"] == "claim_purge" and e["outcome"] == "denied"]
    assert len(denied) == 1


def test_given_active_job_when_purging_claim_then_409(harness):
    client, store, job_store, sink_path, token = harness
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1", status=IngestionStatus.queued)

    response = client.delete(_purge_path("claim-1"), headers=_auth(token("admin-1", ["admin"])))

    assert response.status_code == 409
    assert len(store.get_claim_documents("claim-1")) == 1
    conflict = [e for e in _events(sink_path) if e["event"] == "claim_purge" and e["outcome"] == "conflict"]
    assert len(conflict) == 1


def test_given_invalid_claim_id_when_purging_then_400(harness):
    client, store, job_store, sink_path, token = harness
    response = client.delete(_purge_path("a" * 129), headers=_auth(token("admin-1", ["admin"])))
    assert response.status_code == 400


def test_given_empty_claim_when_purged_then_idempotent_zeros(harness):
    client, store, job_store, sink_path, token = harness
    response = client.delete(_purge_path("claim-empty"), headers=_auth(token("admin-1", ["admin"])))
    assert response.status_code == 200
    assert response.json() == {"claim_id": "claim-empty", "documents_deleted": 0, "jobs_deleted": 0}


def test_given_purged_claim_when_old_job_fetched_then_404(harness):
    client, store, job_store, sink_path, token = harness
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1")

    response = client.delete(_purge_path("claim-1"), headers=_auth(token("admin-1", ["admin"])))
    assert response.status_code == 200

    job_response = client.get("/api/jobs/job_1", headers=_auth(token("admin-1", ["admin"])))
    assert job_response.status_code == 404


def _sync_delete_service(monkeypatch, job_store):
    from dataclasses import replace

    from backend.ingestion import IngestionService

    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, ingestion_mode="sync"))
    service = IngestionService(job_store=job_store)
    monkeypatch.setattr(app_module, "ingestion_service", service)
    return service


def test_given_sync_delete_when_job_fetched_then_identity_fields_scrubbed(harness, monkeypatch):
    """Finding #6: after POST /api/delete the job store can no longer confirm
    which file was uploaded -- the delete's own job row is scrubbed too."""
    client, store, job_store, sink_path, token = harness
    _sync_delete_service(monkeypatch, job_store)
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _add_job(job_store, "claim-1")
    headers = _auth(token("admin-1", ["admin"]))

    response = client.post("/api/delete", json={"filename": "report.pdf", "claim_id": "claim-1"}, headers=headers)
    assert response.status_code == 200
    delete_job_id = response.json()["job_id"]

    for job_id in ("job_1", delete_job_id):
        body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        assert body["filename"] == "[deleted]"
        assert body.get("checksum") is None


def test_given_scrub_failure_when_sync_delete_then_still_200(harness, monkeypatch):
    """The document is already gone when the scrub runs; a scrub error must
    not turn a committed delete into a 500 (the worker path already swallows
    it the same way)."""
    client, store, job_store, sink_path, token = harness
    service = _sync_delete_service(monkeypatch, job_store)
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")

    def _boom(**kwargs):
        raise RuntimeError("job store unavailable")

    monkeypatch.setattr(service, "scrub_document", _boom)
    response = client.post(
        "/api/delete", json={"filename": "report.pdf", "claim_id": "claim-1"},
        headers=_auth(token("admin-1", ["admin"])),
    )

    assert response.status_code == 200
    assert store.get_claim_documents("claim-1") == []
    deleted = [e for e in _events(sink_path) if e["event"] == "delete" and e["outcome"] == "deleted"]
    assert len(deleted) == 1
