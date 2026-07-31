"""Additive, write-only mirror of rule and metadata rows into `rafms_rating_new`.

Nothing in this package is on the critical path of a request. The primary write
to `ra_rule` / `rating` happens exactly as it always has; the mirror observes it
afterwards, on its own engine, in its own transaction, and swallows its own
failures.

Three rules hold everywhere in here:

**Never read.** The mirror database is write-only for this platform. No platform
decision may depend on its contents, which is what keeps it impossible for a
mirror problem to change a rating answer.

**Never raise.** Every entry point in ``hooks.py`` is wrapped so that an
unreachable mirror, a schema drift, or a mapping bug produces a log line and
nothing else.

**Never invent.** Where the target schema cannot express what the platform holds
— a nested condition tree, a stage outside its seven, a negation with no
inverse — the row is *skipped and logged*, not approximated. A silently wrong
mirrored rule is worse than an absent one.
"""
