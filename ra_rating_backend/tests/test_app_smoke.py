"""The app builds, mounts everything under /api/rating, and stays isolated."""

from __future__ import annotations

from app.core.config import settings
from app.main import create_app


def _paths() -> set[str]:
    return {r.path for r in create_app().routes if hasattr(r, "path")}


def test_app_builds_and_mounts_under_the_rating_prefix():
    paths = _paths()
    assert settings.api_prefix == "/api/rating"
    api_paths = [p for p in paths if p.startswith("/api")]
    assert api_paths, "no API routes registered"
    assert all(p.startswith("/api/rating") for p in api_paths), (
        "a route escaped the /api/rating prefix and would collide with the existing API"
    )


def test_core_endpoints_are_registered():
    paths = _paths()
    for expected in (
        "/api/rating/health",
        "/api/rating/me",
        "/api/rating/meta/attributes",
        "/api/rating/meta/operators",
        "/api/rating/meta/actions",
        "/api/rating/meta/enums",
        "/api/rating/rules",
        "/api/rating/rules/stats",
        "/api/rating/rules/{rule_id}",
        "/api/rating/rule-sets",
        "/api/rating/catalog/summary",
        "/api/rating/catalog/products",
        "/api/rating/dashboards/overview",
    ):
        assert expected in paths, f"missing route {expected}"


def test_every_catalog_entity_has_a_full_crud_surface():
    from app.modules.catalog.router import ENTITIES

    paths = _paths()
    for spec in ENTITIES:
        assert f"/api/rating/catalog/{spec.slug}" in paths, spec.slug
        assert f"/api/rating/catalog/{spec.slug}/{{entity_id}}" in paths, spec.slug


def test_rules_stats_is_matched_before_the_rule_id_wildcard():
    """`/rules/stats` must be declared first or it resolves as a rule id."""
    ordered = [r.path for r in create_app().routes if hasattr(r, "path")]
    assert ordered.index("/api/rating/rules/stats") < ordered.index("/api/rating/rules/{rule_id}")


def test_identity_connection_is_read_only():
    """The isolation guarantee: writes to `administration` are refused by Postgres."""
    from app.core.database import IDENTITY_SERVER_SETTINGS

    assert IDENTITY_SERVER_SETTINGS["default_transaction_read_only"] == "on"
    assert IDENTITY_SERVER_SETTINGS["search_path"] == settings.identity_db_schema


def test_no_writable_identity_session_is_exposed():
    from app.core import database

    assert not hasattr(database, "get_identity_session"), (
        "a writable identity session must never become a FastAPI dependency"
    )
    # get_session() — the only session dependency — is bound to the rating engine.
    assert database.SessionFactory.kw["bind"] is database.engine


def test_the_rating_schema_is_never_the_administration_schema():
    assert settings.rating_db_schema != settings.identity_db_schema


def test_clickhouse_target_is_not_the_existing_rafms_database():
    assert settings.clickhouse_database != "rafms"


def test_no_import_of_the_existing_backend():
    """This service must never take a code dependency on ra_backend."""
    import pathlib
    import re

    import_re = re.compile(r"^\s*(?:from|import)\s+.*ra_backend", re.MULTILINE)
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if import_re.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"ra_backend imported in: {offenders}"
