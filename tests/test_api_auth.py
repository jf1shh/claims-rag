"""API-level tests for Phase 4.1 authentication enforcement.

Every /api/* route carries the get_current_tenant dependency. With the
development provider (the default) requests resolve to the explicit local
identity, so the legacy behavior is untouched; with an enforced authenticator
(OIDC or service accounts) a missing/invalid credential must 401 everywhere.
This module swaps the module-level _authenticator for an OIDC chain backed by
a locally-generated keypair and asserts the enforcement surface.
"""

import base64
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app
from backend.authn import (
    ChainAuthenticator,
    OIDCAuthenticator,
    ServiceAccountAuthenticator,
)

ISSUER = "https://idp.example.com"
AUDIENCE = "claimsrag-app"

# All /api routes (health + static frontend are deliberately open; see the
# get_current_tenant docstring / migration doc for the rationale).
API_ROUTES = [
    ("GET", "/api/status"),
    ("GET", "/api/documents"),
    ("GET", "/api/claims"),
    ("GET", "/api/jobs/job_abc"),
    ("POST", "/api/upload"),
    ("POST", "/api/upload-claim-file"),
    ("GET", "/api/documents/claim/claim-1"),
    ("GET", "/api/documents/content/labor.txt"),
    ("GET", "/api/documents/download/labor.txt"),
    ("POST", "/api/delete"),
    ("POST", "/api/chat"),
    ("POST", "/api/eval/search"),
    ("GET", "/api/auth/me"),
]


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


def _mint(private_key, *, kid="test-key-1", **claims):
    payload = {
        "sub": "user-42",
        "tenant_id": "tenant-a",
        "roles": ["admin"],
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        **claims,
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def enforced_auth(monkeypatch):
    """Swaps the app's module-level authenticator for an enforced OIDC +
    service-account chain, so a missing credential 401s on every route."""
    private_key, jwks = _make_keypair()
    oidc = OIDCAuthenticator(
        issuer=ISSUER,
        client_id=AUDIENCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        jwks_fetcher=lambda url: jwks,
        clock=lambda: 1000.0,
    )
    service_accounts = ServiceAccountAuthenticator(
        {"sa_worker_key": {"subject": "ingestion-worker", "tenant_id": "tenant-a", "roles": ["admin"]}}
    )
    monkeypatch.setattr(app_module, "_authenticator", ChainAuthenticator([oidc, service_accounts]))
    return private_key


# --- enforcement surface -----------------------------------------------------


@pytest.mark.parametrize("method,path", API_ROUTES)
def test_given_enforced_auth_when_route_called_without_credentials_then_401(enforced_auth, method, path):
    response = getattr(TestClient(app), method.lower())(path)
    assert response.status_code == 401


def test_given_enforced_auth_when_route_called_with_invalid_token_then_401(enforced_auth):
    client = TestClient(app)
    response = client.get("/api/documents", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_given_enforced_auth_when_route_called_with_valid_token_then_200(enforced_auth):
    token = _mint(enforced_auth)
    client = TestClient(app)
    response = client.get("/api/documents", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_given_enforced_auth_when_service_account_key_then_200(enforced_auth):
    client = TestClient(app)
    response = client.get("/api/documents", headers={"X-API-Key": "sa_worker_key"})
    assert response.status_code == 200


def test_given_enforced_auth_when_health_endpoints_then_open_without_auth(enforced_auth):
    client = TestClient(app)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_given_enforced_auth_when_frontend_mount_then_open_without_auth(enforced_auth):
    # The static frontend must stay reachable: it hosts the login gate itself.
    assert TestClient(app).get("/").status_code == 200


# --- /api/auth/me -------------------------------------------------------------


def test_given_enforced_auth_when_me_without_token_then_401(enforced_auth):
    assert TestClient(app).get("/api/auth/me").status_code == 401


def test_given_enforced_auth_when_me_with_valid_token_then_principal_is_reported(enforced_auth):
    token = _mint(enforced_auth, roles=["adjuster"])
    response = TestClient(app).get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "user-42"
    assert body["tenant_id"] == "tenant-a"
    assert body["roles"] == ["adjuster"]
    assert body["is_development_identity"] is False


def test_given_enforced_auth_when_me_with_service_account_then_principal_is_reported(enforced_auth):
    response = TestClient(app).get("/api/auth/me", headers={"X-API-Key": "sa_worker_key"})
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "ingestion-worker"
    assert body["tenant_id"] == "tenant-a"
    assert body["is_development_identity"] is False


def test_given_enforced_auth_when_expired_token_then_401(enforced_auth):
    token = _mint(enforced_auth, exp=datetime.now(timezone.utc) - timedelta(hours=1))
    response = TestClient(app).get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# --- development mode (default) keeps legacy behavior -------------------------


def test_given_development_mode_when_route_called_without_credentials_then_200():
    # The module-level authenticator is the DevelopmentAuthenticator built at
    # import time (real env defaults to development): requests resolve to the
    # explicit local identity, so the pre-Phase-4 behavior is untouched.
    client = TestClient(app)
    assert client.get("/api/claims").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/me").json()["is_development_identity"] is True
