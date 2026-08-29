from __future__ import annotations

import hmac
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import jwt as pyjwt
from fastapi import Request

from backend.tenant_context import VALID_ROLES, PrincipalContext, development_principal

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """A request could not be authenticated (mapped to HTTP 401)."""


class Authenticator(ABC):
    """Contract every authentication backend must implement.

    ``authenticate`` resolves an HTTP request to a ``PrincipalContext``
    (subject, tenant, roles) or raises ``AuthenticationError``. Nothing in the
    seam knows about Okta/Entra/Google specifically -- the OIDC adapter is one
    implementation, the development identity is another, service-account API
    keys are a third, and a chain composes them.
    """

    @abstractmethod
    def authenticate(self, request: Request) -> PrincipalContext:
        raise NotImplementedError


class DevelopmentAuthenticator(Authenticator):
    """Explicit local/test identity (Phase 0 seam).

    Development and test profiles receive the explicit development principal;
    any other environment is rejected -- treating a client-supplied identity as
    authentication in production is exactly the failure this seam exists to
    prevent. Configuration validation independently forbids the development
    provider in production, so this branch is defense-in-depth.
    """

    def __init__(self, app_env: str):
        self.app_env = app_env

    def authenticate(self, request: Request) -> PrincipalContext:
        if self.app_env in {"development", "test"}:
            return development_principal()
        raise AuthenticationError("authenticated principal is required")


def _default_jwks_fetcher(url: str) -> dict[str, Any]:
    """Fetches a JWKS document over HTTP (the real OIDC path)."""
    import requests

    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


class OIDCAuthenticator(Authenticator):
    """Verifies a Bearer JWT against an issuer's JSON Web Key Set.

    Signature, ``exp``, ``iss``, and ``aud`` are all checked via PyJWT against
    the key selected by the token's ``kid``. JWKS is fetched lazily and cached
    for ``cache_ttl_seconds``. The tenant and role claims are configurable
    because IdPs disagree on where they live (Entra ``tid`` vs a custom
    ``tenant_id``; Okta ``groups`` vs ``roles``).

    The JWKS fetcher is injectable so tests can serve a locally-generated
    keypair without any HTTP or live IdP (mirrors the moto pattern used for
    S3/SQS) -- the verification logic itself is the real thing.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        jwks_url: str,
        roles_claim: str = "roles",
        tenant_claim: str = "tenant_id",
        cache_ttl_seconds: int = 300,
        jwks_fetcher: Callable[[str], dict[str, Any]] = _default_jwks_fetcher,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.issuer = issuer
        self.client_id = client_id
        self.jwks_url = jwks_url
        self.roles_claim = roles_claim
        self.tenant_claim = tenant_claim
        self.cache_ttl_seconds = cache_ttl_seconds
        self._jwks_fetcher = jwks_fetcher
        self._clock = clock
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at = -float("inf")

    def _get_jwks(self) -> dict[str, Any]:
        now = self._clock()
        if self._jwks is None or (now - self._jwks_fetched_at) >= self.cache_ttl_seconds:
            self._jwks = self._jwks_fetcher(self.jwks_url)
            self._jwks_fetched_at = now
        return self._jwks

    def authenticate(self, request: Request) -> PrincipalContext:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("missing bearer token")
        try:
            key_set = pyjwt.PyJWKSet.from_dict(self._get_jwks())
            # PyJWT 2.13: kid matching lives on PyJWKClient (which owns its own
            # HTTP fetch); for an injectable fetcher we select the key by the
            # token header's kid ourselves (PyJWKSet[kid]) -- same semantics.
            header = pyjwt.get_unverified_header(token)
            signing_key = key_set[header["kid"]]
            payload = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
            )
        except AuthenticationError:
            raise
        except Exception as exc:
            # Covers expired/not-yet-valid tokens, signature mismatches,
            # unknown/missing kid, malformed JWKS, and network failures --
            # none of which should leak details to the caller.
            logger.info("OIDC token rejected: %s", type(exc).__name__)
            raise AuthenticationError("invalid bearer token") from exc
        return self._principal_from_payload(payload)

    def _principal_from_payload(self, payload: Mapping[str, Any]) -> PrincipalContext:
        subject = payload.get("sub")
        tenant_id = payload.get(self.tenant_claim)
        if not subject or not tenant_id:
            raise AuthenticationError("token missing subject or tenant claim")
        raw_roles = payload.get(self.roles_claim) or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        roles = frozenset(role for role in raw_roles if role in VALID_ROLES)
        return PrincipalContext(subject=subject, tenant_id=tenant_id, roles=roles)


class ServiceAccountAuthenticator(Authenticator):
    """Machine-to-machine access via a static API key (``X-API-Key`` header).

    Keys come from a JSON file mapping key -> principal fields::

        {"sa_worker_123": {"subject": "worker-1", "tenant_id": "tenant-a",
                           "roles": ["admin"]}}

    Keys are compared in constant time. This is the credential type the async
    ingestion worker / CI / integrations use instead of an interactive login.
    """

    def __init__(self, accounts: Mapping[str, Mapping[str, Any]]):
        self._accounts: dict[str, PrincipalContext] = {}
        for key, fields in accounts.items():
            subject = fields.get("subject") or key
            tenant_id = fields.get("tenant_id")
            roles = fields.get("roles") or []
            if not tenant_id:
                raise ValueError(f"service account {key!r} is missing tenant_id")
            self._accounts[key] = PrincipalContext(
                subject=subject, tenant_id=tenant_id, roles=frozenset(roles)
            )

    @classmethod
    def from_file(cls, path: str | Path) -> "ServiceAccountAuthenticator":
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("service accounts file must contain a JSON object")
        return cls(raw)

    def authenticate(self, request: Request) -> PrincipalContext:
        key = request.headers.get("X-API-Key", "")
        if not key:
            raise AuthenticationError("missing API key")
        for stored_key, principal in self._accounts.items():
            if hmac.compare_digest(key, stored_key):
                return principal
        raise AuthenticationError("invalid API key")


class ChainAuthenticator(Authenticator):
    """Tries each configured authenticator in order; first success wins."""

    def __init__(self, authenticators: Sequence[Authenticator]):
        if not authenticators:
            raise ValueError("at least one authenticator is required")
        self.authenticators = list(authenticators)

    def authenticate(self, request: Request) -> PrincipalContext:
        last_error: Exception | None = None
        for authenticator in self.authenticators:
            try:
                return authenticator.authenticate(request)
            except AuthenticationError as exc:
                last_error = exc
        raise AuthenticationError(
            str(last_error) if last_error else "unauthenticated"
        ) from last_error


def build_authenticator(settings: Any) -> Authenticator:
    """Constructs the authenticator chain for the configured providers
    (``AUTH_PROVIDERS``). Mirrors ``_build_blob_store``/``_build_queue`` in
    ``app_factory`` -- config selects the provider, this returns the adapter."""
    authenticators: list[Authenticator] = []
    for provider in settings.auth_providers:
        if provider == "development":
            authenticators.append(DevelopmentAuthenticator(settings.app_env))
        elif provider == "oidc":
            if not settings.oidc_issuer or not settings.oidc_client_id:
                raise ValueError("OIDC_ISSUER and OIDC_CLIENT_ID are required when AUTH_PROVIDERS includes oidc")
            authenticators.append(
                OIDCAuthenticator(
                    issuer=settings.oidc_issuer,
                    client_id=settings.oidc_client_id,
                    jwks_url=settings.oidc_jwks_url
                    or f"{settings.oidc_issuer.rstrip('/')}/.well-known/jwks.json",
                    roles_claim=settings.oidc_roles_claim,
                    tenant_claim=settings.oidc_tenant_claim,
                    cache_ttl_seconds=settings.oidc_cache_ttl_seconds,
                )
            )
        elif provider == "service-accounts":
            if not settings.service_accounts_file:
                raise ValueError("SERVICE_ACCOUNTS_FILE is required when AUTH_PROVIDERS includes service-accounts")
            authenticators.append(ServiceAccountAuthenticator.from_file(settings.service_accounts_file))
        else:
            raise ValueError(f"unknown auth provider: {provider!r}")
    if len(authenticators) == 1:
        return authenticators[0]
    return ChainAuthenticator(authenticators)
