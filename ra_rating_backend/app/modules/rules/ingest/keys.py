"""Deterministic logical identity for a rule.

The legacy service slugged the rule key from the rule *name*. Two consequences,
both bad and both silent:

**Collision.** Two vendor rules called "Peak" become one logical rule, and the
second import overwrites the first as though it were a new version of it.

**Forking.** A vendor renames "Peak" to "Peak Hours" and the next import creates
a second logical rule at version 1, while the original keeps rating. Nothing
errors; the estate just quietly has two rules where it had one.

So names never participate in identity here. A rename is an update to the same
rule; a changed predicate is a different rule. That ordering is the whole design.
"""

from __future__ import annotations

import re

from app.modules.rules.canonical import fingerprint
from app.modules.rules.canonical.draft import CanonicalDraft

_SLUG = re.compile(r"[^A-Z0-9]+")

#: Matches the ``rule.rule_key`` column.
MAX_KEY_LENGTH = 96

#: A key is upper-case alphanumeric plus separators, at least three characters.
#: Upper-case because every catalogue code in this system is, and a key that
#: differs from its own code only by case is a key two people will write two ways.
_VALID_KEY = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,}$")


def clean_optional(value: str | None) -> str | None:
    """Treat a blank rule key as absent, because that is what it means.

    A form library sends ``""`` for an optional field the author never touched.
    Declaring the field ``str | None`` accepts ``None`` and rejects ``""``, which
    turns "I did not fill this in" into a validation error against a regex the
    author never saw — and the API is the side that knows better.
    """
    if value is None:
        return None
    return value.strip() or None


def validate_format(value: str) -> str:
    """Check an author-supplied key, and say what is wrong in words.

    Pydantic's ``pattern=`` reports ``String should match pattern
    '^[A-Z0-9][A-Z0-9_.-]{2,79}$'``, which is accurate and useless: it tells an
    author their key is wrong without telling them what a right one looks like,
    and the regex is not something anyone should have to read.
    """
    key = value.strip()
    if len(key) < 3:
        raise ValueError(
            f"'{key}' is too short for a rule key — use at least three characters."
        )
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(
            f"A rule key may be at most {MAX_KEY_LENGTH} characters; this one is "
            f"{len(key)}."
        )
    if not _VALID_KEY.match(key):
        suggestion = suggest(key)
        raise ValueError(
            f"'{key}' is not a valid rule key. Use upper-case letters, digits, "
            f"and _ . : or - — for example '{suggestion}'."
        )
    return key


def derive(draft: CanonicalDraft, *, source_code: str | None = None) -> str:
    """The rule key for a draft, in descending order of trustworthiness.

    1. A key the source stated explicitly — it knows its own identity best.
    2. The source system's own primary key, namespaced by the system, which is
       the real idempotency key for a re-import.
    3. A hash of what the rule *does* — mode, service, type, predicates, action
       types. Deliberately excludes price and validity, so re-pricing a rule
       keeps its identity rather than forking it.
    """
    if draft.rule_key:
        return draft.rule_key.strip()[:MAX_KEY_LENGTH]

    prefix = (source_code or draft.provenance.source_system_code or "MANUAL").upper()
    external = draft.provenance.external_ref
    if external:
        return f"{prefix}:{str(external).strip()}"[:MAX_KEY_LENGTH]
    return f"{prefix}:{fingerprint.natural_key(draft)}"[:MAX_KEY_LENGTH]


def suggest(name: str) -> str:
    """A readable key suggestion for the wizard — ``Peak On-net`` → ``PEAK_ON_NET``.

    A *suggestion* only. The author may edit it, it is uniqueness-checked before
    it is accepted, and nothing derives identity from it behind their back.
    """
    key = _SLUG.sub("_", name.strip().upper()).strip("_")
    return (key or "RULE")[:72]


def unique(suggestion: str, taken: set[str]) -> str:
    """``PEAK`` → ``PEAK_2`` when the key is already in use.

    The legacy path raised a conflict here and made the author invent a key. A
    numeric suffix is what every other tool does, and the author can still
    override it.
    """
    if suggestion not in taken:
        return suggestion
    for n in range(2, 1000):
        candidate = f"{suggestion[: MAX_KEY_LENGTH - 5]}_{n}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"Cannot find a free key based on '{suggestion}'.")
