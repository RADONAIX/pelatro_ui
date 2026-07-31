"""The canonical rule API — HTTP over the ingestion kernel.

Mounted at ``/canonical-rules`` rather than ``/rules`` because both models are
live during the R4 window: ``rating.rules`` still feeds the compiler and the
rating engine, so the legacy surface cannot be taken away yet, and two routers
cannot both own ``POST /rules``.

The path is honest about which store it serves, which during a two-model window
is exactly what a client needs to know. At M5 the legacy ``/rules`` becomes a
thin façade that delegates here, and at M7 it goes — neither step renames
anything a UI has been built against.
"""

from __future__ import annotations
