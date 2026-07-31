"""The single writer, against a real Postgres.

The headline test is the round trip: a draft written and read back must be the same
draft. A writer you cannot reverse is a writer whose bugs surface in production six
months later, when someone asks why the stored rate disagrees with the vendor's
document — and by then the raw file is gone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.rules.canonical import fingerprint, projection, reader, specificity
from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftValidity,
    FieldProvenance,
    Provenance,
)
from app.modules.rules.canonical.lineage import CanonicalRuleAudit, RuleSourceLineage
from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.rule import CanonicalRuleVersion
from app.modules.rules.ingest import resolver, writer
from app.modules.rules.vocabulary.sync import sync_vocabulary
from app.modules.rules.vocabulary.values import ValueType

pytestmark = pytest.mark.asyncio

TENANT = settings.default_tenant_id
ACTOR = ("11111111-1111-1111-1111-111111111111", "Test Author")


async def _cache(db) -> resolver.ResolutionCache:
    from sqlalchemy import text

    present = (
        await db.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'ra_rule'")
        )
    ).scalar_one()
    if not present:
        pytest.skip("ra_rule is not migrated (run alembic upgrade head)")
    await sync_vocabulary(db)
    await db.flush()
    return await resolver.build(db, TENANT)


def _base_tariff(**overrides) -> CanonicalDraft:
    """A realistic peak on-net voice tariff: the shape most rules actually take."""
    defaults = dict(
        rule_name="Peak on-net voice",
        charging_mode="PREPAID",
        rule_type_code="BASE_TARIFF",
        service_type="VOICE",
        validity=DraftValidity(effective_from=date(2026, 1, 1), currency_code="GBP"),
        root_group=DraftConditionGroup(
            logic="AND",
            conditions=(
                DraftCondition("service_type", "EQUALS", ("VOICE",)),
                DraftCondition("on_net", "EQUALS", (True,)),
                DraftCondition("duration_seconds", "GREATER_THAN", ("0",)),
            ),
        ),
        actions=(
            DraftAction(
                "SET_RATE",
                parameters=(
                    DraftParameter("rate", "0.012345", ValueType.MONEY, currency="GBP"),
                    DraftParameter("unit", "MINUTE", ValueType.ENUM),
                    DraftParameter("per_units", "1", ValueType.NUMBER),
                ),
            ),
        ),
        behaviour=DraftBehaviour(priority=200),
    )
    defaults.update(overrides)
    return CanonicalDraft(**defaults)


# --- Round trip -------------------------------------------------------------


async def test_a_written_draft_reads_back_identically(db_session):
    cache = await _cache(db_session)
    draft = _base_tariff()
    result = await writer.write(
        db_session, draft, cache=cache, rule_key="TEST_RT_BASE",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()

    back = await reader.read_draft(db_session, result.version.rule_version_id)

    assert back.rule_name == draft.rule_name
    assert back.charging_mode == draft.charging_mode
    assert back.rule_type_code == draft.rule_type_code
    assert back.service_type == draft.service_type
    assert back.behaviour.priority == draft.behaviour.priority
    assert back.behaviour.execution_mode == draft.behaviour.execution_mode
    assert back.validity.effective_from == draft.validity.effective_from
    assert back.validity.currency_code == "GBP"

    assert len(back.conditions) == len(draft.conditions)
    assert {c.attribute for c in back.conditions} == {
        c.attribute for c in draft.conditions
    }
    assert back.action_types == draft.action_types
    await db_session.rollback()


async def test_the_round_trip_preserves_the_behaviour_hash(db_session):
    """The strongest form of the losslessness claim: if the hash of the read-back
    draft matches, nothing behaviourally significant was lost or reordered."""
    cache = await _cache(db_session)
    draft = _base_tariff()
    result = await writer.write(
        db_session, draft, cache=cache, rule_key="TEST_RT_HASH",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()
    back = await reader.read_draft(db_session, result.version.rule_version_id)
    assert fingerprint.behaviour_hash(back) == result.behaviour_hash
    await db_session.rollback()


async def test_nested_condition_groups_survive_the_round_trip(db_session):
    """``(service = VOICE) AND ((zone = LOCAL AND band = PEAK) OR (roaming = yes))``
    — two levels, which the legacy flat ``group_index`` could just express, and a
    third that it could not."""
    cache = await _cache(db_session)
    draft = _base_tariff(
        rule_name="Nested predicate rule",
        root_group=DraftConditionGroup(
            logic="AND",
            conditions=(DraftCondition("service_type", "EQUALS", ("VOICE",)),),
            children=(
                DraftConditionGroup(
                    logic="OR",
                    label="peak local, or roaming",
                    children=(
                        DraftConditionGroup(
                            logic="AND",
                            conditions=(
                                DraftCondition("time_band", "EQUALS", ("PEAK",)),
                                DraftCondition("on_net", "EQUALS", (True,)),
                            ),
                        ),
                        DraftConditionGroup(
                            logic="AND",
                            negated=True,
                            conditions=(DraftCondition("roaming", "EQUALS", (True,)),),
                        ),
                    ),
                ),
            ),
        ),
    )
    result = await writer.write(
        db_session, draft, cache=cache, rule_key="TEST_RT_NESTED",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()

    back = await reader.read_draft(db_session, result.version.rule_version_id)
    assert back.condition_depth == draft.condition_depth == 3
    assert len(back.conditions) == 4
    # The negation is on the group, not on its condition.
    negated = [g for g in back.root_group.walk() if g.negated]
    assert len(negated) == 1
    assert back.root_group.children[0].logic == "OR"
    assert back.root_group.children[0].label == "peak local, or roaming"
    await db_session.rollback()


async def test_nesting_beyond_the_cap_is_refused(db_session):
    cache = await _cache(db_session)
    deep = DraftConditionGroup(conditions=(DraftCondition("on_net", "EQUALS", (True,)),))
    for _ in range(writer.MAX_GROUP_DEPTH):
        deep = DraftConditionGroup(children=(deep,))
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, _base_tariff(root_group=deep), cache=cache,
            rule_key="TEST_TOO_DEEP", actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
    assert "nested" in str(exc.value)
    await db_session.rollback()


# --- Money ------------------------------------------------------------------


async def test_money_reaches_the_column_as_an_exact_decimal(db_session):
    """The whole point of R2. 0.012345 must be 0.012345 in numeric(20,6), not
    0.01234499999999999."""
    cache = await _cache(db_session)
    result = await writer.write(
        db_session, _base_tariff(), cache=cache, rule_key="TEST_MONEY",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(RuleParameter).where(
                RuleParameter.rule_version_id == result.version.rule_version_id,
                RuleParameter.parameter_name == "rate",
            )
        )
    ).scalar_one()
    assert row.parameter_value_numeric == Decimal("0.012345")
    assert isinstance(row.parameter_value_numeric, Decimal)
    assert row.currency_code == "GBP"
    assert row.parameter_value == "0.012345"

    action = (
        await db_session.execute(
            select(RuleActionRow).where(
                RuleActionRow.rule_version_id == result.version.rule_version_id
            )
        )
    ).scalar_one()
    # The rate is promoted to the action's principal value, so "every rule whose
    # rate is above X" is an indexed numeric comparison.
    assert action.action_value_numeric == Decimal("0.012345")
    assert action.target_attribute == "charge"
    await db_session.rollback()


async def test_money_without_a_currency_anywhere_is_refused(db_session):
    cache = await _cache(db_session)
    draft = _base_tariff(
        validity=DraftValidity(effective_from=date(2026, 1, 1), currency_code=None),
        actions=(
            DraftAction(
                "SET_RATE",
                parameters=(
                    DraftParameter("rate", "0.01", ValueType.MONEY),
                    DraftParameter("unit", "MINUTE", ValueType.ENUM),
                ),
            ),
        ),
    )
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, draft, cache=cache, rule_key="TEST_NO_CCY",
            actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
    assert "currency" in str(exc.value).lower()
    await db_session.rollback()


async def test_a_monetary_parameter_inherits_the_versions_currency(db_session):
    """Most tariff sheets state their currency once, in a header. Rejecting every
    row for not repeating it would fail almost every real import."""
    cache = await _cache(db_session)
    draft = _base_tariff(
        actions=(
            DraftAction(
                "SET_RATE",
                parameters=(
                    DraftParameter("rate", "0.05", ValueType.MONEY),  # no currency
                    DraftParameter("unit", "MINUTE", ValueType.ENUM),
                ),
            ),
        ),
    )
    result = await writer.write(
        db_session, draft, cache=cache, rule_key="TEST_CCY_INHERIT",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(RuleParameter).where(
                RuleParameter.rule_version_id == result.version.rule_version_id,
                RuleParameter.parameter_name == "rate",
            )
        )
    ).scalar_one()
    assert row.currency_code == "GBP"
    await db_session.rollback()


# --- Identity and versioning ------------------------------------------------


async def test_a_second_write_of_the_same_key_cuts_version_two(db_session):
    cache = await _cache(db_session)
    first = await writer.write(
        db_session, _base_tariff(), cache=cache, rule_key="TEST_VERSIONING",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()
    second = await writer.write(
        db_session, _base_tariff(rule_name="Peak on-net voice (repriced)"),
        cache=cache, rule_key="TEST_VERSIONING",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()

    assert second.created_rule is False
    assert first.rule.rule_id == second.rule.rule_id
    assert second.version.version_number == 2
    assert second.version.supersedes_id == first.version.rule_version_id
    # The rule points at the newest version, drafts included: the catalogue shows
    # one row per logical rule and it must reflect what is being worked on.
    assert second.rule.current_version_id == second.version.rule_version_id
    # A rename is an update to the same logical rule, never a new one.
    assert second.rule.rule_name == "Peak on-net voice (repriced)"
    await db_session.rollback()


async def test_a_rename_does_not_change_the_behaviour_hash(db_session):
    """A vendor renaming a plan is not a tariff change. Treating it as one versions
    the whole estate nightly and buries the three changes that mattered."""
    cache = await _cache(db_session)
    original = _base_tariff()
    renamed = _base_tariff(rule_name="PEAK ONNET VOICE v2", description="reworded")
    assert fingerprint.behaviour_hash(original) == fingerprint.behaviour_hash(renamed)
    del cache


async def test_a_rate_change_of_one_millionth_changes_the_hash(db_session):
    cache = await _cache(db_session)
    original = _base_tariff()
    repriced = _base_tariff(
        actions=(
            DraftAction(
                "SET_RATE",
                parameters=(
                    DraftParameter("rate", "0.012346", ValueType.MONEY, currency="GBP"),
                    DraftParameter("unit", "MINUTE", ValueType.ENUM),
                    DraftParameter("per_units", "1", ValueType.NUMBER),
                ),
            ),
        )
    )
    assert fingerprint.behaviour_hash(original) != fingerprint.behaviour_hash(repriced)
    del cache


async def test_reordering_anded_conditions_does_not_change_the_hash(db_session):
    """The order two ANDed predicates were typed in cannot affect a charge, so a
    reordering must not version the estate."""
    forward = _base_tariff()
    reversed_ = _base_tariff(
        root_group=DraftConditionGroup(
            logic="AND",
            conditions=tuple(reversed(forward.root_group.conditions)),
        )
    )
    assert fingerprint.behaviour_hash(forward) == fingerprint.behaviour_hash(reversed_)


def test_the_natural_key_ignores_name_and_price():
    """Identity comes from what a rule *does*. A rename keeps it; a re-price keeps
    it; a different predicate does not."""
    base = _base_tariff()
    renamed = _base_tariff(rule_name="Something else entirely")
    repriced = _base_tariff(behaviour=DraftBehaviour(priority=999))
    different = _base_tariff(
        root_group=DraftConditionGroup(
            conditions=(DraftCondition("service_type", "EQUALS", ("SMS",)),)
        )
    )
    assert fingerprint.natural_key(base) == fingerprint.natural_key(renamed)
    assert fingerprint.natural_key(base) == fingerprint.natural_key(repriced)
    assert fingerprint.natural_key(base) != fingerprint.natural_key(different)


# --- Guarantees the writer refuses to break ---------------------------------


async def test_a_write_without_an_actor_is_refused(db_session):
    """An audit entry with no author cannot answer the only question anyone ever
    asks of it."""
    cache = await _cache(db_session)
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, _base_tariff(), cache=cache, rule_key="TEST_NO_ACTOR",
            actor_id=None, actor_name="",
        )
    assert "actor" in str(exc.value)
    await db_session.rollback()


async def test_a_rule_with_no_actions_is_refused(db_session):
    cache = await _cache(db_session)
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, _base_tariff(actions=()), cache=cache,
            rule_key="TEST_NO_ACTIONS", actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
    assert "at least one action" in str(exc.value)
    await db_session.rollback()


async def test_an_unknown_attribute_is_refused_with_its_path(db_session):
    cache = await _cache(db_session)
    draft = _base_tariff(
        root_group=DraftConditionGroup(
            conditions=(DraftCondition("lunar_phase", "EQUALS", ("WAXING",)),)
        )
    )
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, draft, cache=cache, rule_key="TEST_BAD_ATTR",
            actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
    assert "lunar_phase" in str(exc.value)
    await db_session.rollback()


async def test_an_unregistered_source_system_is_refused(db_session):
    """A rule whose origin cannot be identified has no lineage, and the estate loses
    the ability to say where its prices came from."""
    cache = await _cache(db_session)
    draft = _base_tariff(
        provenance=Provenance(channel="CONNECTOR", source_system_code="NOT_A_SOURCE")
    )
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, draft, cache=cache, rule_key="TEST_BAD_SOURCE",
            actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
    assert "NOT_A_SOURCE" in str(exc.value)
    await db_session.rollback()


async def test_an_unknown_conflict_group_is_refused(db_session):
    """Silently ignoring it would leave the rule non-exclusive, which is the exact
    opposite of what the author asked for."""
    cache = await _cache(db_session)
    draft = _base_tariff(behaviour=DraftBehaviour(conflict_group="NO_SUCH_GROUP"))
    with pytest.raises(writer.WriteError) as exc:
        await writer.write(
            db_session, draft, cache=cache, rule_key="TEST_BAD_GROUP",
            actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
    assert "non-exclusive" in str(exc.value)
    await db_session.rollback()


# --- Specificity ------------------------------------------------------------


async def test_specificity_matches_the_legacy_scorer(db_session):
    """The R4 backfill must not renumber the existing estate, so the canonical
    scorer has to agree with the legacy one on the operators it supported."""
    from app.modules.rules.schemas import ConditionIn
    from app.modules.rules.service import compute_specificity as legacy_compute

    cases = (
        (("product", "EQUALS", ["PREPAID_A"]),),
        (("destination_zone", "IN", ["LOCAL", "NATIONAL", "INTL"]),),
        (("duration_seconds", "BETWEEN", ["0", "60"]),),
        (("service_type", "NOT_EQUALS", ["SMS"]),),
        (
            ("product", "EQUALS", ["PREPAID_A"]),
            ("destination_zone", "EQUALS", ["LOCAL"]),
            ("time_band", "EQUALS", ["PEAK"]),
        ),
    )
    for case in cases:
        canonical = specificity.compute(
            tuple(DraftCondition(a, o, tuple(v)) for a, o, v in case)
        )
        legacy = legacy_compute(
            [ConditionIn(attribute=a, operator=o, values=v) for a, o, v in case]
        )
        assert canonical == legacy, case
    del db_session


def test_specificity_derivation_is_explainable():
    """A bare 130 is a number an author has to trust; "product 50 + zone 45 + band
    35" is one they can act on."""
    root = DraftConditionGroup(
        conditions=(
            DraftCondition("product", "EQUALS", ("PREPAID_A",)),
            DraftCondition("destination_zone", "EQUALS", ("LOCAL",)),
            DraftCondition("time_band", "EQUALS", ("PEAK",)),
        )
    )
    explained = specificity.explain(root)
    assert explained["total"] == 50 + 45 + 35
    assert len(explained["contributions"]) == 3
    assert {c["label"] for c in explained["contributions"]} == {
        "Product", "Destination Zone", "Time Band"
    }


def test_an_in_over_many_values_scores_less_than_an_exact_match():
    """An IN over twenty zones is barely narrower than "any zone" and must not
    outrank a rule that pins one."""
    exact = specificity.compute((DraftCondition("destination_zone", "EQUALS", ("LOCAL",)),))
    broad = specificity.compute(
        (DraftCondition("destination_zone", "IN", tuple(f"Z{i}" for i in range(20))),)
    )
    assert broad < exact


def test_existence_checks_score_nothing():
    assert specificity.compute((DraftCondition("apn", "EXISTS", ()),)) == 0


# --- Projection, lineage, audit ---------------------------------------------


async def test_the_projection_agrees_with_the_normalized_rows(db_session):
    """The projection is denormalized, so the risk is silent drift. This is the
    check the nightly re-derivation job automates."""
    cache = await _cache(db_session)
    draft = _base_tariff(
        root_group=DraftConditionGroup(
            conditions=(DraftCondition("service_type", "EQUALS", ("VOICE",)),),
            children=(
                DraftConditionGroup(
                    logic="OR",
                    conditions=(
                        DraftCondition("time_band", "EQUALS", ("PEAK",)),
                        DraftCondition("on_net", "EQUALS", (True,)),
                    ),
                ),
            ),
        )
    )
    result = await writer.write(
        db_session, draft, cache=cache, rule_key="TEST_PROJECTION",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()

    stored_conditions = (
        await db_session.execute(
            select(func.count())
            .select_from(RuleConditionRow)
            .where(RuleConditionRow.rule_version_id == result.version.rule_version_id)
        )
    ).scalar_one()
    stored_groups = (
        await db_session.execute(
            select(func.count())
            .select_from(RuleConditionGroup)
            .where(RuleConditionGroup.rule_version_id == result.version.rule_version_id)
        )
    ).scalar_one()

    payload = result.version.canonical_json
    assert projection.condition_count(payload) == stored_conditions == 3
    assert stored_groups == 2
    flat = projection.flat_conditions(payload)
    assert len(flat) == 3
    assert {c["group_logic"] for c in flat} == {"AND", "OR"}
    # Money in the projection is a string: json.dumps of a Decimal either raises or
    # routes through float, which would undo the codec one layer down.
    rate = payload["actions"][0]["params"]["rate"]
    assert rate["numeric"] == "0.012345"
    assert isinstance(rate["numeric"], str)
    assert payload["behaviour"]["specificity_score"] == result.specificity_score
    assert payload["behaviour_hash"] == result.behaviour_hash
    await db_session.rollback()


async def test_lineage_is_written_even_for_a_hand_authored_rule(db_session):
    """"Typed by name at this time" is provenance too, and a lineage table with
    holes in it is one nobody trusts."""
    cache = await _cache(db_session)
    draft = _base_tariff(
        provenance=Provenance(
            channel="MANUAL",
            fields={
                "priority": FieldProvenance("wizard.step4.priority", 200, "int"),
            },
            applied_aliases={"SET_MINIMUM": "SET_MINIMUM_CHARGE"},
            unmapped={"vendorNote": "carried over from the 2025 sheet"},
        )
    )
    result = await writer.write(
        db_session, draft, cache=cache, rule_key="TEST_LINEAGE",
        actor_id=ACTOR[0], actor_name=ACTOR[1],
    )
    await db_session.flush()

    lineage = (
        await db_session.execute(
            select(RuleSourceLineage).where(
                RuleSourceLineage.rule_version_id == result.version.rule_version_id
            )
        )
    ).scalar_one()
    assert lineage.field_provenance["priority"]["source_field"] == "wizard.step4.priority"
    assert lineage.applied_aliases == {"SET_MINIMUM": "SET_MINIMUM_CHARGE"}
    # Unmapped vendor fields are surfaced, never swallowed.
    assert lineage.unmapped_fields == {"vendorNote": "carried over from the 2025 sheet"}
    assert result.version.extras == {"vendorNote": "carried over from the 2025 sheet"}
    await db_session.rollback()


async def test_every_write_leaves_an_audit_entry(db_session):
    cache = await _cache(db_session)
    result = await writer.write(
        db_session, _base_tariff(change_reason="Initial load"), cache=cache,
        rule_key="TEST_AUDIT", actor_id=ACTOR[0], actor_name=ACTOR[1],
        channel="FILE",
    )
    await db_session.flush()

    entry = (
        await db_session.execute(
            select(CanonicalRuleAudit).where(
                CanonicalRuleAudit.rule_id == result.rule.rule_id
            )
        )
    ).scalar_one()
    assert entry.action == "created"
    assert entry.actor_name == ACTOR[1]
    assert entry.channel == "FILE"
    assert entry.comment == "Initial load"
    assert entry.version_number == 1
    await db_session.rollback()


# --- Coverage across the vocabulary -----------------------------------------


async def test_every_common_rule_type_can_be_written(db_session):
    """A rule type nothing can persist is a rule type that does not exist. This
    walks the whole COMMON family, since those need no R7 catalogue."""
    cache = await _cache(db_session)
    from app.modules.rules.vocabulary import CANONICAL_RULE_TYPES, RuleCategory
    from app.modules.rules.vocabulary.actions import ACTION_BY_CODE

    written = 0
    for spec in CANONICAL_RULE_TYPES:
        if spec.rule_category != RuleCategory.COMMON:
            continue
        action_code = spec.required_action_types[0]
        action_spec = ACTION_BY_CODE[action_code]
        # Reference parameters need catalogue rows; this test is about the writer,
        # so those types are exercised by the R7 suites instead.
        if any(
            p.required and p.value_type == ValueType.REFERENCE for p in action_spec.params
        ):
            continue
        parameters = tuple(
            DraftParameter(
                p.key,
                _sample_for(p),
                p.value_type,
                currency="GBP" if p.value_type == ValueType.MONEY else None,
            )
            for p in action_spec.params
            if p.required
        )
        draft = _base_tariff(
            rule_name=f"Probe {spec.code}",
            rule_type_code=spec.code,
            charging_mode="BOTH",
            actions=(DraftAction(action_code, parameters=parameters),),
        )
        result = await writer.write(
            db_session, draft, cache=cache, rule_key=f"TEST_TYPE_{spec.code}",
            actor_id=ACTOR[0], actor_name=ACTOR[1],
        )
        assert result.version.version_number == 1, spec.code
        written += 1

    await db_session.flush()
    assert written >= 8, f"only exercised {written} common rule types"
    count = (
        await db_session.execute(
            select(func.count()).select_from(CanonicalRuleVersion).where(
                CanonicalRuleVersion.tenant_id == TENANT
            )
        )
    ).scalar_one()
    assert count >= written
    await db_session.rollback()


def _sample_for(param) -> object:
    """A plausible value for a parameter, by type."""
    if param.value_type == ValueType.ENUM:
        return param.values[0]
    if param.value_type == ValueType.BOOLEAN:
        return True
    if param.value_type in (ValueType.MONEY, ValueType.NUMBER):
        return "1"
    return "SAMPLE"
