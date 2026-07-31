"""The canonical rule API — authoring (group 2) and validation (group 3).

Tested through the router functions rather than over HTTP, which is where the
logic actually is: FastAPI's job is to bind and serialise, and a test that goes
through the network stack mostly tests FastAPI. What matters here is that a
wizard payload reaches the kernel unchanged, that the responses carry what a UI
needs to render the catalogue and the wizard, and that the API cannot be used to
get a rule into a state the storage model forbids.

The routing test is the exception, and it earns its place: Starlette matches in
declaration order and has no regex path converter, so a literal path declared
after a path parameter silently becomes unreachable. That failure is invisible in
review and baffling from the client side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from starlette.routing import Match

from app.core.errors import RuleStateError, ValidationFailedError
from app.main import create_app
from app.modules.rules.api import router as api
from app.modules.rules.api import schemas as s
from app.modules.rules.api import service as svc
from app.modules.rules.canonical.rule import CanonicalRule
from app.modules.rules.vocabulary.sync import sync_vocabulary

pytestmark = pytest.mark.asyncio


@dataclass
class FakePrincipal:
    """Only the four attributes the router touches. A real Principal would drag
    in the identity database for no benefit."""

    id: str = "11111111-1111-1111-1111-111111111111"
    email: str = "author@example.com"
    full_name: str = "Test Author"
    role: str = "admin"


PRINCIPAL = FakePrincipal()


@dataclass
class Page:
    limit: int = 50
    offset: int = 0


#: Every filter `list_rules` accepts. Supplied explicitly because calling the
#: handler directly bypasses FastAPI's parameter resolution, so an unpassed
#: `Query(None)` default would reach the query builder as a Query object.
_FILTERS = (
    "search", "charging_mode", "service_type", "rule_type", "status",
    "product_id", "source_system_id", "snapshot_id", "validation_state",
    "execution_mode", "rule_set_id", "effective_on",
)


async def _list(db, **filters):
    return await api.list_rules(
        db, Page(), **{name: filters.get(name) for name in _FILTERS}
    )


async def _ready(db) -> None:
    present = (
        await db.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'ra_rule'")
        )
    ).scalar_one()
    if not present:
        pytest.skip("ra_rule is not migrated (run alembic upgrade head)")
    await sync_vocabulary(db)
    await db.flush()


def _payload(name: str = "API peak voice", rate: str = "0.012345", **overrides):
    """A realistic wizard payload — the shape the six-step form produces."""
    body: dict = {
        "rule_name": name,
        "charging_mode": "PREPAID",
        "rule_type_code": "BASE_TARIFF",
        "service_type": "VOICE",
        "validity": {"effective_from": "2026-01-01", "currency_code": "GBP"},
        "conditions": {
            "logic": "AND",
            "conditions": [
                {"attribute": "service_type", "operator": "EQUALS", "values": ["VOICE"]},
                {
                    "attribute": "destination_zone",
                    "operator": "EQUALS",
                    "values": ["LOCAL_ONNET"],
                },
            ],
        },
        "actions": [
            {
                "action_type": "SET_RATE",
                "parameters": [
                    {"name": "rate", "value": rate, "value_type": "MONEY",
                     "currency": "GBP"},
                    {"name": "unit", "value": "MINUTE", "value_type": "ENUM"},
                    {"name": "per_units", "value": "1", "value_type": "NUMBER"},
                ],
            }
        ],
        "behaviour": {"priority": 200},
    }
    body.update(overrides)
    return s.RuleWrite.model_validate(body)


# --- Routing ----------------------------------------------------------------


def test_literal_paths_are_not_shadowed_by_the_rule_id_parameter():
    """Starlette matches in declaration order. A literal segment declared after
    ``/{rule_id}`` never matches — ``/conflicts`` arrives as a rule id and 404s
    with a message about a missing rule, which is baffling from the client."""
    app = create_app()
    prefix = "/api/rating/canonical-rules"
    for literal in ("/key-suggestion", "/conflicts", "/issues/summary"):
        target = prefix + literal
        matched = next(
            r.path
            for r in app.routes
            if r.matches(
                {"type": "http", "method": "GET", "path": target,
                 "path_params": {}, "headers": [], "root_path": ""}
            )[0]
            == Match.FULL
        )
        assert matched == target, f"{target} is shadowed by {matched}"


def test_the_canonical_surface_is_registered():
    paths = {r.path for r in create_app().routes}
    for expected in (
        "/api/rating/canonical-rules",
        "/api/rating/canonical-rules/{rule_id}",
        "/api/rating/canonical-rules/{rule_id}/versions/{version_number}",
        "/api/rating/canonical-rules/validate",
        "/api/rating/canonical-rules/validate-batch",
        "/api/rating/canonical-rules/validate-references",
        "/api/rating/meta/pipelines",
        "/api/rating/meta/rule-types",
        "/api/rating/meta/canonical-actions",
    ):
        assert expected in paths, f"missing route {expected}"


def test_the_legacy_surface_is_untouched():
    """Both models are live during the R4 window. Taking `/rules` away would
    break the screens built against it and the compiler that reads behind it."""
    paths = {r.path for r in create_app().routes}
    for legacy in (
        "/api/rating/rules",
        "/api/rating/rules/{rule_id}",
        "/api/rating/meta/attributes",
        "/api/rating/meta/actions",
    ):
        assert legacy in paths


# --- Mapping ----------------------------------------------------------------


def test_a_wizard_payload_maps_onto_a_draft_without_loss():
    draft = svc.to_draft(_payload())
    assert draft.rule_name == "API peak voice"
    assert draft.charging_mode == "PREPAID"
    assert len(draft.conditions) == 2
    assert draft.action_types == ("SET_RATE",)
    assert draft.behaviour.priority == 200
    assert draft.validity.currency_code == "GBP"


def test_nested_condition_groups_survive_the_mapping():
    payload = _payload(
        conditions={
            "logic": "OR",
            "children": [
                {"logic": "AND", "conditions": [
                    {"attribute": "service_type", "operator": "EQUALS",
                     "values": ["VOICE"]}]},
                {"logic": "AND", "conditions": [
                    {"attribute": "service_type", "operator": "EQUALS",
                     "values": ["SMS"]}]},
            ],
        }
    )
    draft = svc.to_draft(payload)
    assert draft.root_group.logic == "OR"
    assert len(draft.root_group.children) == 2
    assert draft.condition_depth == 2


def test_the_api_refuses_a_reversed_validity_window_before_it_reaches_storage():
    with pytest.raises(ValueError, match="before effective_from"):
        _payload(validity={"effective_from": "2026-06-01", "effective_to": "2026-01-01"})


# --- Group 2: authoring -----------------------------------------------------


async def test_creating_a_rule_returns_it_fully_rendered(db_session):
    await _ready(db_session)
    response = await api.create_rule(db_session, PRINCIPAL, _payload("API create"))

    assert response.decision == "NEW"
    rule = response.rule
    assert rule.rule_name == "API create"
    assert rule.charging_mode == "PREPAID"
    assert rule.rule_type_name == "Base tariff"
    assert rule.stage_code == "BASE_CHARGE"
    assert rule.version_number == 1
    assert rule.status == "DRAFT"
    assert rule.condition_count == 2
    assert rule.action_count == 1
    # The wizard's Step 4 shows this read-only, with its working.
    assert rule.specificity is not None
    assert rule.specificity.score > 0
    assert "=" in rule.specificity.derivation


async def test_a_created_rule_carries_its_actions_and_typed_parameters(db_session):
    await _ready(db_session)
    response = await api.create_rule(db_session, PRINCIPAL, _payload("API typed"))

    action = response.rule.actions[0]
    assert action.action_type == "SET_RATE"
    assert action.label == "Set rate"
    rate = next(p for p in action.parameters if p.name == "rate")
    # Money is Decimal from the request model to the column and back.
    assert rate.numeric == Decimal("0.012345")
    assert rate.currency_code == "GBP"


async def test_saving_the_same_rule_twice_cuts_no_second_version(db_session):
    await _ready(db_session)
    first = await api.create_rule(db_session, PRINCIPAL, _payload("API unchanged"))
    second = await api.create_rule(db_session, PRINCIPAL, _payload("API unchanged"))

    assert first.decision == "NEW"
    assert second.decision == "UNCHANGED"
    assert second.rule.version_number == 1


async def test_a_mode_incoherent_rule_is_rejected_rather_than_saved(db_session):
    """A 2xx that saved nothing is a response a client checks once and then stops
    checking, so this raises."""
    await _ready(db_session)
    bad = _payload(
        "API incoherent",
        charging_mode="BOTH",
        rule_type_code="TAX",
        actions=[{
            "action_type": "DEDUCT_BALANCE",
            "parameters": [{"name": "balance_type", "value": "MAIN",
                            "value_type": "REFERENCE"}],
        }],
    )
    with pytest.raises(ValidationFailedError):
        await api.create_rule(db_session, PRINCIPAL, bad)


async def test_a_new_version_supersedes_rather_than_edits(db_session):
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API versioned"))
    rule_id = created.rule.rule_id

    updated = await api.new_version(
        db_session, rule_id, PRINCIPAL, _payload("API versioned", rate="0.02")
    )
    assert updated.decision == "CHANGED"
    assert updated.rule.version_number == 2

    versions = await api.list_versions(db_session, rule_id)
    assert [v.version_number for v in versions] == [2, 1]

    # Version 1 still says what it said — which is what makes an old rating
    # result re-explicable.
    original = await api.get_version(db_session, rule_id, 1)
    rate = next(
        p for a in original.actions for p in a.parameters if p.name == "rate"
    )
    assert rate.numeric == Decimal("0.012345")


async def test_an_explicit_new_version_is_cut_even_when_behaviour_is_unchanged(
    db_session,
):
    await _ready(db_session)
    payload = _payload("API explicit version")
    created = await api.create_rule(db_session, PRINCIPAL, payload)

    updated = await api.new_version(
        db_session, created.rule.rule_id, PRINCIPAL, payload
    )

    assert updated.decision == "CHANGED"
    assert updated.rule.version_number == 2


async def test_an_approved_rule_cannot_be_edited_in_place(db_session):
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API immutable"))
    rule_id = created.rule.rule_id
    rule = await db_session.get(CanonicalRule, rule_id)
    rule.status = "APPROVED"
    await db_session.flush()

    with pytest.raises(RuleStateError, match="cannot be edited"):
        await api.update_rule(db_session, rule_id, PRINCIPAL, _payload("API immutable"))


async def test_cloning_produces_a_new_logical_rule_at_version_one(db_session):
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API clone source"))

    clone = await api.clone_rule(
        db_session,
        created.rule.rule_id,
        PRINCIPAL,
        s.CloneRequest(rule_name="API clone target"),
    )
    assert clone.rule.rule_id != created.rule.rule_id
    assert clone.rule.rule_key != created.rule.rule_key
    assert clone.rule.version_number == 1
    assert clone.rule.rule_name == "API clone target"


async def test_the_catalogue_filters_are_all_honoured(db_session):
    await _ready(db_session)
    await api.create_rule(db_session, PRINCIPAL, _payload("API filterable"))

    hit = await _list(db_session, search="API filterable")
    assert hit.total >= 1

    by_mode = await _list(
        db_session, search="API filterable", charging_mode="PREPAID"
    )
    assert by_mode.total >= 1

    wrong_mode = await _list(
        db_session, search="API filterable", charging_mode="POSTPAID"
    )
    assert wrong_mode.total == 0

    by_type = await _list(
        db_session, search="API filterable", rule_type="BASE_TARIFF"
    )
    assert by_type.total >= 1

    live_today = await _list(
        db_session, search="API filterable", effective_on=date(2026, 6, 1)
    )
    assert live_today.total >= 1

    before_it_starts = await _list(
        db_session, search="API filterable", effective_on=date(2025, 1, 1)
    )
    assert before_it_starts.total == 0


async def test_a_key_suggestion_is_deduplicated_rather_than_rejected(db_session):
    await _ready(db_session)
    suggestion = await api.suggest_key(db_session, name="Peak On-net")
    assert suggestion.suggested == "PEAK_ON_NET"
    assert suggestion.resolved  # always usable, even when the suggestion is taken


async def test_a_status_change_is_audited(db_session):
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API audited"))
    rule_id = created.rule.rule_id

    await api.change_status(
        db_session, rule_id, PRINCIPAL,
        s.StatusChange(status="VALIDATED", comment="Looks right"),
    )
    trail = await api.audit_trail(db_session, rule_id)
    assert any(e["action"] == "status_validated" for e in trail)
    assert any(e["actor_name"] == "Test Author" for e in trail)


async def test_an_illegal_status_transition_is_refused(db_session):
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API transition"))
    with pytest.raises(RuleStateError, match="Cannot move"):
        await api.change_status(
            db_session, created.rule.rule_id, PRINCIPAL,
            s.StatusChange(status="APPROVED"),
        )


# --- Group 3: validation ----------------------------------------------------


async def test_validate_agrees_with_what_a_save_would_do(db_session):
    """Both call the same function, so a green validate followed by a failing
    save is not a state this API can reach."""
    await _ready(db_session)
    report = await api.validate_payload(db_session, PRINCIPAL, _payload("API validate"))
    assert report.valid
    assert report.validation_state == "PASS"

    saved = await api.create_rule(db_session, PRINCIPAL, _payload("API validate"))
    assert saved.validation_state == "PASS"


async def test_validate_reports_a_mode_incoherent_rule_without_saving_it(db_session):
    await _ready(db_session)
    bad = _payload(
        "API validate bad",
        charging_mode="BOTH",
        rule_type_code="TAX",
        actions=[{
            "action_type": "DEDUCT_BALANCE",
            "parameters": [{"name": "balance_type", "value": "MAIN",
                            "value_type": "REFERENCE"}],
        }],
    )
    report = await api.validate_payload(db_session, PRINCIPAL, bad)
    assert not report.valid
    assert "action_outside_pipeline" in {i.code for i in report.issues}

    listed = await _list(db_session, search="API validate bad")
    assert listed.total == 0


async def test_a_warning_does_not_block_the_save(db_session):
    """An author must be able to store a rule that is merely broad, or the
    wizard cannot be used incrementally."""
    await _ready(db_session)
    broad = _payload("API broad", conditions={"logic": "AND", "conditions": []})
    report = await api.validate_payload(db_session, PRINCIPAL, broad)
    assert report.valid
    assert report.validation_state == "WARNING"
    assert "no_conditions" in {i.code for i in report.issues}

    saved = await api.create_rule(db_session, PRINCIPAL, broad)
    assert saved.decision == "NEW"


async def test_batch_validation_groups_failures_by_cause(db_session):
    await _ready(db_session)
    broken = [
        _payload(f"API batch {n}", actions=[])
        for n in range(3)
    ]
    report = await api.validate_batch(db_session, PRINCIPAL, broken)
    assert report.total == 3
    assert report.valid_count == 0
    codes = {r["code"]: r["count"] for r in report.reasons}
    assert codes.get("no_actions") == 3


async def test_issues_are_persisted_and_drillable(db_session):
    await _ready(db_session)
    created = await api.create_rule(
        db_session, PRINCIPAL,
        _payload("API issues", conditions={"logic": "AND", "conditions": []}),
    )
    issues = await api.rule_issues(db_session, created.rule.rule_id)
    assert "no_conditions" in {i.code for i in issues}

    summary = await api.issue_summary(db_session)
    assert any(row["code"] == "no_conditions" and row["count"] >= 1 for row in summary)


async def test_reference_validation_answers_per_code(db_session):
    """Different in kind from /validate: "LOCAL_ONNET exists, MOBILE does not" is
    what an author fixes without leaving the condition row."""
    await _ready(db_session)
    payload = _payload(
        "API references",
        conditions={
            "logic": "AND",
            "conditions": [
                {"attribute": "destination_zone", "operator": "IN",
                 "values": ["LOCAL_ONNET", "NOT_A_ZONE"]},
            ],
        },
    )
    report = await api.validate_references(db_session, PRINCIPAL, payload)
    by_code = {c.code: c for c in report.references}
    assert by_code["LOCAL_ONNET"].exists
    assert by_code["LOCAL_ONNET"].resolved_id
    assert not by_code["NOT_A_ZONE"].exists
    assert by_code["NOT_A_ZONE"].message
    assert report.missing == 1


async def test_conflict_detection_finds_duplicate_behaviour(db_session):
    """Two rules that say the same thing: harmless until someone changes one and
    cannot work out why the charge did not move."""
    await _ready(db_session)
    first = await api.create_rule(db_session, PRINCIPAL, _payload("API dup one"))
    second = await api.clone_rule(
        db_session, first.rule.rule_id, PRINCIPAL,
        s.CloneRequest(rule_name="API dup two"),
    )
    for rule_id in (first.rule.rule_id, second.rule.rule_id):
        rule = await db_session.get(CanonicalRule, rule_id)
        rule.status = "APPROVED"
    await db_session.flush()

    report = await api.detect_conflicts(
        db_session, charging_mode="PREPAID", service_type=None
    )
    duplicates = [c for c in report.conflicts if c.kind == "DUPLICATE_BEHAVIOUR"]
    assert duplicates
    assert any(
        {first.rule.rule_key, second.rule.rule_key} <= set(c.rule_keys)
        for c in duplicates
    )


async def test_conflict_detection_ignores_drafts(db_session):
    """A draft cannot conflict with anything, and reporting that it does trains
    operators to ignore the report."""
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API draft only"))
    report = await api.detect_conflicts(
        db_session, charging_mode=None, service_type=None
    )
    assert all(
        created.rule.rule_key not in c.rule_keys for c in report.conflicts
    )


async def test_deleting_a_draft_is_allowed_and_an_approved_rule_is_not(db_session):
    await _ready(db_session)
    created = await api.create_rule(db_session, PRINCIPAL, _payload("API deletable"))
    rule_id = created.rule.rule_id
    await api.delete_draft(db_session, rule_id)
    await db_session.flush()
    gone = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.rule_id == rule_id)
        )
    ).scalar_one_or_none()
    assert gone is None

    kept = await api.create_rule(db_session, PRINCIPAL, _payload("API undeletable"))
    rule = await db_session.get(CanonicalRule, kept.rule.rule_id)
    rule.status = "APPROVED"
    await db_session.flush()
    with pytest.raises(RuleStateError, match="retire"):
        await api.delete_draft(db_session, kept.rule.rule_id)
