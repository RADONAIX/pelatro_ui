"""The ingestion kernel — one path into the canonical store.

``writer.py`` is the only module in this service that writes ``ra_rule.*``.
``resolver.py`` batches the code→id lookups a whole run needs. From R5,
``kernel.py`` orchestrates stage → normalize → type → resolve → validate →
reconcile → commit → project, and ``adapters/`` turns each source's shape into the
``CanonicalDraft`` that is the writer's only accepted input.

The reason it is built this way: the legacy service had three write paths — manual
API, file import, connector — each constructing ORM objects itself, each with its
own coercion, its own idea of identity and its own audit behaviour. They diverged,
as three implementations of one thing always do. Collapsing them is not tidiness;
it is the difference between "a rule is stored correctly" being a property of the
system and being a property of whichever path happened to write it.
"""

from __future__ import annotations
