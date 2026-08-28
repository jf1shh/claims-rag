from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_ROLES = frozenset({"adjuster", "supervisor", "siu", "admin"})


@dataclass(frozen=True)
class PrincipalContext:
    subject: str
    tenant_id: str
    roles: frozenset[str]
    is_development_identity: bool = False

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("subject must not be empty")
        if not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        unknown = self.roles - VALID_ROLES
        if unknown:
            raise ValueError(f"unknown roles: {sorted(unknown)}")


def development_principal() -> PrincipalContext:
    return PrincipalContext(
        subject="local-development-user",
        tenant_id="local-development",
        roles=frozenset({"admin", "adjuster"}),
        is_development_identity=True,
    )


def principal_from_request(request: Any, settings: Any) -> PrincipalContext:
    """Resolve a principal from explicit server configuration.

    Production authentication is intentionally not implemented by this seam;
    rejecting missing production identity is safer than treating a client
    header as authentication. Local/test profiles receive the explicit
    development principal.
    """
    if settings.app_env in {"development", "test"}:
        return development_principal()
    raise PermissionError("authenticated production principal is required")


def require_role(context: PrincipalContext, role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"unknown role: {role}")
    if role not in context.roles and "admin" not in context.roles:
        raise PermissionError(f"role {role!r} is required")


def require_tenant_scope(context: PrincipalContext, tenant_id: str) -> None:
    if context.tenant_id != tenant_id:
        raise PermissionError("tenant scope does not match the authenticated principal")
