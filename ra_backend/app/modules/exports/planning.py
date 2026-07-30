"""Export partition planning.

Decides whether an export runs as a single streamed file or fans out into
date-bounded parts, and computes the part boundaries.

Design invariants (why this module exists):
  * No single Celery task may run near the Redis visibility_timeout ceiling. A
    task that outlives visibility_timeout is re-delivered and runs twice (the bug
    §10.1 fixed). Large exports MUST therefore fan out so every part is short;
    only small/medium jobs take the single-file path, under the larger (but still
    sub-visibility) job time limit.
  * Partitioning is driven by estimated ROWS per part (≈ part duration), NOT by
    calendar span — so a wide, no-date-range export still parallelises. Calendar
    span is only a secondary cap on a part's window.
  * Every sizing query is timeout-guarded (reporting.bounded_count /
    date_histogram return None on timeout). A timeout means "too big/slow to size
    cheaply" → plan conservatively; never launch an unbounded single task.

Parts are whole-day aligned (``export_job_parts`` stores DATE bounds and
``export_groups`` filters on ``col::date`` / ``toDate(col)``), so a single hot day
is its own part — at real volumetrics that is minutes, far under any limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.reporting import service as reporting

log = get_logger("exports.planning")


@dataclass(frozen=True)
class PartBound:
    index: int
    date_from: date          # inclusive lower bound
    date_to: date            # EXCLUSIVE upper bound (matches export_job_parts)
    est_rows: int            # estimate only; the worker records actual rows


@dataclass(frozen=True)
class ExportPlan:
    mode: Literal["single", "multipart", "reject"]
    est_total_rows: int | None = None   # None => unknown (sizing timed out)
    parts: list[PartBound] = field(default_factory=list)
    reason: str = ""                    # audit log / 422 body

    @property
    def rejected(self) -> bool:
        return self.mode == "reject"


async def plan_export(
    report_key: str,
    *,
    date_from: date | None,
    date_to: date | None,       # inclusive (as the user gave it)
    categories: dict[str, list[str]] | None,
    search: str | None,
) -> ExportPlan:
    """Decide single-file vs multipart vs reject, and compute part boundaries."""
    meta = reporting.report_meta(report_key)
    # Query upper bound is EXCLUSIVE; the user's dateTo is inclusive.
    win: tuple[date | None, date | None] = (None, None)
    if date_from is not None and date_to is not None:
        win = (date_from, date_to + timedelta(days=1))

    # (1) No date column → single-file is the only option; guard absurd size so a
    #     job that can't be partitioned can't silently run into its time limit.
    if not meta.date_column:
        n = await reporting.bounded_count(
            report_key, date_from=win[0], date_to=win[1],
            categories=categories, search=search,
            timeout_s=settings.export_count_timeout_seconds,
        )
        if n is None or n > settings.export_single_file_hard_max_rows:
            return ExportPlan(
                mode="reject", est_total_rows=n,
                reason=("This report has no date column to partition on, and the "
                        "filtered result is too large (or too slow to size) to export "
                        "as a single file. Narrow the filters and retry."),
            )
        return ExportPlan(mode="single", est_total_rows=n,
                          reason="no date column; size within single-file limit")

    # (2) Date column → one grouped pass yields total + per-day density, so we can
    #     partition on the data's real shape and skip a separate count.
    hist = await reporting.date_histogram(
        report_key, date_column=meta.date_column,
        categories=categories, search=search, window=win,
        timeout_s=settings.export_histogram_timeout_seconds,
    )
    if hist is None:
        return await _plan_without_histogram(report_key, categories, search, win)

    total = sum(c for _, c in hist)
    if total <= settings.export_multipart_row_threshold:
        return ExportPlan(mode="single", est_total_rows=total,
                          reason="below multipart threshold")

    parts = _pack_parts(
        hist, target_rows=settings.export_target_part_rows,
        max_days=settings.export_days_per_part,
    )
    if len(parts) <= 1:
        # Everything lands in one part (e.g. all rows on a single day — day-granular
        # parts aren't split intra-day). A 1-part chord adds 0% progress + a pointless
        # concat, so run it as a single-file streaming job (incremental progress).
        return ExportPlan(mode="single", est_total_rows=total,
                          reason="single date-part → single-file")
    if len(parts) > settings.export_hard_max_parts:
        return ExportPlan(
            mode="reject", est_total_rows=total,
            reason=(f"Export would need {len(parts)} parts (> hard cap "
                    f"{settings.export_hard_max_parts}). Narrow the date range or filters."),
        )
    if len(parts) > settings.export_soft_max_parts:
        log.warning("export_many_parts", report=report_key, parts=len(parts), total_rows=total)
    return ExportPlan(mode="multipart", est_total_rows=total, parts=parts,
                      reason=f"{len(parts)} date-bounded parts")


async def _plan_without_histogram(
    report_key: str,
    categories: dict[str, list[str]] | None,
    search: str | None,
    win: tuple[date | None, date | None],
) -> ExportPlan:
    """The histogram timed out (e.g. a global ILIKE forced a scan the date index
    couldn't satisfy). We've lost per-day density → decide conservatively and
    never launch an unbounded single task."""
    n = await reporting.bounded_count(
        report_key, date_from=win[0], date_to=win[1],
        categories=categories, search=search,
        timeout_s=settings.export_count_timeout_seconds,
    )
    if n is not None and n <= settings.export_multipart_row_threshold:
        return ExportPlan(mode="single", est_total_rows=n,
                          reason="histogram timed out; count small → single")

    win_from, win_to = win
    if win_from is not None and win_to is not None:
        # Explicit window but no density → uniform calendar split so no single
        # task runs long. Row estimates unknown (indeterminate progress bars).
        parts = _uniform_split(
            win_from, win_to, max_days=settings.export_days_per_part,
            hard_max_parts=settings.export_hard_max_parts,
        )
        if parts is None:
            return ExportPlan(mode="reject", est_total_rows=n,
                              reason="Date span too large to split within the part cap; "
                                     "narrow the date range.")
        return ExportPlan(mode="multipart", est_total_rows=n, parts=parts,
                          reason="histogram timed out; uniform calendar split")

    # No window and no density → refuse rather than fire an unbounded task.
    return ExportPlan(
        mode="reject", est_total_rows=n,
        reason=("Could not size or partition this export within the time budget. "
                "Add a date range to enable partitioning."),
    )


def _pack_parts(
    hist: list[tuple[date, int]], *, target_rows: int, max_days: int
) -> list[PartBound]:
    """Greedy-pack consecutive (data-bearing) days into parts, each capped by BOTH
    ``target_rows`` and ``max_days`` calendar days. A single day heavier than
    target_rows becomes its own part (still minutes at real volumetrics)."""
    bounds: list[tuple[date, date, int]] = []
    open_from: date | None = None
    open_to: date | None = None
    open_rows = 0

    def flush() -> None:
        nonlocal open_from, open_to, open_rows
        if open_from is not None and open_to is not None:
            bounds.append((open_from, open_to, open_rows))
        open_from = open_to = None
        open_rows = 0

    for day, cnt in sorted(hist, key=lambda kv: kv[0]):
        day_end = day + timedelta(days=1)
        if open_from is not None and (
            open_rows + cnt > target_rows or (day_end - open_from).days > max_days
        ):
            flush()
        if open_from is None:
            open_from = day
        open_to = day_end
        open_rows += cnt
    flush()
    return [PartBound(index=i, date_from=f, date_to=t, est_rows=r)
            for i, (f, t, r) in enumerate(bounds)]


def _uniform_split(
    win_from: date, win_to: date, *, max_days: int, hard_max_parts: int
) -> list[PartBound] | None:
    """Density-blind calendar split of [win_from, win_to) into ``max_days`` blocks.
    None if it would exceed the hard part cap."""
    total_days = max(1, (win_to - win_from).days)
    if -(-total_days // max_days) > hard_max_parts:   # ceil div
        return None
    parts: list[PartBound] = []
    cursor, idx = win_from, 0
    while cursor < win_to:
        nxt = min(cursor + timedelta(days=max_days), win_to)
        parts.append(PartBound(index=idx, date_from=cursor, date_to=nxt, est_rows=0))
        cursor, idx = nxt, idx + 1
    return parts
