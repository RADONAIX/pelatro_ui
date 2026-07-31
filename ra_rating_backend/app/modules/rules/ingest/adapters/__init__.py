"""Source adapters — the only place a foreign shape is understood.

Each adapter's sole contract is to produce :class:`CanonicalDraft` objects.
Everything downstream — typing, resolution, validation, reconciliation, the
write — is identical whatever produced them, which is what stops the three
divergent write paths from reappearing one vendor at a time.

``legacy`` is the first of them, and the odd one out: its source is this
service's own ``rating.rules``. It exists so the R4 backfill runs through the
same kernel as every other import rather than being a one-off script with its
own idea of how a rule is stored — a migration that writes rows a different way
from the code that will maintain them is a migration whose output nobody can
trust.
"""

from __future__ import annotations
