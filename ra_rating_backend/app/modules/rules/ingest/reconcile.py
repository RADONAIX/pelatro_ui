"""What to do with each incoming rule: new, changed, unchanged, or withdrawn.

The decision that earns this module its existence is **UNCHANGED**. A vendor
export is a full dump every night; without a behaviour-hash comparison, importing
it creates 4,000 new versions daily and the audit trail is useless inside a week.
With one, the same import reads "3 changed, 3,997 unchanged" and the three that
moved are visible.

The decision that earns it its *care* is **WITHDRAWN**. A rule present in the
store and absent from a FULL export might have been deleted upstream — or the
export might have been truncated, filtered, or generated from the wrong
environment. The legacy connector auto-retired drafts on this signal and silently
ignored live rules, which is exactly backwards: a draft disappearing costs
nothing, a live rule that keeps rating after the vendor deleted it is the failure
this product exists to catch. So a withdrawal is never applied here. It is
*proposed*, and a human decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.rules.canonical.draft import CanonicalDraft
from app.modules.rules.ingest.resolver import ResolutionCache


class Decision(StrEnum):
    NEW = "NEW"
    #: Behaviour hash differs from the stored current version.
    CHANGED = "CHANGED"
    #: Byte-identical behaviour. No version is cut.
    UNCHANGED = "UNCHANGED"
    #: In the store, absent from a FULL export. Proposed, never applied.
    WITHDRAWN = "WITHDRAWN"
    #: Failed validation.
    REJECTED = "REJECTED"
    #: Could not be interpreted at all — held for inspection, not discarded.
    QUARANTINED = "QUARANTINED"


class ImportMode(StrEnum):
    #: The export is the whole truth: anything missing from it was withdrawn.
    FULL = "FULL"
    #: The export carries only what moved. Absence means nothing.
    DELTA = "DELTA"


@dataclass(slots=True)
class Outcome:
    decision: str
    rule_key: str
    #: Present when the rule already exists.
    rule_id: str | None = None
    reason: str = ""


@dataclass(slots=True)
class Summary:
    counts: dict[str, int] = field(default_factory=dict)
    #: Keys seen in this batch, so FULL-mode withdrawal is a set difference.
    seen_keys: set[str] = field(default_factory=set)

    def record(self, decision: str) -> None:
        self.counts[decision] = self.counts.get(decision, 0) + 1

    @property
    def touches_live_pricing(self) -> bool:
        """Whether this batch would alter a price that is already rating.

        The gate on auto-commit. NEW rules land as drafts and change nothing
        until someone publishes them; CHANGED and WITHDRAWN do not have that
        property, so a batch containing either needs a human.
        """
        return bool(
            self.counts.get(Decision.CHANGED) or self.counts.get(Decision.WITHDRAWN)
        )


def decide(
    draft: CanonicalDraft, rule_key: str, cache: ResolutionCache, incoming_hash: str
) -> Outcome:
    """NEW, CHANGED or UNCHANGED for one draft, from the pre-loaded rule index.

    Entirely in memory. The legacy importers called ``latest_version()`` per row
    inside the commit loop — tens of thousands of round trips on a 40,000-row
    dump, which is why one took hours. The index is loaded once by
    :meth:`ResolutionCache.load_rule_index`.
    """
    del draft
    existing = cache.rule_index.get(rule_key)
    if existing is None:
        return Outcome(Decision.NEW, rule_key)

    rule_id, stored_hash = existing
    if cache.rule_statuses.get(rule_key) == "RETIRED":
        return Outcome(
            Decision.CHANGED,
            rule_key,
            rule_id,
            reason="The retired rule was explicitly reintroduced by this import.",
        )
    if stored_hash and stored_hash == incoming_hash:
        return Outcome(
            Decision.UNCHANGED, rule_key, rule_id,
            reason="Behaviour is identical to the stored version.",
        )
    return Outcome(
        Decision.CHANGED, rule_key, rule_id,
        reason="No stored behaviour hash to compare against." if not stored_hash
        else "Behaviour differs from the stored version.",
    )


def withdrawals(
    cache: ResolutionCache,
    seen_keys: set[str],
    *,
    import_mode: str,
    source_system_id: str | None,
) -> list[Outcome]:
    """Rules the store holds that a FULL export did not mention.

    Scoped to the source system that sent the export. Without that scope, an
    Ericsson dump would propose withdrawing every hand-authored rule in the
    estate, which is both wrong and the sort of wrong that erodes trust in the
    whole import feature.
    """
    if import_mode != ImportMode.FULL or source_system_id is None:
        return []

    owned = {
        key for (source_id, _ref), key in cache.external_index.items()
        if source_id == source_system_id
    }
    return [
        Outcome(
            Decision.WITHDRAWN,
            key,
            cache.rule_index.get(key, (None, None))[0],
            reason="Present in the store but absent from this full export.",
        )
        for key in sorted(owned - seen_keys)
    ]
