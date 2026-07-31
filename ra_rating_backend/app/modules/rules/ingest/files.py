"""Turning an uploaded file into drafts the kernel can take.

Thin by design. The *parsing* is the existing ``imports.parser`` — CSV with
delimiter sniffing and BOM handling, XLSX, JSON, XML — which is in production and
correct, and a second copy would drift. The *mapping* is the existing
``suggest_mapping``, extended with the columns only the canonical model needs.
What is new is the row-to-draft conversion, and it is new because the legacy one
produces floats.

The contract this module exists to hold: a file is read once, and every row
produces either a draft or a recorded rejection. Nothing is silently skipped. A
row that cannot be interpreted still reaches the batch as a QUARANTINED record
carrying its own text, because "we dropped 40 rows and did not say which" is the
failure mode that makes an import feature untrustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.errors import ValidationFailedError
from app.modules.imports.constants import ALL_COLUMNS, suggest_mapping
from app.modules.imports.parser import parse
from app.modules.rules.canonical import fingerprint
from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.ingest.adapters import tabular, xml_rules

#: A hard ceiling on one upload. Not a performance limit — the kernel commits in
#: chunks and the parser streams where the format allows — but a guard against a
#: mis-selected file consuming the request worker for minutes.
MAX_ROWS = 50_000


@dataclass(slots=True)
class Rejection:
    """A row that never became a draft."""

    offset: int
    message: str
    column: str = ""
    row: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedFile:
    headings: list[str]
    mapping: dict[str, str | None]
    rows: list[dict[str, Any]]
    drafts: list[CanonicalDraft] = field(default_factory=list)
    #: Raw rows, aligned to `drafts` by index, for the forensic record.
    raw: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    content_hash: str = ""

    @property
    def unmapped_headings(self) -> list[str]:
        return [h for h in self.headings if not self.mapping.get(h)]


def parse_file(
    filename: str,
    data: bytes,
    *,
    mapping: dict[str, str | None] | None = None,
    default_effective_from: date | None = None,
    source_system_code: str | None = None,
    default_charging_mode: str | None = None,
    default_currency: str | None = None,
) -> ParsedFile:
    """Read a file into drafts, rejections and the raw rows behind both.

    Structured XML takes a different route. A tariff XML export nests its
    conditions and actions, and the tabular reader collapses repeated siblings
    onto one key — so two conditions silently become one and every rule fails
    with "the row produced no actions". Nesting is detected, not configured,
    because a vendor cannot be asked to declare which of our two readers their
    file suits.
    """
    if _is_xml(filename, data) and xml_rules.looks_structured(data):
        return _from_structured_xml(
            filename, data,
            default_effective_from=default_effective_from,
            source_system_code=source_system_code,
            default_charging_mode=default_charging_mode,
            default_currency=default_currency,
        )

    headings, rows = parse(filename, data)
    if len(rows) > MAX_ROWS:
        raise ValidationFailedError(
            f"The file has {len(rows):,} rows; the limit for one upload is "
            f"{MAX_ROWS:,}. Split it, or use a connector for a feed this size."
        )

    resolved = tabular.extend_mapping(headings, mapping or suggest_mapping(headings))
    parsed = ParsedFile(
        headings=headings,
        mapping=resolved,
        rows=rows,
        content_hash=fingerprint.content_hash(data),
    )

    effective_from = default_effective_from or date.today()
    for offset, row in enumerate(rows):
        try:
            conversion = tabular.convert(
                row,
                resolved,
                default_effective_from=effective_from,
                source_system_code=source_system_code,
                default_charging_mode=default_charging_mode,
                default_currency=default_currency,
            )
        except tabular.RowError as exc:
            parsed.rejections.append(
                Rejection(
                    offset=offset, message=exc.message, column=exc.column, row=dict(row)
                )
            )
            continue
        parsed.drafts.append(conversion.draft)
        parsed.raw.append(dict(row))
        parsed.notes.extend(conversion.notes)

    return parsed


def column_catalogue() -> list[dict[str, Any]]:
    """Every column an upload may carry, for the mapping screen.

    The legacy registry plus the canonical-only additions, in one list — so the
    UI does not have to know that the two came from different places.
    """
    columns = [
        {
            "key": column.key,
            "label": column.label,
            "kind": column.kind,
            "required": column.required,
            "description": column.description,
            "aliases": list(column.aliases),
        }
        for column in ALL_COLUMNS
    ]
    columns.extend(
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "kind": "HEADER",
            "required": False,
            "description": _EXTRA_HELP.get(key, ""),
            "aliases": list(aliases),
        }
        for key, aliases in tabular.EXTRA_COLUMNS.items()
    )
    return columns


_EXTRA_HELP: dict[str, str] = {
    "charging_mode": "PREPAID, POSTPAID or BOTH. Omitted, the rule is treated as "
                     "BOTH — the only value that cannot narrow who it applies to.",
    "execution_mode": "ONLINE, OFFLINE or BOTH. Which runtime the rule is for.",
    "external_ref": "The vendor's own identifier for the rule. Supply it and a "
                    "re-import updates rather than duplicates — this is the single "
                    "most useful column in the file.",
    "external_version": "The vendor's version string, kept for lineage.",
}


# --- Structured XML ---------------------------------------------------------


def _is_xml(filename: str, data: bytes) -> bool:
    if (filename or "").lower().endswith(".xml"):
        return True
    return data.lstrip()[:1] == b"<"


def _from_structured_xml(
    filename: str,
    data: bytes,
    *,
    default_effective_from: date | None,
    source_system_code: str | None,
    default_charging_mode: str | None,
    default_currency: str | None,
) -> ParsedFile:
    """Read nested rule XML straight into drafts, with no table in between.

    ``headings`` and ``mapping`` come back describing the *element* names rather
    than columns, so the preview screen still has something honest to show: an
    operator wants to know we read their `<Conditions>` block, and a mapping
    table with one row per XML tag would tell them nothing.
    """
    del filename
    parsed_xml = xml_rules.parse_rules(
        data,
        default_effective_from=default_effective_from,
        source_system_code=source_system_code,
        default_charging_mode=default_charging_mode,
        default_currency=default_currency,
    )
    parsed = ParsedFile(
        headings=[parsed_xml.rule_element],
        # Not a heading→column map: this file has no columns. The element name is
        # reported so the preview can say "we read 412 <Rule> elements".
        mapping={parsed_xml.rule_element: "rule"},
        rows=[*parsed_xml.raw, *(r.row for r in parsed_xml.rejections)],
        drafts=parsed_xml.drafts,
        raw=parsed_xml.raw,
        rejections=[
            Rejection(offset=r.offset, message=r.message, column=r.column, row=r.row)
            for r in parsed_xml.rejections
        ],
        notes=[
            f"Read as structured XML: one rule per <{parsed_xml.rule_element}> "
            "element, with nested conditions and actions.",
            *parsed_xml.notes,
        ],
        content_hash=fingerprint.content_hash(data),
    )
    return parsed
