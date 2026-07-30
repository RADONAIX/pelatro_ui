"""Backfill ``rating.rules`` into the canonical model, and report parity.

    python -m scripts.backfill_canonical --dry-run          # convert, compare, roll back
    python -m scripts.backfill_canonical --limit 100        # a slice, for a first look
    python -m scripts.backfill_canonical --rule-key PEAK_ON_NET
    python -m scripts.backfill_canonical                    # the real thing
    python -m scripts.backfill_canonical --parity-only      # re-check after the fact

A script rather than an ``op.execute`` inside a migration, for one reason: a
40,000-rule backfill that dies at 90% must not restart from zero. The kernel
commits in chunks with a checkpoint per chunk, so a re-run picks up the rules
that did not land and reports the rest as UNCHANGED rather than re-versioning
them.

**This does not switch anything over.** It writes the canonical side and tells
you whether the two agree. Repointing the compiler and revoking writes on the
legacy tables are the plan's M4 and M5, and they belong behind a green parity
report — which is what ``--dry-run`` exists to produce before anyone commits to
the real one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.modules.rules import backfill
from app.modules.rules.ingest import kernel
from app.modules.tenancy import context as tenant_context

log = get_logger("backfill")

#: Differences printed in full before the output collapses to a count. Enough to
#: see the shape of a systematic problem, few enough that a broken run does not
#: produce forty thousand lines nobody reads.
_SHOWN = 20


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill rating.rules into ra_rule.* and report compile parity."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert, validate and compare, then roll back. Writes nothing.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only the first N logical rules."
    )
    parser.add_argument(
        "--rule-key", default=None, help="One logical rule, by key. Implies --limit 1."
    )
    parser.add_argument(
        "--parity-only",
        action="store_true",
        help="Skip the backfill and only compare what is already there.",
    )
    parser.add_argument(
        "--no-parity",
        action="store_true",
        help="Backfill without comparing. Faster, and proves nothing.",
    )
    parser.add_argument(
        "--tenant",
        default=settings.default_tenant_id,
        help="Tenant to backfill into. Defaults to the deployment's own.",
    )
    parser.add_argument(
        "--actor",
        default="backfill",
        help="Name recorded on every audit entry this run creates.",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    actor = kernel.Actor(id=None, name=args.actor)

    async with SessionFactory() as db:
        # The canonical tables are under FORCEd row-level security from migration
        # 0013. An unbound connection reads nothing, so a backfill would report a
        # clean run against an estate it could not see.
        await tenant_context.bind_session(db, args.tenant)
        tenant_token = tenant_context.set_current_tenant(args.tenant)
        if args.parity_only:
            report = await backfill.check_parity(db, rule_key=args.rule_key)
        else:
            report = await backfill.backfill(
                db,
                actor=actor,
                limit=args.limit,
                rule_key=args.rule_key,
                dry_run=args.dry_run,
            )
            if not args.no_parity and not args.dry_run:
                # Parity after the write, in the same session: comparing against
                # a canonical side that has not been written yet would report
                # every rule as missing and prove nothing.
                await backfill.check_parity(db, report, rule_key=args.rule_key)

        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()
        tenant_context.reset_current_tenant(tenant_token)

    _print(report, dry_run=args.dry_run)
    return 0 if report.parity and not report.failures else 1


def _print(report: backfill.Report, *, dry_run: bool) -> None:
    summary = report.summary()
    log.info("backfill_complete", dry_run=dry_run, **summary)

    print()
    print("Backfill" + (" (dry run — nothing was written)" if dry_run else ""))
    print(f"  read       {summary['read']}")
    print(f"  converted  {summary['converted']}")
    print(f"  written    {summary['written']}")
    print(f"  unchanged  {summary['unchanged']}")
    print(f"  rejected   {summary['rejected']}")

    if report.needs_money_review:
        print()
        print(
            f"  {len(report.needs_money_review)} rule(s) carry a monetary value that "
            "was stored as a float and cannot be recovered with certainty."
        )
        print("  Backfilled as the closest exact decimal, and flagged for confirmation:")
        for key in report.needs_money_review[:_SHOWN]:
            print(f"    {key}")
        _more(len(report.needs_money_review))

    if report.failures:
        print()
        print(f"  {len(report.failures)} rule(s) could not be converted:")
        for key, reason in report.failures[:_SHOWN]:
            print(f"    {key}: {reason}")
        _more(len(report.failures))

    if dry_run:
        print()
        print(
            "  Parity is not checked on a dry run: the kernel rolls the canonical\n"
            "  rows back, so there would be nothing to compile against. Run for real\n"
            "  and read the parity section, or use --parity-only afterwards."
        )
    elif report.compared:
        print()
        print("Parity — legacy row and canonical row, through the same compiler")
        print(f"  compared     {report.compared}")
        print(f"  differences  {len(report.differences)}")
        for difference in report.differences[:_SHOWN]:
            print(f"    {difference}")
        _more(len(report.differences))
        print()
        print(
            "  PARITY CLEAN — safe to proceed to M4."
            if report.parity
            else "  PARITY FAILED — do not repoint the compiler."
        )
    print()


def _more(total: int) -> None:
    if total > _SHOWN:
        print(f"    … and {total - _SHOWN} more")


def main(argv: list[str] | None = None) -> int:
    configure_logging(level="INFO", json_logs=False)
    args = _parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
