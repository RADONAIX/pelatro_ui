"""Replay existing rows into `rafms_rating_new`.

This is the repair path that makes the mirror's at-least-once, fail-open posture
acceptable. The runtime hooks make no durability promise — a mirror that was down
during a rule save simply logged a warning — so there has to be a way to bring it
back into line without a queue or an outbox table in the primary database.

Idempotent: every write is an upsert or a replace on a natural key, so running it
twice produces the same mirror as running it once.

    python -m scripts.mirror_backfill --all
    python -m scripts.mirror_backfill --vocabulary --time-bands
    python -m scripts.mirror_backfill --rules --tenant 0000...-0001
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.modules.catalog.models import DestinationPrefix, TimeBand
from app.modules.cdr.models import SubscriberProduct
from app.modules.mirror import sync, vocabulary
from app.modules.mirror.engine import dispose, mirror_session
from app.modules.rules.canonical.rule import CanonicalRuleVersion
from app.modules.rules.models import Rule as LegacyRule
from app.modules.tenancy import context as tenant_context

log = get_logger("mirror.backfill")


async def backfill_vocabulary() -> None:
    # Invalidate first: an explicit --vocabulary run must actually re-seed, not
    # short-circuit on a flag some earlier call in this process set.
    vocabulary.invalidate()
    async with mirror_session() as mirror:
        counts = await vocabulary.reconcile(mirror)
    log.info("backfill_vocabulary", **counts)


async def backfill_rules(
    tenant_id: str | None, *, require_nonempty: bool = False
) -> None:
    tenant = tenant_id or settings.default_tenant_id
    async with SessionFactory() as db, tenant_context.scoped(db, tenant):
        # Canonical tables are protected by FORCE RLS. A script session without
        # the tenant GUC fails closed and sees zero rows, which is especially
        # dangerous after --purge-rules because it leaves the repaired mirror
        # empty while reporting a successful run.
        stmt = (
            select(CanonicalRuleVersion.rule_version_id)
            .where(CanonicalRuleVersion.tenant_id == tenant)
            .order_by(
                CanonicalRuleVersion.rule_id,
                CanonicalRuleVersion.version_number,
            )
        )
        ids = list((await db.execute(stmt)).scalars().all())
        if require_nonempty and not ids:
            raise RuntimeError(
                f"Canonical backfill found no versions for tenant {tenant}; "
                "refusing to leave a purged mirror empty."
            )
        written = 0
        for rule_version_id in ids:
            try:
                written += int(await sync.push_rule_version(db, rule_version_id))
            except Exception as exc:
                log.warning("backfill_rule_failed", id=rule_version_id, error=repr(exc))
    log.info("backfill_rules", seen=len(ids), written=written)


async def purge_rules() -> None:
    """Clear every mirrored rule. Conditions and actions cascade.

    Needed when switching `MIRROR_RULE_SOURCE`: the rows written from the other
    model would otherwise stay behind and read as duplicated tariffs.
    """
    from sqlalchemy import delete

    from app.modules.mirror import tables

    async with mirror_session() as mirror:
        result = await mirror.execute(delete(tables.rating_rule))
    log.info("purge_rules", deleted=result.rowcount)


async def backfill_legacy_rules() -> None:
    """Replay `rating.rules` — the model the authoring UI actually writes."""
    async with SessionFactory() as db:
        ids = list((await db.execute(select(LegacyRule.id))).scalars().all())
        written = 0
        for rule_id in ids:
            try:
                written += int(await sync.push_legacy_rule(db, rule_id))
            except Exception as exc:
                log.warning("backfill_legacy_rule_failed", id=rule_id, error=repr(exc))
    log.info("backfill_legacy_rules", seen=len(ids), written=written)


async def backfill_time_bands() -> None:
    async with SessionFactory() as db:
        ids = list((await db.execute(select(TimeBand.id))).scalars().all())
        written = 0
        for entity_id in ids:
            try:
                written += int(await sync.push_time_band(db, entity_id))
            except Exception as exc:
                log.warning("backfill_time_band_failed", id=entity_id, error=repr(exc))
    log.info("backfill_time_bands", seen=len(ids), written=written)


async def backfill_prefixes() -> None:
    async with SessionFactory() as db:
        ids = list((await db.execute(select(DestinationPrefix.id))).scalars().all())
        written = 0
        for entity_id in ids:
            try:
                written += int(await sync.push_destination_prefix(db, entity_id))
            except Exception as exc:
                log.warning("backfill_prefix_failed", id=entity_id, error=repr(exc))
    log.info("backfill_prefixes", seen=len(ids), written=written)


async def backfill_subscriber_offers() -> None:
    """`subscriber_offer` has no runtime writer — this is its only feed.

    `rating.subscriber_products` is loaded out of band (seed or operator import)
    and only ever read by enrichment, so there is no application event to hook.
    Both this and the balance mirror also depend on `canonical_rating.subscriber`
    being populated, which is outside this platform's scope.
    """
    async with SessionFactory() as db:
        ids = list((await db.execute(select(SubscriberProduct.id))).scalars().all())
        written = 0
        for entity_id in ids:
            try:
                written += int(await sync.push_subscriber_offer(db, entity_id))
            except Exception as exc:
                log.warning("backfill_subscriber_offer_failed", id=entity_id, error=repr(exc))
    log.info("backfill_subscriber_offers", seen=len(ids), written=written)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--vocabulary", action="store_true")
    parser.add_argument("--rules", action="store_true", help="Canonical ra_rule.* rules.")
    parser.add_argument(
        "--legacy-rules", action="store_true", help="rating.rules — what the UI writes."
    )
    parser.add_argument("--time-bands", action="store_true")
    parser.add_argument("--prefixes", action="store_true")
    parser.add_argument("--subscriber-offers", action="store_true")
    parser.add_argument("--tenant", default=None, help="Limit rules to one tenant.")
    parser.add_argument(
        "--purge-rules",
        action="store_true",
        help="Delete every mirrored rule first. Use when switching rule source, "
             "so rows from the other model do not linger as apparent duplicates.",
    )
    args = parser.parse_args()

    configure_logging(level=settings.log_level, json_logs=False)
    if not settings.mirror_enabled:
        log.error("mirror_disabled", hint="Set MIRROR_ENABLED=true to run a backfill.")
        return 2

    try:
        # Vocabulary first, always: the rule tables have foreign keys onto it.
        if args.all or args.vocabulary:
            await backfill_vocabulary()
        if args.purge_rules:
            await purge_rules()
        if args.all or args.rules:
            await backfill_rules(args.tenant, require_nonempty=args.purge_rules)
        if args.all or args.legacy_rules:
            await backfill_legacy_rules()
        if args.all or args.time_bands:
            await backfill_time_bands()
        if args.all or args.prefixes:
            await backfill_prefixes()
        if args.all or args.subscriber_offers:
            await backfill_subscriber_offers()
    finally:
        await dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
