"""API-level tests for Phase 4.2 RBAC enforcement.

Uses the OIDC authenticator (as in test_api_auth.py) with principals carrying
different roles, plus a configured ClaimAccessPolicy, and asserts the 200/403
matrix: adjusters on their assigned claims, adjusters denied on unassigned
claims, supervisor/siu/admin full access, permission denials for delete/upload
on restricted roles, and the dev default (open policy + admin principal)
preserving pre-4.2 behavior.
"""

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app
from backend.authn import OIDCAuthenticator
from backend.rbac import ClaimAccessPolicy

ISSUER = "https://idp.example.com"
AUDIENCE = "claimsrag-app"

ASSIGNMENTS = {
    "#2026-99382": ["adjuster-alice"],
    "#2026-10492": ["adjuster-bob"],
}


def _make_keypair(kid="test-key-1"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64(value):
        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": _b64(public_numbers.n),
                "e": _b64(public_numbers.e),
            }
        ]
    }
    return private_key, jwks


def _token(private_key, *, subject, roles, tenant="tenant-a"):
    payload = {
        "sub": subject,
        "tenant_id": tenant,
        "roles": roles,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key-1"})


@pytest.fixture
def enforced(monkeypatch):
    """OIDC authenticator + configured claim ACLs, so both permission and
    claim-level checks are live. Returns a token factory."""
    private_key, jwks = _make_keypair()
    oidc = OIDCAuthenticator(
        issuer=ISSUER,
        client_id=AUDIENCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        jwks_fetcher=lambda url: jwks,
        clock=lambda: 1000.0,
    )
    monkeypatch.setattr(app_module, "_authenticator", oidc)
    monkeypatch.setattr(app_module, "_claim_access_policy", ClaimAccessPolicy(ASSIGNMENTS))
    return lambda subject, roles: _token(private_key, subject=subject, roles=roles)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _claim_path(claim_id: str) -> str:
    # Claim IDs contain '#' which is a URL fragment delimiter -- encode it.
    return f"/api/documents/claim/{quote(claim_id, safe='')}"


# --- claims listing (ACL filtering) ------------------------------------------


def test_given_adjuster_when_listing_claims_then_only_assigned_claims_are_returned(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).get("/api/claims", headers=_auth(token))
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == ["#2026-99382"]


def test_given_supervisor_when_listing_claims_then_all_claims_are_returned(enforced):
    token = enforced("sup-1", ["supervisor"])
    response = TestClient(app).get("/api/claims", headers=_auth(token))
    assert response.status_code == 200
    assert len(response.json()) == 4


def test_given_siu_when_listing_claims_then_all_claims_are_returned(enforced):
    token = enforced("siu-1", ["siu"])
    response = TestClient(app).get("/api/claims", headers=_auth(token))
    assert response.status_code == 200
    assert len(response.json()) == 4


# --- claim-scoped document access --------------------------------------------


def test_given_adjuster_when_accessing_assigned_claim_documents_then_200(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).get(_claim_path("#2026-99382"), headers=_auth(token))
    assert response.status_code == 200


def test_given_adjuster_when_accessing_unassigned_claim_documents_then_403(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).get(_claim_path("#2026-10492"), headers=_auth(token))
    assert response.status_code == 403


def test_given_supervisor_when_accessing_any_claim_documents_then_200(enforced):
    token = enforced("sup-1", ["supervisor"])
    assert TestClient(app).get(_claim_path("#2026-10492"), headers=_auth(token)).status_code == 200


def test_given_siu_when_accessing_any_claim_documents_then_200(enforced):
    token = enforced("siu-1", ["siu"])
    assert TestClient(app).get(_claim_path("#2026-55912"), headers=_auth(token)).status_code == 200


# --- claim upload (claims:write) ---------------------------------------------


def test_given_adjuster_when_uploading_to_assigned_claim_then_202_or_200(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/upload-claim-file",
        headers=_auth(token),
        data={"claim_id": "#2026-99382"},
        files={"file": ("report.txt", b"claim report", "text/plain")},
    )
    assert response.status_code in (200, 202)


def test_given_adjuster_when_uploading_to_unassigned_claim_then_403(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/upload-claim-file",
        headers=_auth(token),
        data={"claim_id": "#2026-10492"},
        files={"file": ("report.txt", b"claim report", "text/plain")},
    )
    assert response.status_code == 403


def test_given_siu_when_uploading_claim_document_then_403(enforced):
    # SIU is read-only: can inspect and chat, cannot attach documents.
    token = enforced("siu-1", ["siu"])
    response = TestClient(app).post(
        "/api/upload-claim-file",
        headers=_auth(token),
        data={"claim_id": "#2026-55912"},
        files={"file": ("report.txt", b"claim report", "text/plain")},
    )
    assert response.status_code == 403


# --- global document upload/delete (documents:upload / documents:delete) -----


def test_given_adjuster_when_uploading_global_document_then_403(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/upload",
        headers=_auth(token),
        files={"file": ("policy.txt", b"policy text", "text/plain")},
    )
    assert response.status_code == 403


def test_given_supervisor_when_uploading_global_document_then_200_or_202(enforced):
    token = enforced("sup-1", ["supervisor"])
    response = TestClient(app).post(
        "/api/upload",
        headers=_auth(token),
        files={"file": ("policy.txt", b"policy text", "text/plain")},
    )
    assert response.status_code in (200, 202)


def test_given_adjuster_when_deleting_document_then_403(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post("/api/delete", headers=_auth(token), json={"filename": "policy.txt"})
    assert response.status_code == 403


def test_given_supervisor_when_deleting_document_then_not_403(enforced):
    token = enforced("sup-1", ["supervisor"])
    response = TestClient(app).post("/api/delete", headers=_auth(token), json={"filename": "ghost.txt"})
    assert response.status_code != 403  # 404 for missing doc, but permission passes


# --- chat scoping -------------------------------------------------------------


def test_given_adjuster_when_chatting_global_then_200(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/chat",
        headers=_auth(token),
        json={"query": "labor rate?", "engine": "simulated", "claim_id": None},
    )
    assert response.status_code == 200


def test_given_adjuster_when_chatting_assigned_claim_then_200(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/chat",
        headers=_auth(token),
        json={"query": "estimate?", "engine": "simulated", "claim_id": "#2026-99382"},
    )
    assert response.status_code == 200


def test_given_adjuster_when_chatting_unassigned_claim_then_403(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/chat",
        headers=_auth(token),
        json={"query": "estimate?", "engine": "simulated", "claim_id": "#2026-10492"},
    )
    assert response.status_code == 403


# --- eval endpoint (eval:search) ---------------------------------------------


def test_given_adjuster_when_calling_eval_search_then_403(enforced):
    token = enforced("adjuster-alice", ["adjuster"])
    response = TestClient(app).post(
        "/api/eval/search",
        headers=_auth(token),
        json={"query": "labor", "mode": "naive", "top_k": 4},
    )
    assert response.status_code == 403


def test_given_siu_when_calling_eval_search_then_200(enforced):
    token = enforced("siu-1", ["siu"])
    response = TestClient(app).post(
        "/api/eval/search",
        headers=_auth(token),
        json={"query": "labor", "mode": "naive", "top_k": 4},
    )
    assert response.status_code == 200


def test_given_admin_when_calling_eval_search_then_200(enforced):
    token = enforced("admin-1", ["admin"])
    response = TestClient(app).post(
        "/api/eval/search",
        headers=_auth(token),
        json={"query": "labor", "mode": "naive", "top_k": 4},
    )
    assert response.status_code == 200


# --- dev default (open policy + admin principal) preserves behavior ----------


def test_given_development_default_when_listing_claims_then_all_returned():
    # Module-level _claim_access_policy is open (no CLAIM_ACLS_FILE env) and
    # the development principal is admin+adjuster: pre-4.2 behavior intact.
    client = TestClient(app)
    assert client.get("/api/claims").status_code == 200
    assert len(client.get("/api/claims").json()) == 4
    assert client.get("/api/documents").status_code == 200
