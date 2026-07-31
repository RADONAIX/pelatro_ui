"""A snapshot, out of the system and into a file.

Three formats because three audiences ask for it: an analyst opens CSV, an
integration reads JSON, and an operator's compliance team still receives XML.

What is exported is the **compiled** form — the rules exactly as the engine will
walk them, with their dimensions, predicates and actions resolved. Exporting the
authored form instead would produce a file that reads like the rules but does not
say what will happen, and the entire point of exporting a snapshot is to be able
to hand somebody the answer to "what was rating on the 14th".

Money and dates are written as text in their canonical form. A CSV that renders
0.012345 as 1.2345E-2 because a spreadsheet guessed at the type has lost the
thing it was exported to preserve.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.compiler.models import ExecutableRule, RuleSnapshot

#: Flat columns for the tabular export, in the order an analyst reads them:
#: what the rule is, then when it fires, then what it does.
CSV_COLUMNS: tuple[str, ...] = (
    "rule_key", "rule_name", "rule_version", "rule_type", "execution_stage",
    "stage_order", "priority", "specificity", "stacking_policy", "conflict_group",
    "service_type", "product_code", "offer_code", "tariff_plan_code",
    "destination_zone", "origin_zone", "time_band", "account_type", "network_type",
    "rating_group", "roaming", "on_net", "effective_from", "effective_to",
    "currency_code", "actions", "predicates", "signature",
)


async def _rules(db: AsyncSession, snapshot_id: str) -> list[ExecutableRule]:
    return list(
        (
            await db.execute(
                select(ExecutableRule)
                .where(ExecutableRule.snapshot_id == snapshot_id)
                .order_by(
                    ExecutableRule.stage_order,
                    ExecutableRule.specificity.desc(),
                    ExecutableRule.priority.desc(),
                    ExecutableRule.rule_key,
                )
            )
        )
        .scalars()
        .all()
    )


def _scalar(value: Any) -> str:
    """One cell, in a form that survives a round trip through a spreadsheet."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


async def to_csv(db: AsyncSession, snapshot: RuleSnapshot) -> str:
    """One row per compiled rule.

    Nested predicates and actions are JSON inside their cell rather than being
    spread across columns: a rule with three actions and a rule with one must
    occupy the same shape, or the file cannot be read by anything that expects a
    table.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for rule in await _rules(db, snapshot.id):
        writer.writerow([_scalar(getattr(rule, column, None)) for column in CSV_COLUMNS])
    return buffer.getvalue()


async def to_json(db: AsyncSession, snapshot: RuleSnapshot) -> dict[str, Any]:
    """The snapshot and its rules, nested.

    Carries the checksum, because a JSON export that cannot be tied back to the
    snapshot it came from is a document with no provenance — and provenance is
    what an assurance platform exports for.
    """
    rules = await _rules(db, snapshot.id)
    return {
        "snapshot": {
            "id": snapshot.id,
            "version": snapshot.version,
            "name": snapshot.name,
            "description": snapshot.description,
            "status": snapshot.status,
            "checksum": snapshot.checksum,
            "rule_count": snapshot.rule_count,
            "effective_from": _scalar(snapshot.effective_from),
            "effective_to": _scalar(snapshot.effective_to),
            "compiled_by": snapshot.compiled_by_name,
            "compiled_at": _scalar(snapshot.created_at),
            "activated_at": _scalar(snapshot.activated_at),
        },
        "exported_at": datetime.now().astimezone().isoformat(),
        "rules": [
            {
                "rule_key": r.rule_key,
                "rule_name": r.rule_name,
                "rule_version": r.rule_version,
                "rule_type": r.rule_type,
                "execution_stage": r.execution_stage,
                "stage_order": r.stage_order,
                "priority": r.priority,
                "specificity": r.specificity,
                "stacking_policy": r.stacking_policy,
                "conflict_group": r.conflict_group,
                "match": {
                    "service_type": r.service_type,
                    "product_code": r.product_code,
                    "offer_code": r.offer_code,
                    "tariff_plan_code": r.tariff_plan_code,
                    "destination_zone": r.destination_zone,
                    "origin_zone": r.origin_zone,
                    "time_band": r.time_band,
                    "account_type": r.account_type,
                    "network_type": r.network_type,
                    "rating_group": r.rating_group,
                    "roaming": r.roaming,
                    "on_net": r.on_net,
                    "dimension_sets": r.dimension_sets,
                },
                "predicates": r.predicates,
                "actions": r.actions,
                "effective_from": _scalar(r.effective_from),
                "effective_to": _scalar(r.effective_to),
                "currency_code": r.currency_code,
                "signature": r.signature,
            }
            for r in rules
        ],
    }


async def to_xml(db: AsyncSession, snapshot: RuleSnapshot) -> str:
    """The same content as an XML document.

    Structured as a tree rather than flattened into attributes, because a
    compliance archive is read by people and by XSLT, and both cope better with
    `<Actions><Action/></Actions>` than with an attribute holding JSON.
    """
    payload = await to_json(db, snapshot)
    root = ET.Element("RuleSnapshot")

    meta = ET.SubElement(root, "Snapshot")
    for key, value in payload["snapshot"].items():
        ET.SubElement(meta, _tag(key)).text = _scalar(value)
    ET.SubElement(root, "ExportedAt").text = payload["exported_at"]

    rules = ET.SubElement(root, "Rules", count=str(len(payload["rules"])))
    for entry in payload["rules"]:
        node = ET.SubElement(rules, "Rule")
        for key in ("rule_key", "rule_name", "rule_version", "rule_type",
                    "execution_stage", "stage_order", "priority", "specificity",
                    "stacking_policy", "conflict_group", "effective_from",
                    "effective_to", "currency_code", "signature"):
            ET.SubElement(node, _tag(key)).text = _scalar(entry.get(key))

        match = ET.SubElement(node, "Match")
        for key, value in (entry.get("match") or {}).items():
            if key == "dimension_sets":
                sets = ET.SubElement(match, "DimensionSets")
                for dimension, values in (value or {}).items():
                    element = ET.SubElement(sets, _tag(dimension))
                    for item in values:
                        ET.SubElement(element, "Value").text = _scalar(item)
                continue
            ET.SubElement(match, _tag(key)).text = _scalar(value)

        actions = ET.SubElement(node, "Actions")
        for action in entry.get("actions") or []:
            element = ET.SubElement(actions, "Action")
            for key, value in (action or {}).items():
                ET.SubElement(element, _tag(key)).text = _scalar(value)

        predicates = ET.SubElement(node, "Predicates")
        for predicate in entry.get("predicates") or []:
            element = ET.SubElement(predicates, "Predicate")
            for key, value in (predicate or {}).items():
                ET.SubElement(element, _tag(key)).text = _scalar(value)

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def _tag(name: str) -> str:
    """`rule_key` → `RuleKey`. XML element names are conventionally PascalCase,
    and a document full of snake_case reads as a JSON dump wearing brackets."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in str(name))
    return "".join(part.capitalize() for part in cleaned.split()) or "Value"


def filename(snapshot: RuleSnapshot, extension: str) -> str:
    """`snapshot-v11-2026-07-30.csv` — sortable, and says what it is."""
    stamp = (snapshot.created_at or datetime.now().astimezone()).date().isoformat()
    return f"snapshot-v{snapshot.version}-{stamp}.{extension}"
