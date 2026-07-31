"""Tenant identity, owned by this service.

The awkward fact this module exists to work around: nothing upstream knows about
tenants. The JWT ``ra_backend`` issues carries ``sub``, ``sid`` and ``type``, and
``administration.users`` is a read-only view we are not allowed to extend. So a
tenant cannot be read from the token or the user record without changing a
service outside this one's remit.

Rather than wait for that, the mapping lives here: a tenant registry and a
membership table in ``ra_rule``, read alongside the role permissions this service
already loads on every request. If a ``tenant_id`` claim ever appears in the
token it is honoured in preference — the membership table is then a fallback
rather than the only answer, and nothing has to be unwound.
"""

from __future__ import annotations
