"""What a batch references that the catalogue does not have — and how to fix it.

An import that reports "'STANDARD' does not exist in the offers catalogue" five
times, once per row, has told the operator the truth and given them no way
forward. What they need is the shape of the problem: *one* missing offer, *one*
missing tariff plan, *two* missing zones — and a way to create them.

That framing matters because the two situations look identical in a per-row
report and are completely different in practice. Five rows failing on five
different missing zones is a data problem. Five rows failing on one missing offer
is a five-second fix that currently reads like a broken file.

**Creating them is opt-in and never silent.** A stub offer conjured from a tariff
sheet has no product, no validity and no commercial meaning; it exists so the
tariff can land, and it is marked as such — ``source_system`` records the import
and the description says where it came from. An operator who wants their
catalogue built properly should build it properly; an operator who wants to get a
40,000-row export in and tidy up afterwards should be able to say so explicitly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.ingest.resolver import (
    PENDING_CATALOGUES,
    REFERENCE_MODELS,
    ResolutionCache,
)

#: Catalogues an import may create stubs in.
#:
#: The test is not "does it have a code and a name" but **"is a placeholder here
#: inert?"**. A destination zone with no prefixes matches nothing, so a stub is
#: safe: rules referencing it simply do not fire until someone loads the
#: prefixes. A *time band* is the opposite — its start and end decide which
#: events fall inside it, so a placeholder does not wait to be completed, it
#: mis-classifies traffic from the moment it exists.
#:
#: Everything excluded below is excluded for that reason, not because it was
#: harder to build.
CREATABLE: frozenset[str] = frozenset(
    {"products", "tariff-plans", "destination-zones", "rating-groups",
     "balance-types", "billing-cycles", "invoice-components", "charging-units"}
)

#: Why a catalogue cannot be stubbed, in words an operator can act on.
_NOT_CREATABLE: dict[str, str] = {
    "offers": (
        "An offer belongs to a product, and this file does not say which. Add a "
        "Product column, or create the offer under Metadata Catalogue first — "
        "inventing a product to hang it on would be two fictions rather than one."
    ),
    "time-bands": (
        "A time band's start and end decide which events fall inside it, so a "
        "placeholder does not wait to be completed — it mis-classifies traffic "
        "from the moment it exists. Create it with its real hours first."
    ),
    "bundles": (
        "A bundle is its allowance, and this file does not state one. A stub "
        "bundle would grant zero and silently zero-rate whatever consumed it."
    ),
    "currencies": (
        "A currency is an ISO-4217 code with a minor-unit scale that decides how "
        "money rounds. There is no safe placeholder for it."
    ),
    "tax-rules": (
        "A tax rule with no rate is not a placeholder, it is a zero rate. Create "
        "it with its real percentage, or state the rate inline with a Tax "
        "Percentage column."
    ),
    "discounts": "A discount with no value silently discounts nothing.",
    "promotions": "A promotion with no value silently applies nothing.",
    "rounding-rules": (
        "A rounding rule decides how money rounds; a default here is a penny per "
        "invoice. Create it, or state a Rounding Mode column."
    ),
}

#: Fixed columns a stub needs beyond code and name. Listed rather than inferred
#: from the model, because a value that happens to satisfy a NOT NULL is not the
#: same as one that means something.
_STUB_DEFAULTS: dict[str, dict[str, Any]] = {
    "rating-groups": {"service_type": "ANY"},
    "destination-zones": {"zone_type": "NATIONAL"},
    "balance-types": {"category": "MAIN"},
    "billing-cycles": {"frequency": "MONTHLY"},
    "invoice-components": {"component_type": "USAGE"},
    "charging-units": {"dimension": "EVENT", "base_unit": "EVENT"},
}


@dataclass(slots=True)
class MissingCatalogue:
    """One catalogue, and everything this batch wanted from it that is absent."""

    catalogue: str
    label: str
    codes: list[str] = field(default_factory=list)
    #: How many rules are blocked by this catalogue. One missing offer blocking
    #: forty rules is a different conversation from forty missing offers.
    affected_rules: int = 0
    creatable: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "catalogue": self.catalogue,
            "label": self.label,
            "codes": self.codes,
            "affected_rules": self.affected_rules,
            "creatable": self.creatable,
            "reason": self.reason,
            "create_endpoint": f"/api/rating/catalog/{self.catalogue}",
        }


async def survey(
    db: AsyncSession, drafts: list[CanonicalDraft], cache: ResolutionCache
) -> list[MissingCatalogue]:
    """Every catalogue code the batch needs and does not have, grouped.

    One pass over the drafts and one query per catalogue, so surveying a 40,000
    row import costs the same twenty queries as importing it.
    """
    del db
    wanted: dict[str, set[str]] = defaultdict(set)
    blocked: dict[str, set[int]] = defaultdict(set)

    for index, draft in enumerate(drafts):
        for slug, codes in draft.reference_codes().items():
            wanted[slug] |= codes
            blocked[slug].add(index)

    if not wanted:
        return []

    missing = await cache.load_catalogues({k: set(v) for k, v in wanted.items()})
    out: list[MissingCatalogue] = []
    for slug, codes in sorted(missing.items()):
        # Only the rules that actually asked for a *missing* code are blocked.
        affected = sum(
            1
            for draft in drafts
            if codes & draft.reference_codes().get(slug, set())
        )
        out.append(
            MissingCatalogue(
                catalogue=slug,
                label=slug.replace("-", " "),
                codes=sorted(codes),
                affected_rules=affected,
                creatable=slug in CREATABLE and slug in REFERENCE_MODELS,
                reason=_reason(slug),
            )
        )
    return sorted(out, key=lambda m: (-m.affected_rules, m.catalogue))


def _reason(slug: str) -> str:
    if slug in PENDING_CATALOGUES:
        return "This catalogue is not available in this release."
    if slug in _NOT_CREATABLE:
        return _NOT_CREATABLE[slug]
    if slug not in CREATABLE:
        return (
            f"A {slug.rstrip('s').replace('-', ' ')} cannot be created from an "
            "import. Create it under Metadata Catalogue first."
        )
    return ""


def _derived(
    slug: str, code: str, drafts: list[CanonicalDraft]
) -> dict[str, Any] | None:
    """Columns taken from the rules that reference this code, not invented.

    A tariff plan needs a service type and a currency, and both are stated by the
    rules referencing it — so they are read rather than defaulted. Returning
    ``None`` means the file does not carry what the stub needs, and the entry is
    skipped with that reported rather than filled in with a guess.
    """
    if slug != "tariff-plans":
        return {}

    users = [
        d for d in drafts
        if code in d.reference_codes().get("tariff-plans", set())
    ]
    services = {d.service_type for d in users if d.service_type}
    currencies = {
        d.validity.currency_code for d in users if d.validity.currency_code
    }
    if not currencies:
        return None
    return {
        # Several services on one plan is normal; ANY is the honest summary
        # rather than picking one of them arbitrarily.
        "service_type": services.pop() if len(services) == 1 else "ANY",
        "currency_code": currencies.pop() if len(currencies) == 1 else "GBP",
    }


async def create_stubs(
    db: AsyncSession,
    missing: list[MissingCatalogue],
    drafts: list[CanonicalDraft],
    *,
    actor_id: str | None,
    source_label: str,
) -> dict[str, list[str]]:
    """Create placeholder catalogue entries so the batch can land.

    Only for catalogues on :data:`CREATABLE`, only when explicitly asked, and
    every row is marked with where it came from — an operator looking at their
    catalogue later must be able to tell what they defined from what an import
    invented on their behalf.

    Where a stub needs a value the file does carry — a tariff plan's currency —
    it is read from the referencing rules. Where the file does not carry it, the
    entry is skipped rather than guessed at.
    """
    from app.modules.catalog import service as catalog_service

    created: dict[str, list[str]] = {}
    for entry in missing:
        if not entry.creatable:
            continue
        model = REFERENCE_MODELS[entry.catalogue]
        for code in entry.codes:
            derived = _derived(entry.catalogue, code, drafts)
            if derived is None:
                continue
            payload = {
                "code": code,
                "name": code.replace("_", " ").title(),
                "description": (
                    f"Created automatically by {source_label} because a rule "
                    "referenced it. Review and complete this entry."
                ),
                "status": "ACTIVE",
                "source_system": "FILE_IMPORT",
                "attributes": {"auto_created": True, "source": source_label},
                **_STUB_DEFAULTS.get(entry.catalogue, {}),
                **derived,
            }
            await catalog_service.create(
                db, model, payload, label=entry.label, actor_id=actor_id
            )
            created.setdefault(entry.catalogue, []).append(code)
    await db.flush()
    return created
