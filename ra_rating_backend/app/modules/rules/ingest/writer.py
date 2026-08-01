"""The only place in this service that writes ``ra_rule.*``.

Everything — the wizard, file import, every connector — arrives here as a
:class:`CanonicalDraft` and leaves as normalized rows plus a projection, lineage and
an audit entry, all in one transaction. There is no second path, and from R3 a CI
test walks the AST to keep it that way, because documentation has never once stopped
a future connector from calling ``db.add(Rule(...))``.

Three invariants this module refuses to break:

**No write without an actor.** An audit row with a null author cannot answer the
only question anyone asks of it six months later.

**A version is immutable once it leaves DRAFT.** A change to an approved rule cuts
version N+1; it never edits in place. That is what makes a rating result from
February re-explicable in August.

**Money is ``Decimal`` from the codec to the column.** No float, no
``json.dumps`` of a Decimal, no re-parsing a formatted string.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ValidationFailedError
from app.modules.mirror import hooks as mirror_hooks
from app.modules.rules.canonical import fingerprint, projection, specificity
from app.modules.rules.canonical.base import new_id
from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
)
from app.modules.rules.canonical.lineage import (
    CanonicalRuleAudit,
    RuleSourceLineage,
)
from app.modules.rules.canonical.logic import (
    RuleActionRow,
    RuleConditionGroup,
    RuleConditionRow,
    RuleParameter,
)
from app.modules.rules.canonical.rule import CanonicalRule, CanonicalRuleVersion
from app.modules.rules.canonical.sets import RuleSetMember
from app.modules.rules.canonical.valuetypes import (
    TypedValue,
    ValueError_,
    encode,
    encode_list,
    encode_range,
)
from app.modules.rules.constants import Operator, RuleStatus
from app.modules.rules.ingest.resolver import ResolutionCache
from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY
from app.modules.rules.vocabulary.modes import ValidationState
from app.modules.rules.vocabulary.values import ValueType

#: Operators that bind no value at all.
_NULLARY: frozenset[str] = frozenset({Operator.EXISTS, Operator.NOT_EXISTS})
#: Operators whose values form a set.
_SET_OPERATORS: frozenset[str] = frozenset({Operator.IN, Operator.NOT_IN})

#: The validator caps nesting here rather than the schema doing it: deeper is
#: unreadable to an author and makes the compiler's key expansion pathological,
#: but that is a policy judgement and policy does not belong in a CHECK constraint.
MAX_GROUP_DEPTH = 4


@dataclass(slots=True)
class WriteResult:
    rule: CanonicalRule
    version: CanonicalRuleVersion
    created_rule: bool
    behaviour_hash: str
    specificity_score: int
    #: Typed values, keyed by id() of the draft object they came from. Returned so a
    #: caller (the projection check, a test, the diff view) can see exactly what was
    #: stored without re-encoding and risking a different answer.
    encoded_conditions: dict[int, TypedValue]
    encoded_parameters: dict[int, TypedValue]


class WriteError(ValidationFailedError):
    """A draft that cannot be persisted as given."""


# --- Typing ------------------------------------------------------------------


def _condition_value(cond: DraftCondition, *, path: str) -> TypedValue | None:
    """Encode one predicate's value(s) against its attribute's declared type."""
    attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
    if attr is None:
        raise WriteError(
            f"'{cond.attribute}' is not a known rating attribute.",
            details={"path": f"{path}.attribute"},
        )
    if cond.operator in _NULLARY:
        return None

    element_type = attr.data_type
    allowed = tuple(attr.values or ())
    try:
        if cond.operator in _SET_OPERATORS:
            return encode_list(
                list(cond.values), element_type, field=f"{path}.values",
                allowed=allowed, unit=cond.unit, currency=cond.currency,
            )
        if cond.operator == Operator.BETWEEN:
            if len(cond.values) != 2:
                raise WriteError(
                    f"BETWEEN expects two values, got {len(cond.values)}.",
                    details={"path": f"{path}.values"},
                )
            return encode_range(
                cond.values[0], cond.values[1], element_type,
                field=f"{path}.values", unit=cond.unit, currency=cond.currency,
            )
        if len(cond.values) != 1:
            raise WriteError(
                f"'{cond.operator}' expects one value, got {len(cond.values)}.",
                details={"path": f"{path}.values"},
            )
        return encode(
            cond.values[0], element_type, field=f"{path}.values",
            allowed=allowed, unit=cond.unit, currency=cond.currency,
        )
    except ValueError_ as exc:
        raise WriteError(exc.message, details={"path": exc.field or path}) from exc


def _parameter_value(
    param: DraftParameter, *, path: str, fallback_currency: str | None
) -> TypedValue:
    allowed: tuple[str, ...] = ()
    if param.value_type == ValueType.ENUM:
        allowed = _enum_values_for(param, path)
    try:
        return encode(
            param.raw, param.value_type, field=path, allowed=allowed,
            # A monetary parameter with no currency of its own inherits the
            # version's. Rejecting it instead would fail every tariff sheet that
            # states its currency once in a header, which is most of them.
            currency=param.currency or fallback_currency,
            unit=param.unit,
        )
    except ValueError_ as exc:
        raise WriteError(exc.message, details={"path": exc.field or path}) from exc


def _enum_values_for(param: DraftParameter, path: str) -> tuple[str, ...]:
    """Allowed values for an ENUM parameter, from the action spec that declares it."""
    del path
    for spec in ACTION_BY_CODE.values():
        for declared in spec.params:
            if declared.key == param.name and declared.value_type == ValueType.ENUM:
                return declared.values
    return ()


# --- Structure ---------------------------------------------------------------


def _check_shape(draft: CanonicalDraft) -> None:
    if not draft.rule_name.strip():
        raise WriteError("A rule name is required.", details={"path": "rule_name"})
    if draft.condition_depth > MAX_GROUP_DEPTH:
        raise WriteError(
            f"Conditions are nested {draft.condition_depth} deep; the limit is "
            f"{MAX_GROUP_DEPTH}. Deeper than that, no author can read the rule and "
            "the compiler's key expansion becomes pathological.",
            details={"path": "conditions"},
        )
    conditions = draft.conditions
    if len(conditions) > settings.max_conditions_per_rule:
        raise WriteError(
            f"A rule may have at most {settings.max_conditions_per_rule} conditions; "
            f"this one has {len(conditions)}.",
            details={"path": "conditions"},
        )
    if len(draft.actions) > settings.max_actions_per_rule:
        raise WriteError(
            f"A rule may have at most {settings.max_actions_per_rule} actions; this "
            f"one has {len(draft.actions)}.",
            details={"path": "actions"},
        )
    if not draft.actions:
        raise WriteError(
            "A rule must define at least one action. Without one it matches CDRs and "
            "changes nothing, which is indistinguishable from a broken rule.",
            details={"path": "actions"},
        )


# --- Write -------------------------------------------------------------------


async def write(
    db: AsyncSession,
    draft: CanonicalDraft,
    *,
    cache: ResolutionCache,
    rule_key: str,
    actor_id: str | None,
    actor_name: str,
    tenant_id: str | None = None,
    channel: str = "MANUAL",
    status: str = RuleStatus.DRAFT,
    validation_state: str = ValidationState.UNKNOWN,
) -> WriteResult:
    """Persist one canonical draft. Flushes; the caller owns the commit.

    A caller that wants a dry run simply does not commit — preview and commit are
    the same code path, which is the only way a preview can be trusted.
    """
    if not actor_name:
        raise WriteError(
            "A canonical write needs an actor. An audit entry with no author cannot "
            "answer the only question anyone ever asks of it."
        )
    tenant = tenant_id or cache.tenant_id
    _check_shape(draft)

    rule_type = cache.rule_types.get(draft.rule_type_code)
    if rule_type is None:
        raise WriteError(
            f"'{draft.rule_type_code}' is not a rule type in this tenant's registry.",
            details={"path": "rule_type"},
        )
    stage_id = rule_type.rule_stage_id
    stage_code = next(
        (code for code, ident in cache.stages.items() if ident == stage_id), ""
    )

    policy_id = cache.stacking.get(draft.behaviour.stacking_policy)
    if policy_id is None:
        raise WriteError(
            f"'{draft.behaviour.stacking_policy}' is not a known stacking policy.",
            details={"path": "behaviour.stacking_policy"},
        )
    conflict_group_id = (
        cache.conflict_groups.get(draft.behaviour.conflict_group)
        if draft.behaviour.conflict_group
        else None
    )
    if draft.behaviour.conflict_group and conflict_group_id is None:
        raise WriteError(
            f"Conflict group '{draft.behaviour.conflict_group}' does not exist. Create "
            "it first — an unknown group would silently make the rule non-exclusive.",
            details={"path": "behaviour.conflict_group"},
        )

    # --- Type every value before writing anything -------------------------
    # Deliberately up front: a rule half-written and then rejected on its ninth
    # condition leaves a savepoint to unwind and an author with no idea how far it got.
    encoded_conditions: dict[int, TypedValue] = {}
    for index, cond in enumerate(draft.conditions):
        typed = _condition_value(cond, path=f"conditions[{index}]")
        if typed is not None:
            encoded_conditions[id(cond)] = typed

    fallback_currency = draft.validity.currency_code
    encoded_parameters: dict[int, TypedValue] = {}
    for a_index, action in enumerate(draft.actions):
        if action.action_type not in ACTION_BY_CODE:
            raise WriteError(
                f"'{action.action_type}' is not a known action.",
                details={"path": f"actions[{a_index}].action_type"},
            )
        for param in action.parameters:
            encoded_parameters[id(param)] = _parameter_value(
                param,
                path=f"actions[{a_index}].params.{param.name}",
                fallback_currency=fallback_currency,
            )
    for param in draft.parameters:
        encoded_parameters[id(param)] = _parameter_value(
            param, path=f"parameters.{param.name}", fallback_currency=fallback_currency
        )

    # --- Resolve targets ---------------------------------------------------
    resolved = {
        "product_id": cache.catalogue_id("products", draft.targets.product),
        "offer_id": cache.catalogue_id("offers", draft.targets.offer),
        "tariff_plan_id": cache.catalogue_id("tariff-plans", draft.targets.tariff_plan),
    }
    for label, code, key in (
        ("product", draft.targets.product, "product_id"),
        ("offer", draft.targets.offer, "offer_id"),
        ("tariff plan", draft.targets.tariff_plan, "tariff_plan_id"),
    ):
        if code and resolved[key] is None:
            raise WriteError(
                f"{label.capitalize()} '{code}' does not exist in the catalogue.",
                details={"path": f"targets.{label.replace(' ', '_')}"},
            )

    source_system_id = cache.source_system_id(draft.provenance.source_system_code)
    if draft.provenance.source_system_code and source_system_id is None:
        raise WriteError(
            f"Source system '{draft.provenance.source_system_code}' is not registered. "
            "A rule whose origin cannot be identified has no lineage, and the estate "
            "loses the ability to say where its prices came from.",
            details={"path": "provenance.source_system_code"},
        )

    # --- Rule (upsert on the logical identity) ----------------------------
    existing = cache.rule_index.get(rule_key)
    created_rule = existing is None
    if created_rule:
        rule = CanonicalRule(
            tenant_id=tenant,
            rule_key=rule_key,
            rule_name=draft.rule_name,
            description=draft.description,
            charging_mode=draft.charging_mode,
            rule_type_id=rule_type.rule_type_id,
            rule_stage_id=stage_id,
            service_type=draft.service_type,
            source_system_id=source_system_id,
            external_ref=draft.provenance.external_ref,
            status=status,
            owner=draft.owner,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(rule)
        await db.flush()
        next_version = 1
    else:
        rule_id, _ = existing
        rule = await db.get(CanonicalRule, rule_id)
        if rule is None:  # pragma: no cover - the index is loaded from this table
            raise ConflictError(f"Rule '{rule_key}' vanished mid-write.")
        # A rename is an update to the same logical rule, never a new one.
        rule.rule_name = draft.rule_name
        rule.description = draft.description
        rule.rule_type_id = rule_type.rule_type_id
        rule.rule_stage_id = stage_id
        rule.updated_by = actor_id
        if draft.owner:
            rule.owner = draft.owner
        # The logical row describes its current version. The previously-live
        # version remains ACTIVE in its immutable version row until this
        # replacement is activated, so this does not interrupt rating traffic.
        rule.status = status
        next_version = await _next_version_number(db, rule.rule_id)

    # --- Version ------------------------------------------------------------
    score = specificity.compute_for_group(draft.root_group)
    hash_ = fingerprint.behaviour_hash(draft)

    version = CanonicalRuleVersion(
        tenant_id=tenant,
        rule_id=rule.rule_id,
        version_number=next_version,
        product_id=resolved["product_id"],
        offer_id=resolved["offer_id"],
        tariff_plan_id=resolved["tariff_plan_id"],
        priority=draft.behaviour.priority,
        specificity_score=score,
        stacking_policy_id=policy_id,
        conflict_group_id=conflict_group_id,
        fallback_policy=draft.behaviour.fallback_policy,
        stop_processing=draft.behaviour.stop_processing,
        execution_mode=draft.behaviour.execution_mode,
        condition_logic=draft.behaviour.condition_logic,
        effective_from=draft.validity.effective_from,
        effective_to=draft.validity.effective_to,
        currency_code=draft.validity.currency_code,
        status=status,
        change_reason=draft.change_reason,
        supersedes_id=rule.current_version_id if not created_rule else None,
        validation_state=validation_state,
        behaviour_hash=hash_,
        canonical_json={},
        extras=dict(draft.provenance.unmapped or {}),
        created_by=actor_id,
    )
    db.add(version)
    await db.flush()

    _write_groups(db, draft.root_group, version, tenant, encoded_conditions, cache)
    _write_actions(db, draft, version, tenant, encoded_parameters, cache)

    version.canonical_json = projection.build(
        draft,
        encoded_conditions=encoded_conditions,
        encoded_parameters=encoded_parameters,
        rule_key=rule_key,
        version_number=next_version,
        stage_code=stage_code,
        specificity=score,
        behaviour_hash=hash_,
        resolved=resolved,
    )

    # The rule points at its newest version. Drafts included: the catalogue shows
    # one row per logical rule, and that row must reflect what is being worked on.
    rule.current_version_id = version.rule_version_id

    _write_set_membership(db, draft, rule, version, tenant, cache)
    _write_lineage(db, draft, version, tenant, source_system_id)

    db.add(
        CanonicalRuleAudit(
            tenant_id=tenant,
            rule_id=rule.rule_id,
            rule_version_id=version.rule_version_id,
            version_number=next_version,
            action="created" if created_rule else "version_created",
            to_status=status,
            channel=channel,
            batch_id=draft.provenance.batch_id,
            actor_id=actor_id,
            actor_name=actor_name,
            comment=draft.change_reason,
            diff={} if created_rule else {"from_version": next_version - 1,
                                          "to_version": next_version},
        )
    )

    await db.flush()
    # Additive mirror. Records an id on the session and returns; the write itself
    # happens after this request's transaction commits. A no-op when disabled.
    mirror_hooks.record_rule_version(db, version.rule_version_id)
    cache.rule_index[rule_key] = (rule.rule_id, hash_)
    cache.rule_statuses[rule_key] = rule.status
    if source_system_id and draft.provenance.external_ref:
        cache.external_index[(source_system_id, draft.provenance.external_ref)] = rule_key

    return WriteResult(
        rule=rule,
        version=version,
        created_rule=created_rule,
        behaviour_hash=hash_,
        specificity_score=score,
        encoded_conditions=encoded_conditions,
        encoded_parameters=encoded_parameters,
    )


async def _next_version_number(db: AsyncSession, rule_id: str) -> int:
    from sqlalchemy import func, select

    highest = (
        await db.execute(
            select(func.max(CanonicalRuleVersion.version_number)).where(
                CanonicalRuleVersion.rule_id == rule_id
            )
        )
    ).scalar_one_or_none()
    return int(highest or 0) + 1


def _write_groups(
    db: AsyncSession,
    group: DraftConditionGroup,
    version: CanonicalRuleVersion,
    tenant: str,
    encoded: dict[int, TypedValue],
    cache: ResolutionCache,
    parent_id: str | None = None,
    sequence: int = 0,
) -> None:
    """Persist a condition tree. Recursive, depth already capped by ``_check_shape``."""
    # The id is minted here rather than left to the column default: the children
    # of this group are constructed in the same unit of work and need to carry it,
    # and a Python-side default is not applied until the row is flushed.
    group_id = new_id()
    row = RuleConditionGroup(
        condition_group_id=group_id,
        tenant_id=tenant,
        rule_version_id=version.rule_version_id,
        parent_group_id=parent_id,
        group_logic=group.logic,
        negated_flag=group.negated,
        sequence_number=sequence,
        label=group.label,
    )
    db.add(row)

    for index, cond in enumerate(group.conditions):
        typed = encoded.get(id(cond))
        attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
        ref_id = None
        if typed is not None and attr is not None and attr.data_type == ValueType.REFERENCE:
            ref_id = cache.catalogue_id(attr.reference or "", typed.text)
            if ref_id:
                typed = typed.with_ref(ref_id)
                encoded[id(cond)] = typed
        db.add(
            RuleConditionRow(
                tenant_id=tenant,
                rule_version_id=version.rule_version_id,
                condition_group_id=group_id,
                attribute_name=cond.attribute,
                operator_code=cond.operator,
                comparison_value=typed.text if typed else "",
                comparison_value_type=(
                    typed.value_type if typed else ValueType.STRING
                ),
                comparison_values=list(typed.elements) if typed else [],
                comparison_value_numeric=typed.numeric if typed else None,
                resolved_ref_id=ref_id,
                unit_code=typed.unit_code if typed else None,
                currency_code=typed.currency_code if typed else None,
                sequence_number=index,
                negated_flag=cond.negated,
            )
        )

    for child_index, child in enumerate(group.children):
        _write_groups(
            db, child, version, tenant, encoded, cache,
            parent_id=group_id, sequence=child_index,
        )


def _write_actions(
    db: AsyncSession,
    draft: CanonicalDraft,
    version: CanonicalRuleVersion,
    tenant: str,
    encoded: dict[int, TypedValue],
    cache: ResolutionCache,
) -> None:
    for sequence, action in enumerate(sorted(draft.actions, key=lambda a: a.sequence)):
        spec = ACTION_BY_CODE[action.action_type]
        by_name = {p.key: p for p in spec.params}

        principal: TypedValue | None = None
        if spec.value_param:
            principal = next(
                (
                    encoded[id(p)]
                    for p in action.parameters
                    if p.name == spec.value_param and id(p) in encoded
                ),
                None,
            )

        principal_ref = None
        if principal is not None and principal.value_type == ValueType.REFERENCE:
            declared = by_name.get(spec.value_param or "")
            principal_ref = cache.catalogue_id(
                declared.reference or "" if declared else "", principal.text
            )

        action_id = new_id()
        row = RuleActionRow(
            rule_action_id=action_id,
            tenant_id=tenant,
            rule_version_id=version.rule_version_id,
            action_type=action.action_type,
            target_attribute=action.target_attribute or spec.target_attribute,
            action_value=principal.text if principal else None,
            action_value_type=principal.value_type if principal else None,
            action_value_numeric=principal.numeric if principal else None,
            currency_code=principal.currency_code if principal else None,
            unit_code=principal.unit_code if principal else None,
            resolved_ref_id=principal_ref,
            execution_sequence=sequence,
        )
        db.add(row)

        for param in action.parameters:
            typed = encoded.get(id(param))
            if typed is None:
                continue
            declared = by_name.get(param.name)
            ref_id = None
            if typed.value_type == ValueType.REFERENCE and declared is not None:
                ref_id = cache.catalogue_id(declared.reference or "", typed.text)
                if ref_id:
                    encoded[id(param)] = typed.with_ref(ref_id)
                    typed = encoded[id(param)]
            db.add(
                RuleParameter(
                    tenant_id=tenant,
                    rule_version_id=version.rule_version_id,
                    rule_action_id=action_id,
                    parameter_name=param.name,
                    parameter_value=typed.text,
                    parameter_value_type=typed.value_type,
                    parameter_value_numeric=typed.numeric,
                    currency_code=typed.currency_code,
                    unit_code=typed.unit_code,
                    resolved_ref_id=ref_id,
                    sequence_number=param.sequence,
                )
            )

    for param in draft.parameters:
        typed = encoded.get(id(param))
        if typed is None:
            continue
        db.add(
            RuleParameter(
                tenant_id=tenant,
                rule_version_id=version.rule_version_id,
                rule_action_id=None,
                parameter_name=param.name,
                parameter_value=typed.text,
                parameter_value_type=typed.value_type,
                parameter_value_numeric=typed.numeric,
                currency_code=typed.currency_code,
                unit_code=typed.unit_code,
                sequence_number=param.sequence,
            )
        )


def _write_set_membership(
    db: AsyncSession,
    draft: CanonicalDraft,
    rule: CanonicalRule,
    version: CanonicalRuleVersion,
    tenant: str,
    cache: ResolutionCache,
) -> None:
    """Join the rule to the sets the draft names.

    Unknown set codes are skipped rather than fatal: a vendor's grouping label is
    not worth failing a tariff import over, and the batch summary reports it.
    """
    for code in draft.set_codes:
        set_id = cache.rule_sets.get(code)
        if set_id is None or (set_id, rule.rule_id) in cache.written_memberships:
            continue
        db.add(
            RuleSetMember(
                tenant_id=tenant,
                rule_set_id=set_id,
                rule_id=rule.rule_id,
                rule_version_id=version.rule_version_id,
                sequence_number=0,
            )
        )
        cache.written_memberships.add((set_id, rule.rule_id))


async def link_to_sets(
    db: AsyncSession,
    rule_id: str,
    set_codes: list[str] | tuple[str, ...],
    tenant: str,
    cache: ResolutionCache,
) -> None:
    """Join an already-written rule to the sets an import names.

    Called for rules a batch decided were UNCHANGED. Not cutting a version for
    an unchanged rule is correct — a nightly full dump would otherwise produce
    four thousand versions a day and bury the three that moved. Membership is a
    different question: "this rule was in that import" is true whether or not the
    rule changed, and it is the only thing that makes "approve everything from
    that import" mean anything.

    Skipping it produced a specific and quiet failure. Re-import an unchanged
    file, and its brand-new rule set came out empty — so the bulk approve that
    followed reported success over zero rules, which reads exactly like a batch
    that was already approved.
    """
    if not set_codes:
        return
    rule = await db.get(CanonicalRule, rule_id)
    if rule is None:
        return
    for code in set_codes:
        set_id = cache.rule_sets.get(code)
        if set_id is None or (set_id, rule_id) in cache.written_memberships:
            continue
        existing = await db.scalar(
            select(RuleSetMember.rule_set_member_id).where(
                RuleSetMember.rule_set_id == set_id,
                RuleSetMember.rule_id == rule_id,
            )
        )
        if existing is not None:
            cache.written_memberships.add((set_id, rule_id))
            continue
        db.add(
            RuleSetMember(
                tenant_id=tenant,
                rule_set_id=set_id,
                rule_id=rule_id,
                rule_version_id=rule.current_version_id,
                sequence_number=0,
            )
        )
        cache.written_memberships.add((set_id, rule_id))


def _write_lineage(
    db: AsyncSession,
    draft: CanonicalDraft,
    version: CanonicalRuleVersion,
    tenant: str,
    source_system_id: str | None,
) -> None:
    """Record where every field came from.

    Written even for a hand-authored rule: "typed by name at this time" is
    provenance too, and a lineage table with holes in it is one nobody trusts.
    """
    provenance = draft.provenance
    db.add(
        RuleSourceLineage(
            tenant_id=tenant,
            rule_version_id=version.rule_version_id,
            source_system_id=source_system_id,
            batch_id=provenance.batch_id,
            record_id=provenance.record_id,
            external_ref=provenance.external_ref,
            external_version=provenance.external_version,
            raw_hash=provenance.raw_hash,
            field_provenance={
                name: {
                    "source_field": entry.source_field,
                    "raw": _jsonable(entry.raw),
                    "transform": entry.transform,
                }
                for name, entry in (provenance.fields or {}).items()
            },
            applied_aliases=dict(provenance.applied_aliases or {}),
            unmapped_fields={
                k: _jsonable(v) for k, v in (provenance.unmapped or {}).items()
            },
            imported_at=datetime.now(UTC),
        )
    )


def _jsonable(value: Any) -> Any:
    from datetime import date

    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)
