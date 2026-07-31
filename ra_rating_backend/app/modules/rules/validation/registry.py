"""Runs the validation tiers and persists what they found.

Tier order is not cosmetic. Structural runs first and can short-circuit: there is
no point resolving catalogue codes on a draft whose attributes do not exist, and
a batch of 40,000 malformed records should fail before it issues a single query.

Persisting the issues (rather than caching a ``{"valid": false, "error_count": 3}``
summary on the rule, as the legacy model does) is what makes the catalogue's
Validation column drillable — "show me every rule that failed currency_missing"
becomes an indexed query instead of a script that re-validates the estate.
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.canonical.lineage import RuleValidationIssue
from app.modules.rules.ingest.resolver import ResolutionCache
from app.modules.rules.validation import modes, semantic, structural
from app.modules.rules.validation.issues import Report


def check_offline(draft: CanonicalDraft) -> Report:
    """Every tier that needs no database. What the wizard calls as you type."""
    report = Report()
    report.extend(structural.check(draft))
    report.extend(modes.check(draft))
    return report


async def check_draft(
    draft: CanonicalDraft, cache: ResolutionCache, *, skip_references: bool = False
) -> Report:
    """The full per-rule verdict: structural, mode coherence, then semantic.

    ``skip_references`` exists for the preview of a batch whose catalogue entries
    are being created by the same release — the reference tier would report
    thousands of failures that the commit will not hit.
    """
    report = check_offline(draft)
    # A draft whose attributes or actions are unknown cannot have its references
    # meaningfully resolved; reporting "MAIN does not exist" on top of "that is
    # not an attribute" buries the error that matters.
    if not skip_references and report.valid:
        report.extend(await semantic.check(draft, cache))
    return report


async def persist(
    db: AsyncSession, rule_version_id: str, tenant_id: str, report: Report
) -> None:
    """Replace this version's issues with the current verdict.

    Replace, not append: a stale issue against logic that has since been fixed is
    worse than no issue at all, because it makes the whole column untrustworthy.
    """
    await db.execute(
        delete(RuleValidationIssue).where(
            RuleValidationIssue.rule_version_id == rule_version_id
        )
    )
    for issue in report.issues:
        db.add(
            RuleValidationIssue(
                tenant_id=tenant_id,
                rule_version_id=rule_version_id,
                severity=issue.severity,
                code=issue.code,
                message=issue.message,
                path=issue.path,
                hint=issue.hint,
            )
        )
