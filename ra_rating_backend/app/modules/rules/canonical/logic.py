"""Condition groups, conditions, actions and parameters — the rule's actual logic.

The legacy model held all of this in two tables with JSONB payloads. Three things
were unexpressible as a result, and each one has cost somebody a wrong invoice
somewhere:

**Nesting.** ``group_index`` as an integer gives exactly two levels —
``(A AND B) OR (C AND D)`` and nothing deeper. ``parent_group_id`` gives a tree.

**Declared types.** A JSONB array of values leaves the engine re-inferring
"is this a number?" per CDR. ``comparison_value_type`` states it once, and the
numeric shadow column lets a report aggregate without casting text.

**Parameters as rows.** Action parameters inside a JSONB dict cannot be
constrained, indexed, or — the reason this matters — stored as ``numeric``.
Money went through ``float`` on the way in. ``rule_parameter`` ends that.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.rules.canonical.base import RULE_SCHEMA, TenantMixin, new_id

#: Money and quantities: 20 digits with 6 decimal places. Six is not arbitrary —
#: per-byte data rates in a major currency need every one of them, and rounding
#: at ingest is how a fraction of a cent per CDR becomes a reconciliation gap.
MONEY = Numeric(20, 6)

_VERSION_FK = f"{RULE_SCHEMA}.rule_version.rule_version_id"


class RuleConditionGroup(Base, TenantMixin, TimestampMixin):
    """A bracket in the condition expression.

    Depth is capped at 4 by the validator rather than the schema: deeper is
    unreadable to an author and makes the compiler's key expansion pathological,
    but that is a policy judgement and policy does not belong in a CHECK.
    """

    __tablename__ = "rule_condition_group"
    __table_args__ = (
        Index("ix_rule_condition_group_version", "rule_version_id", "sequence_number"),
        CheckConstraint(
            "parent_group_id IS DISTINCT FROM condition_group_id",
            name="ck_rule_condition_group_not_self_parent",
        ),
        {"schema": RULE_SCHEMA},
    )

    condition_group_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_version_id: Mapped[str] = mapped_column(
        ForeignKey(_VERSION_FK, ondelete="CASCADE"), nullable=False, index=True
    )
    parent_group_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_condition_group.condition_group_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: AND / OR between the members of this group.
    group_logic: Mapped[str] = mapped_column(
        String(4), default="AND", server_default="AND", nullable=False
    )
    #: NOT applied to the whole group — impossible in the legacy model, and the
    #: natural way to say "anything except these three zones".
    negated_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: Author's label, e.g. "peak weekday window". Shown in the builder and in the
    #: human-readable summary, so a 12-condition rule stays explicable.
    label: Mapped[str] = mapped_column(String(128), default="", server_default="", nullable=False)


class RuleConditionRow(Base, TenantMixin, TimestampMixin):
    """One ``attribute operator value`` predicate."""

    __tablename__ = "rule_condition"
    __table_args__ = (
        UniqueConstraint(
            "rule_version_id", "condition_group_id", "sequence_number",
            name="uq_rule_condition_group_sequence",
        ),
        # "Which rules mention zone LOCAL_ONNET?" — an index scan, not the JSONB
        # scan the legacy model forced. This is most of why normalising was worth it.
        Index("ix_rule_condition_attr", "tenant_id", "attribute_name", "comparison_value"),
        Index("ix_rule_condition_ref", "tenant_id", "resolved_ref_id"),
        {"schema": RULE_SCHEMA},
    )

    rule_condition_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_version_id: Mapped[str] = mapped_column(
        ForeignKey(_VERSION_FK, ondelete="CASCADE"), nullable=False, index=True
    )
    condition_group_id: Mapped[str] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_condition_group.condition_group_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attribute_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_code: Mapped[str] = mapped_column(String(24), nullable=False)

    #: Canonical string form of the single/first value — always populated, so a
    #: text search over conditions needs no per-type branching.
    comparison_value: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    comparison_value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Typed elements for IN / NOT_IN / BETWEEN. Always an array, even for a single
    #: value, so the compiler consumes one shape.
    comparison_values: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    #: Typed shadow for NUMBER / MONEY — lets "every rule with a rate above X" be
    #: a numeric comparison instead of a cast.
    comparison_value_numeric: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    #: REFERENCE values store the catalogue *code* above and the resolved id here.
    #: The code survives an export moving between environments; the id gives joins.
    resolved_ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)

    sequence_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    negated_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class RuleActionRow(Base, TenantMixin, TimestampMixin):
    """One charging instruction the rule performs when it matches."""

    __tablename__ = "rule_action"
    __table_args__ = (
        UniqueConstraint(
            "rule_version_id", "execution_sequence", name="uq_rule_action_version_sequence"
        ),
        Index("ix_rule_action_type", "tenant_id", "action_type"),
        Index("ix_rule_action_target", "tenant_id", "target_attribute"),
        {"schema": RULE_SCHEMA},
    )

    rule_action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_version_id: Mapped[str] = mapped_column(
        ForeignKey(_VERSION_FK, ondelete="CASCADE"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(String(48), nullable=False)
    #: What the action writes — charge, quantity, balance, invoice_line, session…
    #: Left implicit in the action code by the legacy model, which meant nothing
    #: could answer "which rules affect the charge?" without a hard-coded list.
    target_attribute: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The action's principal value, promoted out of the parameters so a rate
    #: change is an indexed numeric comparison rather than a JSONB dig.
    action_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_value_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action_value_numeric: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    execution_sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class RuleParameter(Base, TenantMixin, TimestampMixin):
    """A typed parameter of an action, or of the rule itself.

    This table is the fix for money-as-float. ``params`` JSONB stops being the
    source of truth and survives only inside ``rule_version.canonical_json`` as a
    derived projection.
    """

    __tablename__ = "rule_parameter"
    __table_args__ = (
        UniqueConstraint(
            "rule_version_id", "rule_action_id", "parameter_name", "sequence_number",
            name="uq_rule_parameter_scope_name_sequence",
        ),
        Index("ix_rule_parameter_name", "tenant_id", "parameter_name"),
        Index("ix_rule_parameter_ref", "tenant_id", "resolved_ref_id"),
        {"schema": RULE_SCHEMA},
    )

    rule_parameter_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_version_id: Mapped[str] = mapped_column(
        ForeignKey(_VERSION_FK, ondelete="CASCADE"), nullable=False, index=True
    )
    #: NULL = a rule-level parameter rather than one belonging to an action.
    rule_action_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{RULE_SCHEMA}.rule_action.rule_action_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parameter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_value: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    parameter_value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    parameter_value_numeric: Mapped[float | None] = mapped_column(MONEY, nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    unit_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: Ordered parameters — a rate ladder is several rows under one name, in order.
    sequence_number: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0", nullable=False
    )
