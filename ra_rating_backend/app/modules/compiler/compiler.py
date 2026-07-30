"""Canonical rule → executable rule.

    Validate → resolve references → compute specificity → build the lookup key
    → order by stage → precompile actions → checksum → snapshot

The output is deliberately *flat*. Everything the selection join needs is a
column; everything else is JSON evaluated only against the few candidates a
context already matched. That split is what makes the §27 scale target
reachable — the join runs once per distinct context, not once per CDR.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import models as cm
from app.modules.compiler.constants import (
    DIMENSION_BY_ATTRIBUTE,
    DIMENSION_OPERATORS,
    MATCH_DIMENSIONS,
)
from app.modules.compiler.models import ExecutableRule
from app.modules.rules.constants import STAGE_ORDER, ActionType, Operator
from app.modules.rules.models import Rule

_STAGE_INDEX = {stage: i for i, stage in enumerate(STAGE_ORDER)}

#: Action parameters that name a catalogue entity by code. Resolved at compile
#: time so the engine never has to look anything up mid-rating.
_ACTION_REFERENCES: dict[str, str] = {
    "tax_rule": "tax_rules",
    "rounding_rule": "rounding_rules",
    "discount": "discounts",
    "bundle": "bundles",
    "promotion": "promotions",
    "tariff_plan": "tariff_plans",
    "currency": "currencies",
}


class CompileError(Exception):
    """Compilation cannot proceed — reported against the snapshot, not raised
    to the caller as a 500."""


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().upper()
    if text in {"TRUE", "YES", "Y", "1"}:
        return True
    if text in {"FALSE", "NO", "N", "0"}:
        return False
    return None


def _numeric(value: Any) -> Any:
    """Normalise numbers so a rate written 0.10 and .1 checksum identically."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        return float(Decimal(str(value)))
    try:
        return float(Decimal(str(value)))
    except Exception:
        return value


async def _reference_index(db: AsyncSession) -> dict[str, dict[str, str]]:
    """catalogue slug -> {CODE: id}, for resolving action references once."""
    models: dict[str, Any] = {
        "tax_rules": cm.TaxRule,
        "rounding_rules": cm.RoundingRule,
        "discounts": cm.DiscountDefinition,
        "bundles": cm.BundleDefinition,
        "promotions": cm.Promotion,
        "tariff_plans": cm.TariffPlan,
        "currencies": cm.Currency,
        "products": cm.Product,
        "offers": cm.Offer,
    }
    index: dict[str, dict[str, str]] = {}
    for slug, model in models.items():
        rows = await db.execute(select(model.code, model.id))
        index[slug] = {code.upper(): rid for code, rid in rows.all()}
    return index


async def _code_index(db: AsyncSession) -> dict[str, dict[str, str]]:
    """catalogue slug -> {id: CODE}, for resolving a rule's header links."""
    models: dict[str, Any] = {
        "products": cm.Product,
        "offers": cm.Offer,
        "tariff_plans": cm.TariffPlan,
    }
    index: dict[str, dict[str, str]] = {}
    for slug, model in models.items():
        rows = await db.execute(select(model.id, model.code))
        index[slug] = dict(rows.all())
    return index


async def _catalog_payloads(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """Inline the values the engine needs so rating does no catalogue reads.

    A tax rate resolved at compile time is also the *correct* rate: if someone
    edits VAT tomorrow, a snapshot compiled today keeps rating at the rate it
    was published with, which is what makes a historical charge reproducible.
    """
    taxes = (await db.execute(select(cm.TaxRule))).scalars().all()
    rounding = (await db.execute(select(cm.RoundingRule))).scalars().all()
    discounts = (await db.execute(select(cm.DiscountDefinition))).scalars().all()
    bundles = (await db.execute(select(cm.BundleDefinition))).scalars().all()
    currencies = (await db.execute(select(cm.Currency))).scalars().all()
    return {
        "tax_rules": {
            t.code.upper(): {
                "code": t.code,
                "rate_percent": float(t.rate_percent),
                "inclusive": t.inclusive,
                "tax_type": t.tax_type,
            }
            for t in taxes
        },
        "rounding_rules": {
            r.code.upper(): {"code": r.code, "mode": r.mode, "decimals": r.decimals}
            for r in rounding
        },
        "discounts": {
            d.code.upper(): {
                "code": d.code,
                "discount_type": d.discount_type,
                "value": float(d.value),
                "pre_tax": d.pre_tax,
            }
            for d in discounts
        },
        "bundles": {
            b.code.upper(): {
                "code": b.code,
                "quota_unit": b.quota_unit,
                "quota_value": float(b.quota_value),
                "reset_period": b.reset_period,
                "shared": b.shared,
            }
            for b in bundles
        },
        "currencies": {
            c.code.upper(): {"code": c.code, "decimals": c.decimals, "symbol": c.symbol}
            for c in currencies
        },
    }


def _compile_conditions(
    rule: Rule, codes: dict[str, dict[str, str]] | None = None
) -> tuple[dict[str, Any], dict[str, list[str]], list[dict]]:
    """Split conditions into dimensions, dimension sets and residual predicates."""
    dimensions: dict[str, Any] = {col: None for _, col in MATCH_DIMENSIONS}
    dimension_sets: dict[str, list[str]] = {}
    predicates: list[dict] = []

    dimensions["service_type"] = rule.service_type.upper()

    # The rule header's product / offer / tariff-plan links are match dimensions
    # too. Without this a rule attached to PREPAID_A would compile with
    # product_code=* and price POSTPAID_A traffic as well — the header says
    # "this rule is for that product", and the engine has to honour it.
    codes = codes or {}
    for field_name, slug, column in (
        ("product_id", "products", "product_code"),
        ("offer_id", "offers", "offer_code"),
        ("tariff_plan_id", "tariff_plans", "tariff_plan_code"),
    ):
        entity_id = getattr(rule, field_name, None)
        if entity_id:
            code = codes.get(slug, {}).get(entity_id)
            if code:
                dimensions[column] = code.upper()

    for cond in rule.conditions:
        column = DIMENSION_BY_ATTRIBUTE.get(cond.attribute)
        values = list(cond.values or [])
        reducible = (
            column is not None
            and cond.operator in DIMENSION_OPERATORS
            and not cond.negate
            and values
            # A rule that pins a dimension twice keeps the second as a predicate
            # rather than silently dropping one of them.
            and (column not in dimension_sets)
        )
        if not reducible:
            predicates.append(
                {
                    "attribute": cond.attribute,
                    "operator": str(cond.operator),
                    "values": [_numeric(v) for v in values],
                    "negate": cond.negate,
                    "group_index": cond.group_index,
                }
            )
            continue

        assert column is not None
        if column in {"roaming", "on_net"}:
            flag = _as_bool(values[0])
            if flag is None:
                predicates.append(
                    {
                        "attribute": cond.attribute,
                        "operator": str(cond.operator),
                        "values": values,
                        "negate": cond.negate,
                        "group_index": cond.group_index,
                    }
                )
                continue
            dimensions[column] = flag
        elif cond.operator == Operator.EQUALS or len(values) == 1:
            dimensions[column] = str(values[0]).upper()
        else:
            # IN over several values: the column stays wildcard and the set is
            # checked alongside it at selection time.
            dimension_sets[column] = sorted({str(v).upper() for v in values})

    return dimensions, dimension_sets, predicates


def _compile_actions(
    rule: Rule,
    references: dict[str, dict[str, str]],
    payloads: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve references and inline catalogue values, keeping execution order."""
    out: list[dict[str, Any]] = []
    for action in sorted(rule.actions, key=lambda a: a.sequence):
        params: dict[str, Any] = {}
        resolved: dict[str, Any] = {}
        for key, value in (action.params or {}).items():
            slug = _ACTION_REFERENCES.get(key)
            if slug and isinstance(value, str):
                code = value.upper()
                params[key] = code
                entity_id = references.get(slug, {}).get(code)
                if entity_id:
                    resolved[f"{key}_id"] = entity_id
                inlined = payloads.get(slug, {}).get(code)
                if inlined is not None:
                    resolved[key] = inlined
            else:
                params[key] = _numeric(value)
        out.append(
            {
                "action_type": action.action_type,
                "params": params,
                "resolved": resolved,
            }
        )
    # SET_ZERO_CHARGE overrides everything else at its stage, so hoist it —
    # otherwise a rate action later in the list would quietly win.
    out.sort(key=lambda a: 0 if a["action_type"] == ActionType.SET_ZERO_CHARGE.value else 1)
    return out


def _signature(dimensions: dict[str, Any], dimension_sets: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for _, column in MATCH_DIMENSIONS:
        if column in dimension_sets:
            parts.append(f"{column}∈{{{','.join(dimension_sets[column])}}}")
        elif dimensions.get(column) is not None:
            parts.append(f"{column}={dimensions[column]}")
        else:
            parts.append(f"{column}=*")
    return " | ".join(parts)


def compile_rule(
    rule: Rule,
    snapshot_id: str,
    references: dict[str, dict[str, str]],
    payloads: dict[str, dict[str, Any]],
    codes: dict[str, dict[str, str]] | None = None,
) -> ExecutableRule:
    dimensions, dimension_sets, predicates = _compile_conditions(rule, codes)
    actions = _compile_actions(rule, references, payloads)
    stage_order = _STAGE_INDEX.get(rule.execution_stage)
    if stage_order is None:
        raise CompileError(
            f"Rule '{rule.rule_key}' has unknown execution stage '{rule.execution_stage}'."
        )

    return ExecutableRule(
        snapshot_id=snapshot_id,
        rule_id=rule.id,
        rule_key=rule.rule_key,
        rule_version=rule.version,
        rule_name=rule.name,
        rule_type=rule.rule_type,
        execution_stage=rule.execution_stage,
        stage_order=stage_order,
        priority=rule.priority,
        specificity=rule.specificity,
        stacking_policy=rule.stacking_policy,
        conflict_group=rule.conflict_group,
        condition_logic=rule.condition_logic,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        currency_code=rule.currency_code,
        dimension_sets=dimension_sets,
        predicates=predicates,
        actions=actions,
        signature=_signature(dimensions, dimension_sets),
        **dimensions,
    )


def checksum(executables: list[ExecutableRule]) -> str:
    """Stable hash of the compiled set.

    Deliberately excludes ids and timestamps: two compiles of the same approved
    rules must produce the same checksum, which is how a republish that changes
    nothing is recognised as a no-op.
    """
    digest = hashlib.sha256()
    for ex in sorted(executables, key=lambda e: (e.rule_key, e.rule_version)):
        payload = {
            "key": ex.rule_key,
            "version": ex.rule_version,
            "stage": ex.execution_stage,
            "priority": ex.priority,
            "specificity": ex.specificity,
            "stacking": ex.stacking_policy,
            "conflict_group": ex.conflict_group,
            "from": ex.effective_from.isoformat(),
            "to": ex.effective_to.isoformat() if ex.effective_to else None,
            "signature": ex.signature,
            "sets": ex.dimension_sets,
            "predicates": ex.predicates,
            "actions": ex.actions,
        }
        digest.update(json.dumps(payload, sort_keys=True, default=str).encode())
    return digest.hexdigest()


def build_stats(executables: list[ExecutableRule], rules: list[Rule]) -> dict[str, Any]:
    by_stage: dict[str, int] = defaultdict(int)
    by_service: dict[str, int] = defaultdict(int)
    for ex in executables:
        by_stage[ex.execution_stage] += 1
        by_service[ex.service_type or "ANY"] += 1

    products = {
        code
        for ex in executables
        for code in (
            [ex.product_code]
            if ex.product_code
            else ex.dimension_sets.get("product_code", [])
        )
    }
    wildcard_dimensions = sum(
        1
        for ex in executables
        for _, column in MATCH_DIMENSIONS
        if getattr(ex, column) is None and column not in ex.dimension_sets
    )
    return {
        "by_stage": dict(by_stage),
        "by_service_type": dict(by_service),
        "distinct_products": len(products),
        "residual_predicate_rules": sum(1 for ex in executables if ex.predicates),
        # A high wildcard ratio means broad rules, which makes each context match
        # more candidates. Useful when selection starts to slow down.
        "wildcard_dimension_ratio": round(
            wildcard_dimensions / max(1, len(executables) * len(MATCH_DIMENSIONS)), 3
        ),
        "source_rule_versions": len(rules),
    }


def snapshot_window(rules: list[Rule]) -> tuple[date, date | None]:
    """The union of the compiled rules' validity windows."""
    if not rules:
        today = date.today()
        return today, None
    start = min(r.effective_from for r in rules)
    ends = [r.effective_to for r in rules]
    end = None if any(e is None for e in ends) else max(e for e in ends if e)
    return start, end
