"""Streaming ZIP-of-CSVs writer.

Produces a single ``.zip`` whose entries are CSV files each holding at most
``rows_per_file`` data rows (plus the header) — so every file opens in Excel
(≤ 1,048,576 rows). Rows are written straight into DEFLATE-compressed zip
entries as they stream in: no intermediate uncompressed files, memory bounded by
one chunk. Used by the export worker for the single-file path and to reassemble
multi-part exports.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any


class ZipCsvWriter:
    def __init__(
        self,
        fileobj: Any,
        header: list,
        rows_per_file: int,
        base_name: str = "export",
        total_rows: int | None = None,
    ) -> None:
        self._zip = zipfile.ZipFile(
            fileobj, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        )
        self._header = header
        self._rows_per_file = max(1, rows_per_file)
        self._base = base_name
        # Exact total (when known, e.g. the ClickHouse count) makes the last file's
        # end-of-range in its name exact rather than the nominal block end.
        self._total = total_rows
        self._entry: io.TextIOWrapper | None = None
        self._writer: Any = None
        self._entry_rows = 0
        self.total_rows = 0

    def _entry_name(self) -> str:
        # Files are named {base}_{firstRow}-{lastRow}.csv (1-indexed, 7-digit min),
        # e.g. sdp_reconciliation_0000001-1000000.csv.
        start = self.total_rows + 1
        end = start + self._rows_per_file - 1
        if self._total is not None and self._total >= start:
            end = min(end, self._total)  # exact end for the final (partial) file
        return f"{self._base}_{start:07d}-{end:07d}.csv"

    def _open_entry(self) -> None:
        raw = self._zip.open(self._entry_name(), mode="w")
        self._entry = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        self._writer = csv.writer(self._entry)
        self._writer.writerow(self._header)
        self._entry_rows = 0

    def _close_entry(self) -> None:
        if self._entry is not None:
            self._entry.close()  # flush + finalize this zip entry
            self._entry = self._writer = None

    def write_rows(self, chunk: list) -> None:
        """Append rows, rolling to a new CSV file at the row cap (respected even
        when a chunk straddles the boundary)."""
        i, n = 0, len(chunk)
        while i < n:
            if self._entry is None:
                self._open_entry()
            take = min(self._rows_per_file - self._entry_rows, n - i)
            self._writer.writerows(chunk[i : i + take])
            self._entry_rows += take
            self.total_rows += take
            i += take
            if self._entry_rows >= self._rows_per_file:
                self._close_entry()

    def close(self) -> None:
        self._close_entry()
        self._zip.close()
