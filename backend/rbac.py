from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.tenant_context import PrincipalContext

# Roles that see every claim in the tenant regardless of assignment.
_ALL_CLAIMS_ROLES = frozenset({"admin", "supervisor", "siu"})

# Actions in the API surface, granted per role. `admin` is an implicit
# superuser (see require_permission) -- the matrix entries below document the
# intended grants explicitly rather than relying on the override alone.
#
#   adjuster    -- handles claims assigned to them: reads guidelines/claims,
#                  attaches documents to their claims. No deletions, no global
#                  guideline uploads.
#   supervisor  -- adjuster + all claims, global guideline upload, deletion.
#   siu         -- read-only investigation: sees all claims and chats, but
#                  cannot modify anything (no upload/delete/attach).
#   admin       -- everything, including the eval harness endpoint.
PERMISSIONS: Mapping[str, frozenset[str]] = {
    "adjuster": frozenset(
        {"documents:read", "claims:read", "claims:write"}
    ),
    "supervisor": frozenset(
        {
            "documents:read",
            "documents:upload",
            "documents:delete",
            "claims:read",
            "claims:write",
        }
    ),
    "siu": frozenset({"documents:read", "claims:read", "eval:search"}),
    "admin": frozenset(
        {
            "documents:read",
            "documents:upload",
            "documents:delete",
            "claims:read",
            "claims:write",
            "eval:search",
        }
    ),
}


def granted_permissions(context: PrincipalContext) -> frozenset[str]:
    """Union of every permission granted by the principal's roles."""
    if "admin" in context.roles:
        return frozenset().union(*PERMISSIONS.values())
    granted: set[str] = set()
    for role in context.roles:
        granted |= PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def require_permission(context: PrincipalContext, permission: str) -> None:
    """Raises PermissionError unless the principal holds the permission."""
    if permission not in granted_permissions(context):
        raise PermissionError(f"permission {permission!r} is required")


class ClaimAccessPolicy:
    """Claim-level ACLs: which principal may access which claim.

    Assignments come from a JSON file mapping claim_id -> [subjects]::

        {"#2026-99382": ["adjuster-alice"], "#2026-10492": ["adjuster-bob"]}

    A principal may access a claim if they hold one of the _ALL_CLAIMS_ROLES
    (admin/supervisor/siu) or, for adjusters, their subject is assigned to it.
    With no assignments file configured (the dev/test default), claim access
    is open within the tenant -- matching the pre-4.2 behavior; the moment a
    file is configured, adjuster access is restricted to their assignments.
    """

    def __init__(self, assignments: Mapping[str, Sequence[str]] | None = None):
        self._open = assignments is None
        self._assignments: dict[str, frozenset[str]] = {
            claim_id: frozenset(subjects)
            for claim_id, subjects in (assignments or {}).items()
        }

    @classmethod
    def from_file(cls, path: str | Path) -> "ClaimAccessPolicy":
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("claim ACLs file must contain a JSON object")
        return cls(raw)

    def is_open(self) -> bool:
        return self._open

    def can_access_claim(self, context: PrincipalContext, claim_id: str) -> bool:
        if _ALL_CLAIMS_ROLES.intersection(context.roles):
            return True
        if "adjuster" not in context.roles:
            return False
        if self.is_open():
            return True
        return context.subject in self._assignments.get(claim_id, frozenset())

    def require_claim_access(self, context: PrincipalContext, claim_id: str) -> None:
        if not self.can_access_claim(context, claim_id):
            raise PermissionError(
                f"access to claim {claim_id!r} is not permitted for this principal"
            )

    def filter_claims(self, context: PrincipalContext, claims: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Returns only the claims the principal may access (list shape used
        by CLAIMS_DATA-driven endpoints)."""
        if _ALL_CLAIMS_ROLES.intersection(context.roles) or self.is_open():
            return list(claims)
        allowed = {
            claim_id for claim_id, subjects in self._assignments.items() if context.subject in subjects
        }
        return [claim for claim in claims if claim.get("id") in allowed]
