"""Read an uploaded tariff sheet into rows of ``{heading: value}``.

Format detection is by extension, then by content — a file named ``.csv`` that
is actually tab-separated is common enough in operator handovers to be worth
sniffing rather than rejecting.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.core.errors import ValidationFailedError

MAX_ROWS = 50_000
#: Excel support is optional: the service must start without openpyxl installed,
#: and only refuse .xlsx uploads if it is genuinely missing.
try:  # pragma: no cover - exercised by the absence of the dependency
    from openpyxl import load_workbook

    EXCEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore[assignment]
    EXCEL_AVAILABLE = False


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _from_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    # utf-8-sig strips the BOM Excel writes, which would otherwise corrupt the
    # first heading into "﻿name" and break the auto-mapping.
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        raise ValidationFailedError("The file is empty.")
    headings = [_clean(h) for h in rows[0]]
    out: list[dict[str, str]] = []
    for raw in rows[1 : MAX_ROWS + 1]:
        if not any(_clean(c) for c in raw):
            continue  # blank separator row
        out.append({h: _clean(v) for h, v in zip(headings, raw, strict=False) if h})
    return [h for h in headings if h], out


def _from_excel(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    if not EXCEL_AVAILABLE:
        raise ValidationFailedError(
            "Excel import needs the openpyxl package.",
            details={"hint": "pip install openpyxl, or save the sheet as CSV."},
        )
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration as exc:
        raise ValidationFailedError("The spreadsheet is empty.") from exc
    headings = [_clean(h) for h in header_row]
    out: list[dict[str, str]] = []
    for i, raw in enumerate(rows_iter):
        if i >= MAX_ROWS:
            break
        if not any(_clean(c) for c in raw):
            continue
        out.append({h: _clean(v) for h, v in zip(headings, raw, strict=False) if h})
    wb.close()
    return [h for h in headings if h], out


def _from_json(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        parsed = json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(f"Invalid JSON: {exc}") from exc
    # Accept either a bare array or {"rules": [...]} / {"rows": [...]}.
    if isinstance(parsed, dict):
        for key in ("rules", "rows", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise ValidationFailedError(
            "Expected a JSON array of rows, or an object with a 'rules' array."
        )
    rows = [
        {k: _clean(v) for k, v in row.items()}
        for row in parsed[:MAX_ROWS]
        if isinstance(row, dict)
    ]
    headings: list[str] = []
    for row in rows:
        for k in row:
            if k not in headings:
                headings.append(k)
    return headings, rows


def _from_xml(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """One row per repeated element; attributes and child text become columns.

    Vendor exports differ in what the repeated element is called (<rule>,
    <tariff>, <record>…), so instead of demanding a name, the parser takes
    whichever child element repeats under the root. Nested single-level
    children flatten to ``parent.child`` so a structured export still maps.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    except ET.ParseError as exc:
        raise ValidationFailedError(f"Invalid XML: {exc}") from exc

    def _local(tag: str) -> str:
        # Strip namespaces: {urn:vendor}rate_per_unit -> rate_per_unit.
        return tag.rsplit("}", 1)[-1]

    children = list(root)
    if not children:
        raise ValidationFailedError("The XML file contains no rule elements.")

    def _flatten(el: Any, prefix: str = "") -> dict[str, str]:
        row: dict[str, str] = {}
        for name, value in el.attrib.items():
            row[prefix + _local(name)] = _clean(value)
        for child in el:
            name = prefix + _local(child.tag)
            if len(child) or child.attrib:
                row.update(_flatten(child, prefix=f"{name}."))
            if child.text and _clean(child.text):
                row[name] = _clean(child.text)
        return row

    rows = [r for r in (_flatten(el) for el in children[:MAX_ROWS]) if r]
    if not rows:
        raise ValidationFailedError("No usable rows were found in the XML.")
    headings: list[str] = []
    for row in rows:
        for k in row:
            if k not in headings:
                headings.append(k)
    return headings, rows


def parse(filename: str, data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Return ``(headings, rows)``. Raises ValidationFailedError on a bad file."""
    if not data:
        raise ValidationFailedError("The uploaded file is empty.")
    lower = (filename or "").lower()
    if lower.endswith((".xlsx", ".xlsm", ".xltx")):
        return _from_excel(data)
    if lower.endswith(".json"):
        return _from_json(data)
    if lower.endswith(".xml"):
        return _from_xml(data)
    if lower.endswith((".csv", ".tsv", ".txt")):
        return _from_csv(data)
    # Unknown extension — sniff. An .xlsx is a zip, so it starts with "PK".
    if data[:2] == b"PK":
        return _from_excel(data)
    stripped = data.lstrip()
    if stripped[:1] in (b"[", b"{"):
        return _from_json(data)
    if stripped[:1] == b"<":
        return _from_xml(data)
    return _from_csv(data)
