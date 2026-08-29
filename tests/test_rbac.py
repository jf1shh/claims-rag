"""Unit tests for Phase 4.2 RBAC (backend/rbac.py).

Covers the role -> permission matrix (adjuster / supervisor / siu / admin),
admin as implicit superuser, and the ClaimAccessPolicy claim-level ACLs:
file loading, admin/supervisor/siu full access, adjuster assigned-only when a
file is configured, and the open policy when none is configured.
"""

import json

import pytest

from backend.rbac import (
    PERMISSIONS,
    ClaimAccessPolicy,
    granted_permissions,
    require_permission,
)
from backend.tenant_context import PrincipalContext


def _principal(subject="user-1", roles=("adjuster",), tenant="tenant-a"):
    return PrincipalContext(subject=subject, tenant_id=tenant, roles=frozenset(roles))


# --- permission matrix -------------------------------------------------------


def test_given_permission_matrix_then_all_roles_are_covered():
    assert set(PERMISSIONS) == {"adjuster", "supervisor", "siu", "admin"}


def test_given_adjuster_then_claim_work_permissions_are_granted_but_delete_and_global_upload_are_not():
    principal = _principal(roles=("adjuster",))
    require_permission(principal, "documents:read")
    require_permission(principal, "claims:read")
    require_permission(principal, "claims:write")
    with pytest.raises(PermissionError):
        require_permission(principal, "documents:delete")
    with pytest.raises(PermissionError):
        require_permission(principal, "documents:upload")
    with pytest.raises(PermissionError):
        require_permission(principal, "eval:search")


def test_given_supervisor_then_upload_and_delete_are_granted():
    principal = _principal(roles=("supervisor",))
    require_permission(principal, "documents:read")
    require_permission(principal, "documents:upload")
    require_permission(principal, "documents:delete")
    require_permission(principal, "claims:read")
    require_permission(principal, "claims:write")
    with pytest.raises(PermissionError):
        require_permission(principal, "eval:search")


def test_given_siu_then_read_only_investigation_permissions_are_granted():
    principal = _principal(roles=("siu",))
    require_permission(principal, "documents:read")
    require_permission(principal, "claims:read")
    require_permission(principal, "eval:search")
    with pytest.raises(PermissionError):
        require_permission(principal, "claims:write")
    with pytest.raises(PermissionError):
        require_permission(principal, "documents:upload")
    with pytest.raises(PermissionError):
        require_permission(principal, "documents:delete")


def test_given_admin_then_every_permission_is_granted():
    principal = _principal(roles=("admin",))
    for permission in granted_permissions(principal):
        require_permission(principal, permission)
    # Admin is an implicit superuser: the union of all matrices.
    assert granted_permissions(principal) == frozenset().union(*PERMISSIONS.values())


def test_given_multi_role_principal_then_permissions_are_union():
    principal = _principal(roles=("adjuster", "siu"))
    require_permission(principal, "eval:search")  # from siu
    require_permission(principal, "claims:write")  # from adjuster
    with pytest.raises(PermissionError):
        require_permission(principal, "documents:delete")


# --- ClaimAccessPolicy: open (no file) ---------------------------------------


def test_given_no_assignments_then_policy_is_open_for_everyone():
    policy = ClaimAccessPolicy()
    assert policy.is_open() is True
    assert policy.can_access_claim(_principal(roles=("adjuster",)), "#2026-99382") is True
    assert policy.can_access_claim(_principal(subject="nobody", roles=("adjuster",)), "#anything") is True


def test_given_no_assignments_then_filter_returns_all_claims():
    policy = ClaimAccessPolicy()
    claims = [{"id": "c1"}, {"id": "c2"}]
    assert policy.filter_claims(_principal(roles=("adjuster",)), claims) == claims


# --- ClaimAccessPolicy: configured file --------------------------------------


def test_given_assignments_file_then_parsed(tmp_path):
    path = tmp_path / "claim-acls.json"
    path.write_text(
        json.dumps({"#2026-99382": ["alice", "bob"], "#2026-10492": ["bob"]}),
        encoding="utf-8",
    )
    policy = ClaimAccessPolicy.from_file(path)
    assert policy.is_open() is False
    assert policy.can_access_claim(_principal(subject="alice"), "#2026-99382") is True
    assert policy.can_access_claim(_principal(subject="alice"), "#2026-10492") is False
    assert policy.can_access_claim(_principal(subject="bob"), "#2026-10492") is True


def test_given_non_object_acls_file_then_error(tmp_path):
    path = tmp_path / "claim-acls.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        ClaimAccessPolicy.from_file(path)


def test_given_assignments_when_adjuster_accesses_assigned_claim_then_allowed():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    assert policy.can_access_claim(_principal(subject="alice"), "#2026-99382") is True
    policy.require_claim_access(_principal(subject="alice"), "#2026-99382")


def test_given_assignments_when_adjuster_accesses_unassigned_claim_then_denied():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    principal = _principal(subject="carol")
    assert policy.can_access_claim(principal, "#2026-99382") is False
    with pytest.raises(PermissionError):
        policy.require_claim_access(principal, "#2026-99382")


def test_given_assignments_when_adjuster_accesses_unknown_claim_then_denied():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    assert policy.can_access_claim(_principal(subject="alice"), "#not-in-file") is False


def test_given_assignments_when_supervisor_then_all_claims_accessible():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    for claim in ("#2026-99382", "#2026-10492", "#unknown"):
        assert policy.can_access_claim(_principal(subject="sup-1", roles=("supervisor",)), claim) is True


def test_given_assignments_when_siu_then_all_claims_accessible():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    assert policy.can_access_claim(_principal(subject="siu-1", roles=("siu",)), "#2026-10492") is True


def test_given_assignments_when_admin_then_all_claims_accessible():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    assert policy.can_access_claim(_principal(subject="admin-1", roles=("admin",)), "#anything") is True


def test_given_assignments_when_filtering_then_adjuster_sees_only_assigned_claims():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"], "#2026-10492": ["bob"]})
    claims = [{"id": "#2026-99382"}, {"id": "#2026-10492"}, {"id": "#2026-30291"}]
    visible = policy.filter_claims(_principal(subject="alice"), claims)
    assert [c["id"] for c in visible] == ["#2026-99382"]


def test_given_assignments_when_filtering_then_admin_sees_all_claims():
    policy = ClaimAccessPolicy({"#2026-99382": ["alice"]})
    claims = [{"id": "#2026-99382"}, {"id": "#2026-10492"}]
    assert len(policy.filter_claims(_principal(roles=("admin",)), claims)) == 2
    assert len(policy.filter_claims(_principal(roles=("supervisor",)), claims)) == 2
    assert len(policy.filter_claims(_principal(roles=("siu",)), claims)) == 2
