"""Rating-Assurance RBAC.

Why a separate permission catalogue instead of extending the existing one:
adding keys to ``ra_backend``'s ``PERMISSION_CATALOG`` would change the payload
of ``GET /permissions`` and the shape of every row in
``administration.roles.permissions`` — i.e. it would modify the live Role
Management screen. So the rating keys live here, and the per-role matrix lives
in ``rating.role_permissions``, keyed by the *existing* role slug. The
administration schema is never written.

Resolution order for a signed-in user:
    rating.role_permissions[role_slug]  →  DEFAULT_ROLE_PERMISSIONS[role_slug]
    →  a deny-all matrix (unknown role)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

PermAction = Literal["view", "edit"]


class RoleSlug(StrEnum):
    """Mirrors the role slugs owned by ra_backend (administration.roles.id)."""

    ADMIN = "admin"
    RA_LEAD = "ra_lead"
    ANALYST = "analyst"
    VIEWER = "viewer"


class RatingPermKey(StrEnum):
    #: Rating overview + KPI tiles.
    DASHBOARD = "ratingDashboard"
    #: Canonical metadata: products, offers, tariffs, zones, time bands, taxes.
    CATALOG = "ratingCatalog"
    #: Author / edit / version tariff rules.
    RULES = "ratingRules"
    #: Review and approve rules (maker-checker — deliberately separate from RULES).
    APPROVALS = "ratingApprovals"
    #: Compile and publish executable snapshots.
    SNAPSHOTS = "ratingSnapshots"
    #: CDR batches, rating runs, calculation traces.
    RUNS = "ratingRuns"
    #: Simulation and what-if comparison.
    SIMULATION = "ratingSimulation"
    #: Exceptions, root cause, replay, revenue recovery.
    EXCEPTIONS = "ratingExceptions"


PERMISSION_CATALOG: list[tuple[RatingPermKey, str, str]] = [
    (RatingPermKey.DASHBOARD, "Rating Overview", "/rating"),
    (RatingPermKey.CATALOG, "Metadata Catalogue", "/rating/catalog"),
    (RatingPermKey.RULES, "Rule Management", "/rating/rules"),
    (RatingPermKey.APPROVALS, "Rule Approvals", "/rating/approvals"),
    (RatingPermKey.SNAPSHOTS, "Rule Snapshots", "/rating/snapshots"),
    (RatingPermKey.RUNS, "Rating Runs", "/rating/runs"),
    (RatingPermKey.SIMULATION, "Rating Simulation", "/rating/simulation"),
    (RatingPermKey.EXCEPTIONS, "Exceptions & Recovery", "/rating/exceptions"),
]

PermissionMap = dict[str, dict[str, bool]]


def _all(view: bool, edit: bool) -> PermissionMap:
    return {k.value: {"view": view, "edit": edit} for k in RatingPermKey}


def deny_all() -> PermissionMap:
    return _all(False, False)


#: Defaults per existing role. Maker-checker is the reason RA_LEAD gets
#: APPROVALS+edit while ANALYST (the maker) does not: an analyst can author and
#: submit a rule but cannot approve their own work.
DEFAULT_ROLE_PERMISSIONS: dict[str, PermissionMap] = {
    RoleSlug.ADMIN: _all(True, True),
    RoleSlug.RA_LEAD: {
        RatingPermKey.DASHBOARD: {"view": True, "edit": False},
        RatingPermKey.CATALOG: {"view": True, "edit": True},
        RatingPermKey.RULES: {"view": True, "edit": True},
        RatingPermKey.APPROVALS: {"view": True, "edit": True},
        RatingPermKey.SNAPSHOTS: {"view": True, "edit": True},
        RatingPermKey.RUNS: {"view": True, "edit": True},
        RatingPermKey.SIMULATION: {"view": True, "edit": True},
        RatingPermKey.EXCEPTIONS: {"view": True, "edit": True},
    },
    RoleSlug.ANALYST: {
        RatingPermKey.DASHBOARD: {"view": True, "edit": False},
        RatingPermKey.CATALOG: {"view": True, "edit": False},
        RatingPermKey.RULES: {"view": True, "edit": True},
        RatingPermKey.APPROVALS: {"view": True, "edit": False},
        RatingPermKey.SNAPSHOTS: {"view": True, "edit": False},
        RatingPermKey.RUNS: {"view": True, "edit": False},
        RatingPermKey.SIMULATION: {"view": True, "edit": True},
        RatingPermKey.EXCEPTIONS: {"view": True, "edit": True},
    },
    RoleSlug.VIEWER: {
        RatingPermKey.DASHBOARD: {"view": True, "edit": False},
        RatingPermKey.CATALOG: {"view": True, "edit": False},
        RatingPermKey.RULES: {"view": True, "edit": False},
        RatingPermKey.APPROVALS: {"view": False, "edit": False},
        RatingPermKey.SNAPSHOTS: {"view": True, "edit": False},
        RatingPermKey.RUNS: {"view": True, "edit": False},
        RatingPermKey.SIMULATION: {"view": False, "edit": False},
        RatingPermKey.EXCEPTIONS: {"view": True, "edit": False},
    },
}


def default_permissions_for(role: str) -> PermissionMap:
    return DEFAULT_ROLE_PERMISSIONS.get(role, deny_all())


def normalize(perms: PermissionMap | None, role: str) -> PermissionMap:
    """Fill any key missing from a stored matrix from the role's defaults.

    Stored matrices are edited by hand/API over time; without this a newly added
    RatingPermKey would read as ``None`` and silently deny.
    """
    base = default_permissions_for(role)
    if not perms:
        return base
    return {k.value: perms.get(k.value) or base[k.value] for k in RatingPermKey}


def has_permission(perms: PermissionMap, key: RatingPermKey, action: PermAction) -> bool:
    entry = perms.get(key.value)
    return bool(entry and entry.get(action, False))
