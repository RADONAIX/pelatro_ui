"""Operating the rule lifecycle on a set rather than on a row.

An import lands five hundred drafts. Moving them to APPROVED one at a time is
not merely tedious — it is a workflow nobody completes, so in practice either
the import sits unused or somebody writes a script that bypasses every check
this system has.

Nothing here is a new lifecycle. The nine statuses, the transitions and the
maker-checker split all exist and all work per rule. This package does two
things: it resolves "which rules" from something an operator can actually name,
and it applies the existing per-rule operation across that set with the safety
properties that only matter at scale — atomicity, maker-checker, and refusing to
make a catastrophic action as cheap as a trivial one.
"""

from __future__ import annotations
