"""Hermetic tests for the Phase 4.1 authentication seam (backend/authn.py).

The OIDC adapter does real JWT/JWKS verification: a locally-generated RSA
keypair stands in for the identity provider, tests mint real signed JWTs with
PyJWT, and the JWKS fetcher is injected so no HTTP or live IdP is needed
(mirrors the moto pattern used for S3/SQS). Every failure mode that matters --
expired, wrong issuer, wrong audience, bad signature, unknown kid -- is
exercised against the real verification logic.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.authn import (
    AuthenticationError,
    ChainAuthenticator,
    DevelopmentAuthenticator,
    OIDCAuthenticator,
    ServiceAccountAuthenticator,
    build_authenticator,
)
from backend.tenant_context import development_principal

ISSUER = "https://idp.example.com"
AUDIENCE = "claimsrag-app"


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


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


def _mint(private_key, *, kid="test-key-1", issuer=ISSUER, audience=AUDIENCE, **claims):
    payload = {
        "sub": "user-42",
        "tenant_id": "tenant-a",
        "roles": ["adjuster"],
        "iss": issuer,
        "aud": audience,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        **claims,
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def _oidc(jwks, cache_ttl=300, clock=None, tenant_claim="tenant_id"):
    return OIDCAuthenticator(
        issuer=ISSUER,
        client_id=AUDIENCE,
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        tenant_claim=tenant_claim,
        cache_ttl_seconds=cache_ttl,
        jwks_fetcher=lambda url: jwks,
        clock=clock or (lambda: 1000.0),
    )


# --- DevelopmentAuthenticator -------------------------------------------------


def test_given_development_env_when_authenticating_then_explicit_local_identity_is_used():
    principal = DevelopmentAuthenticator("development").authenticate(_FakeRequest({}))
    assert principal == development_principal()
    assert principal.is_development_identity is True


def test_given_test_env_when_authenticating_then_local_identity_is_used():
    principal = DevelopmentAuthenticator("test").authenticate(_FakeRequest({}))
    assert principal == development_principal()


def test_given_production_env_when_authenticating_then_access_is_denied():
    with pytest.raises(AuthenticationError):
        DevelopmentAuthenticator("production").authenticate(_FakeRequest({}))


# --- OIDCAuthenticator: happy path -------------------------------------------


def test_given_valid_token_when_authenticating_then_principal_is_resolved():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, roles=["adjuster", "siu", "not-a-role"])

    principal = _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))

    assert principal.subject == "user-42"
    assert principal.tenant_id == "tenant-a"
    # Unknown roles are filtered against VALID_ROLES; known ones survive.
    assert principal.roles == frozenset({"adjuster", "siu"})
    assert principal.is_development_identity is False


def test_given_token_with_custom_tenant_claim_when_authenticating_then_claim_is_used():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, tid="tenant-entra")

    principal = _oidc(jwks, tenant_claim="tid").authenticate(
        _FakeRequest({"Authorization": f"Bearer {token}"})
    )

    assert principal.tenant_id == "tenant-entra"


# --- OIDCAuthenticator: failure modes ----------------------------------------


def test_given_missing_authorization_header_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    with pytest.raises(AuthenticationError, match="missing bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({}))


def test_given_wrong_auth_scheme_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    token = _mint(private_key)
    with pytest.raises(AuthenticationError, match="missing bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Basic {token}"}))


def test_given_expired_token_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, exp=datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_wrong_issuer_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, issuer="https://evil.example.com")
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_wrong_audience_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, audience="some-other-app")
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_token_signed_by_unknown_key_when_authenticating_then_denied():
    # Signed with a different keypair than the JWKS advertises: signature
    # verification must fail, not be skipped because the kid is absent.
    private_key, jwks = _make_keypair()
    rogue_key, _ = _make_keypair(kid="test-key-1")
    token = _mint(rogue_key)
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_token_with_unknown_kid_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, kid="nope-not-a-key")
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_token_missing_tenant_claim_when_authenticating_then_denied():
    private_key, jwks = _make_keypair()
    token = _mint(private_key, tenant_id=None)
    with pytest.raises(AuthenticationError, match="missing subject or tenant"):
        _oidc(jwks).authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_jwks_fetch_failure_when_authenticating_then_denied():
    private_key, _ = _make_keypair()
    token = _mint(private_key)

    def failing_fetcher(url):
        raise RuntimeError("network down")

    authenticator = OIDCAuthenticator(
        issuer=ISSUER,
        client_id=AUDIENCE,
        jwks_url="https://idp.example.com/jwks.json",
        jwks_fetcher=failing_fetcher,
        clock=lambda: 1.0,
    )
    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        authenticator.authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))


def test_given_jwks_when_multiple_requests_then_fetched_once_within_ttl_and_refreshed_after():
    private_key, jwks = _make_keypair()
    token = _mint(private_key)
    clock_state = {"now": 0.0}
    calls = []

    def clock():
        return clock_state["now"]

    def fetcher(url):
        calls.append(url)
        return jwks

    authenticator = OIDCAuthenticator(
        issuer=ISSUER,
        client_id=AUDIENCE,
        jwks_url="https://idp.example.com/jwks.json",
        cache_ttl_seconds=300,
        jwks_fetcher=fetcher,
        clock=clock,
    )
    request = _FakeRequest({"Authorization": f"Bearer {token}"})

    authenticator.authenticate(request)
    authenticator.authenticate(request)
    assert len(calls) == 1  # cached

    clock_state["now"] = 301.0
    authenticator.authenticate(request)
    assert len(calls) == 2  # TTL elapsed -> refetched


# --- ServiceAccountAuthenticator ---------------------------------------------


def _service_accounts():
    return ServiceAccountAuthenticator(
        {
            "sa_worker_key": {"subject": "ingestion-worker", "tenant_id": "tenant-a", "roles": ["admin"]},
            "sa_ci_key": {"subject": "ci-runner", "tenant_id": "tenant-b", "roles": ["adjuster", "siu"]},
        }
    )


def test_given_valid_api_key_when_authenticating_then_principal_is_resolved():
    principal = _service_accounts().authenticate(_FakeRequest({"X-API-Key": "sa_worker_key"}))
    assert principal.subject == "ingestion-worker"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == frozenset({"admin"})


def test_given_missing_api_key_when_authenticating_then_denied():
    with pytest.raises(AuthenticationError, match="missing API key"):
        _service_accounts().authenticate(_FakeRequest({}))


def test_given_unknown_api_key_when_authenticating_then_denied():
    with pytest.raises(AuthenticationError, match="invalid API key"):
        _service_accounts().authenticate(_FakeRequest({"X-API-Key": "sa_wrong"}))


def test_given_service_accounts_file_when_loading_then_accounts_are_parsed(tmp_path):
    path = tmp_path / "service-accounts.json"
    path.write_text(
        json.dumps({"key_1": {"subject": "bot", "tenant_id": "tenant-a", "roles": ["adjuster"]}}),
        encoding="utf-8",
    )
    authenticator = ServiceAccountAuthenticator.from_file(path)
    principal = authenticator.authenticate(_FakeRequest({"X-API-Key": "key_1"}))
    assert principal.subject == "bot"
    assert principal.tenant_id == "tenant-a"


# --- ChainAuthenticator -------------------------------------------------------


def test_given_chain_when_oidc_fails_then_service_account_is_tried():
    private_key, jwks = _make_keypair()
    oidc = _oidc(jwks)
    chain = ChainAuthenticator([oidc, _service_accounts()])

    # No credentials -> denied.
    with pytest.raises(AuthenticationError):
        chain.authenticate(_FakeRequest({}))

    # API key succeeds even though OIDC fails first.
    principal = chain.authenticate(_FakeRequest({"X-API-Key": "sa_worker_key"}))
    assert principal.tenant_id == "tenant-a"

    # Valid OIDC token succeeds too.
    token = _mint(private_key)
    principal = chain.authenticate(_FakeRequest({"Authorization": f"Bearer {token}"}))
    assert principal.subject == "user-42"


def test_given_chain_with_no_authenticators_when_built_then_error():
    with pytest.raises(ValueError):
        ChainAuthenticator([])


# --- build_authenticator (config-driven wiring) ------------------------------


class _FakeSettings:
    def __init__(self, **kwargs):
        self.auth_providers = kwargs.get("auth_providers", ("development",))
        self.app_env = kwargs.get("app_env", "development")
        self.oidc_issuer = kwargs.get("oidc_issuer")
        self.oidc_client_id = kwargs.get("oidc_client_id")
        self.oidc_jwks_url = kwargs.get("oidc_jwks_url")
        self.oidc_roles_claim = kwargs.get("oidc_roles_claim", "roles")
        self.oidc_tenant_claim = kwargs.get("oidc_tenant_claim", "tenant_id")
        self.oidc_cache_ttl_seconds = kwargs.get("oidc_cache_ttl_seconds", 300)
        self.service_accounts_file = kwargs.get("service_accounts_file")


def test_given_development_provider_when_building_then_development_authenticator():
    authenticator = build_authenticator(_FakeSettings(auth_providers=("development",)))
    assert isinstance(authenticator, DevelopmentAuthenticator)


def test_given_oidc_provider_when_building_then_oidc_authenticator():
    authenticator = build_authenticator(
        _FakeSettings(
            auth_providers=("oidc",),
            oidc_issuer=ISSUER,
            oidc_client_id=AUDIENCE,
        )
    )
    assert isinstance(authenticator, OIDCAuthenticator)


def test_given_oidc_provider_without_issuer_when_building_then_error():
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        build_authenticator(_FakeSettings(auth_providers=("oidc",), oidc_client_id=AUDIENCE))


def test_given_multiple_providers_when_building_then_chain_is_returned(tmp_path):
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(json.dumps({"key_1": {"subject": "bot", "tenant_id": "tenant-a", "roles": []}}), encoding="utf-8")
    authenticator = build_authenticator(
        _FakeSettings(
            auth_providers=("oidc", "service-accounts"),
            oidc_issuer=ISSUER,
            oidc_client_id=AUDIENCE,
            service_accounts_file=str(sa_file),
        )
    )
    assert isinstance(authenticator, ChainAuthenticator)
    assert len(authenticator.authenticators) == 2


def test_given_unknown_provider_when_building_then_error():
    with pytest.raises(ValueError, match="unknown auth provider"):
        build_authenticator(_FakeSettings(auth_providers=("magic",)))
