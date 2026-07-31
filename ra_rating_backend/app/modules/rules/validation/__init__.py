"""Rule validation — the legacy tier and the canonical tiers, side by side.

``validate_rule`` still validates a legacy ``rating.rules`` row and is still what
the compiler, the file importer and the rule service call. It is re-exported here
unchanged so that turning ``validation.py`` into a package broke no caller: every
existing ``from app.modules.rules import validation`` keeps working, and the
module it now resolves to has the same public name attached to the same function.

The canonical tiers validate a :class:`CanonicalDraft` — the shape everything
arriving through the ingestion kernel takes — and add the one check the legacy
validator has no vocabulary for: **mode coherence**. The two coexist until the
R4 cut-over repoints the compiler at the canonical store, at which point
``legacy`` goes.
"""

from __future__ import annotations

from app.modules.rules.validation.issues import Issue, Report, error, warn
from app.modules.rules.validation.legacy import validate_rule
from app.modules.rules.validation.registry import (
    check_draft,
    check_offline,
    persist,
)

__all__ = [
    "Issue",
    "Report",
    "check_draft",
    "check_offline",
    "error",
    "persist",
    "validate_rule",
    "warn",
]
