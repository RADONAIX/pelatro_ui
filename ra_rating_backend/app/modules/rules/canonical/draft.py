"""``CanonicalDraft`` — the only shape that reaches the writer.

The wizard builds one. The file importer builds one. Every connector builds one.
Nothing else may be persisted, and after R3 a CI test enforces that by walking the
AST for constructions of the ORM classes outside ``ingest/writer.py``.

Why a frozen dataclass rather than a Pydantic model: this is an internal contract
between adapters and the writer, not an HTTP boundary. Immutability means a draft
cannot be half-mutated by a validation pass, and the API schemas stay free to
evolve their field names without dragging the storage contract with them.

Drafts carry *codes*, not ids. The resolver turns codes into ids in one batched
pass per catalogue, so a 40,000-rule import does ~20 queries instead of 40,000.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.modules.rules.vocabulary.modes import (
    ExecutionMode,
    FallbackPolicy,
)


@dataclass(frozen=True, slots=True)
class DraftParameter:
    """One typed parameter of an action, or of the rule itself."""

    name: str
    #: Raw value as it arrived. Typed by the codec during the kernel's TYPE step,
    #: so a draft can be built before the value type is known and rejected with a
    #: precise message if the value cannot be represented.
    raw: Any
    value_type: str
    currency: str | None = None
    unit: str | None = None
    #: Ordered parameters — a rate ladder is several entries under one name.
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class DraftAction:
    action_type: str
    parameters: tuple[DraftParameter, ...] = ()
    #: Overrides the action spec's default. Rarely set; present because a vendor
    #: occasionally states a target the spec cannot infer.
    target_attribute: str | None = None
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class DraftCondition:
    attribute: str
    operator: str
    #: Always a list, even for a single-value operator, so the writer has one shape
    #: to consume and no per-operator branching.
    values: tuple[Any, ...] = ()
    negated: bool = False
    unit: str | None = None
    currency: str | None = None
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class DraftConditionGroup:
    """A bracket. ``children`` nests up to the validator's depth cap of 4."""

    logic: str = "AND"
    negated: bool = False
    label: str = ""
    sequence: int = 0
    conditions: tuple[DraftCondition, ...] = ()
    children: tuple[DraftConditionGroup, ...] = ()

    def walk(self):
        """Depth-first over this group and its descendants."""
        yield self
        for child in self.children:
            yield from child.walk()

    def all_conditions(self):
        for group in self.walk():
            yield from group.conditions

    def depth(self) -> int:
        return 1 + max((c.depth() for c in self.children), default=0)


@dataclass(frozen=True, slots=True)
class DraftBehaviour:
    """The wizard's Step 4. ``specificity`` is absent on purpose — it is computed."""

    priority: int = 100
    stacking_policy: str = "EXCLUSIVE"
    conflict_group: str | None = None
    fallback_policy: str = FallbackPolicy.FALLBACK_CHAIN
    stop_processing: bool = False
    execution_mode: str = ExecutionMode.BOTH
    condition_logic: str = "AND"


@dataclass(frozen=True, slots=True)
class DraftTargets:
    """What the version applies to, as catalogue codes."""

    product: str | None = None
    offer: str | None = None
    tariff_plan: str | None = None


@dataclass(frozen=True, slots=True)
class DraftValidity:
    effective_from: date
    effective_to: date | None = None
    currency_code: str | None = None


@dataclass(frozen=True, slots=True)
class DraftDependency:
    #: The other rule's key. Resolved to an id at write time; a dependency on a
    #: rule that does not exist yet is a deferred edge, not a failure, because a
    #: vendor export routinely lists rules in an order of its own choosing.
    depends_on_rule_key: str
    dependency_type: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DraftFallback:
    fallback_rule_key: str
    level: int
    scope: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    """Where one canonical field came from."""

    source_field: str
    raw: Any
    transform: str = ""


@dataclass(frozen=True, slots=True)
class Provenance:
    """Everything needed to explain the rule back to the team that sent it."""

    channel: str = "MANUAL"
    source_system_code: str | None = None
    external_ref: str | None = None
    external_version: str | None = None
    raw_hash: str | None = None
    batch_id: str | None = None
    record_id: str | None = None
    fields: dict[str, FieldProvenance] = field(default_factory=dict)
    #: Vendor synonyms that were substituted, so "our file said SET_MINIMUM" has an
    #: answer rather than an argument.
    applied_aliases: dict[str, str] = field(default_factory=dict)
    #: Vendor columns the profile did not map. Surfaced in the UI, never swallowed.
    unmapped: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CanonicalDraft:
    """One rule, in the only shape the writer accepts."""

    rule_name: str
    charging_mode: str
    rule_type_code: str
    service_type: str
    validity: DraftValidity
    #: None → derived deterministically by the kernel. Names never participate in
    #: identity, so a rename is an update rather than a new logical rule.
    rule_key: str | None = None
    description: str = ""
    root_group: DraftConditionGroup = field(default_factory=DraftConditionGroup)
    actions: tuple[DraftAction, ...] = ()
    parameters: tuple[DraftParameter, ...] = ()
    behaviour: DraftBehaviour = field(default_factory=DraftBehaviour)
    targets: DraftTargets = field(default_factory=DraftTargets)
    dependencies: tuple[DraftDependency, ...] = ()
    fallbacks: tuple[DraftFallback, ...] = ()
    set_codes: tuple[str, ...] = ()
    provenance: Provenance = field(default_factory=Provenance)
    owner: str | None = None
    change_reason: str = ""

    # --- Convenience -------------------------------------------------------

    @property
    def conditions(self) -> tuple[DraftCondition, ...]:
        return tuple(self.root_group.all_conditions())

    @property
    def condition_depth(self) -> int:
        return self.root_group.depth()

    @property
    def action_types(self) -> tuple[str, ...]:
        return tuple(a.action_type for a in self.actions)

    def reference_codes(self) -> dict[str, set[str]]:
        """Catalogue codes this draft mentions, grouped by catalogue slug.

        The resolver unions these across a whole batch, so existence checking is
        one query per catalogue for 40,000 rules rather than one per value.
        """
        from app.modules.rules.vocabulary.actions import ACTION_BY_CODE
        from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTE_BY_KEY
        from app.modules.rules.vocabulary.values import ValueType

        wanted: dict[str, set[str]] = {}

        def add(slug: str | None, value: Any) -> None:
            if slug and value not in (None, ""):
                wanted.setdefault(slug, set()).add(str(value).strip().upper())

        for cond in self.conditions:
            attr = CANONICAL_ATTRIBUTE_BY_KEY.get(cond.attribute)
            if attr is None or attr.data_type != ValueType.REFERENCE:
                continue
            for value in cond.values:
                add(attr.reference, value)

        for action in self.actions:
            spec = ACTION_BY_CODE.get(action.action_type)
            if spec is None:
                continue
            by_name = {p.key: p for p in spec.params}
            for param in action.parameters:
                declared = by_name.get(param.name)
                if declared is not None and declared.value_type == ValueType.REFERENCE:
                    add(declared.reference, param.raw)

        add("products", self.targets.product)
        add("offers", self.targets.offer)
        add("tariff-plans", self.targets.tariff_plan)
        if self.validity.currency_code:
            add("currencies", self.validity.currency_code)
        return wanted

    def money_currencies(self) -> set[str]:
        """Currency codes any monetary value in this draft names."""
        codes: set[str] = set()
        if self.validity.currency_code:
            codes.add(self.validity.currency_code.upper())
        for action in self.actions:
            for param in action.parameters:
                if param.currency:
                    codes.add(param.currency.upper())
                if param.name == "currency" and param.raw:
                    codes.add(str(param.raw).upper())
        return codes


def money(name: str, amount: Decimal | str | int, currency: str | None = None,
          *, sequence: int = 0) -> DraftParameter:
    """Shorthand for a monetary parameter. Kept next to the DTO so a caller cannot
    accidentally build one as a plain NUMBER and lose its currency."""
    from app.modules.rules.vocabulary.values import ValueType

    return DraftParameter(name, amount, ValueType.MONEY, currency=currency,
                          sequence=sequence)
