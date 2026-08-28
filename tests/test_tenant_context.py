import pytest

from backend.tenant_context import (
    PrincipalContext,
    development_principal,
    principal_from_request,
    require_role,
    require_tenant_scope,
)
from config import Settings


def test_given_principal_with_role_when_role_is_required_then_access_is_allowed():
    context = PrincipalContext("user-1", "tenant-a", frozenset({"adjuster"}))
    require_role(context, "adjuster")


def test_given_principal_without_role_when_role_is_required_then_access_is_denied():
    context = PrincipalContext("user-1", "tenant-a", frozenset())
    with pytest.raises(PermissionError):
        require_role(context, "supervisor")


def test_given_principal_from_tenant_a_when_tenant_b_is_requested_then_access_is_denied():
    context = PrincipalContext("user-1", "tenant-a", frozenset())
    with pytest.raises(PermissionError):
        require_tenant_scope(context, "tenant-b")


def test_given_development_settings_when_context_is_resolved_then_explicit_local_identity_is_used():
    context = principal_from_request(None, Settings.from_env({"APP_ENV": "development"}))
    assert context == development_principal()
    assert context.is_development_identity is True


def test_given_production_settings_when_context_is_resolved_without_authentication_then_access_is_denied():
    with pytest.raises(PermissionError, match="authenticated"):
        principal_from_request(None, Settings.from_env({"APP_ENV": "production", "SIMULATION_MODE": "false", "CORS_ORIGINS": "https://claims.example.com"}))
