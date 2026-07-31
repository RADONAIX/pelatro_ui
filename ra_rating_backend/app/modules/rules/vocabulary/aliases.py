"""Vendor and spec synonyms, resolved on the way in.

Every operator, every vendor and every spec revision has its own word for the
same thing. Rather than accept three spellings into the store — which is how a
catalogue ends up with `SET_MINIMUM` and `SET_MINIMUM_CHARGE` as two unrelated
action types — the ingestion kernel resolves synonyms to one canonical code and
records what it resolved in ``rule_source_lineage.applied_aliases``.

That last part is the point: the alias is applied, not silently assumed. An
operator asking "why does this say SET_MINIMUM_CHARGE, our file said
SET_MINIMUM?" gets an answer from the lineage tab instead of an argument.
"""

from __future__ import annotations

#: Action code synonyms. The canonical side keeps the explicit ``_CHARGE`` suffix
#: because a minimum *charge* and a minimum *quantity* are different actions and
#: a bare ``SET_MINIMUM`` cannot say which one it means.
ACTION_ALIASES: dict[str, str] = {
    "SET_MINIMUM": "SET_MINIMUM_CHARGE",
    "SET_MAXIMUM": "SET_MAXIMUM_CHARGE",
    "ZERO_RATE": "SET_ZERO_CHARGE",
    "SET_ZERO": "SET_ZERO_CHARGE",
    "APPLY_SURCHARGE": "ADD_SURCHARGE",
    "SET_CONNECT_FEE": "SET_CONNECTION_FEE",
    "DEDUCT": "DEDUCT_BALANCE",
    "RESERVE": "RESERVE_BALANCE",
    "RELEASE": "RELEASE_RESERVATION",
    "ADD_RENTAL": "ADD_RECURRING_CHARGE",
    "ADD_MONTHLY_RENTAL": "ADD_RECURRING_CHARGE",
}

#: Attribute synonyms. The spec names several attributes ``*_id``; the canonical
#: registry keeps code-valued names because the *value* stored is a catalogue
#: code, not a UUID — a rule exported from staging must import into production
#: unchanged, and ids do not survive that trip. The resolved id is stored
#: alongside the code in ``resolved_ref_id``, so both readings are satisfied.
ATTRIBUTE_ALIASES: dict[str, str] = {
    "product_id": "product",
    "offer_id": "offer",
    "tariff_plan_id": "tariff_plan",
    "rating_group_id": "rating_group",
    "destination_zone_id": "destination_zone",
    "time_band_id": "time_band",
    "balance_type_id": "balance_type",
    "billing_cycle_id": "billing_cycle",
    "bundle_id": "bundle",
    "roaming_flag": "roaming",
    "on_net_flag": "on_net",
    "service": "service_type",
    "zone": "destination_zone",
    "msisdn_number": "msisdn",
}

#: Rule-type synonyms. ``USAGE_RATING`` is NOT here — it is a first-class type
#: with ``alias_of='BASE_TARIFF'``, because an operator filtering the catalogue
#: for their postpaid usage rules should see their own word for it.
RULE_TYPE_ALIASES: dict[str, str] = {
    "TARIFF": "BASE_TARIFF",
    "RATE": "BASE_TARIFF",
    "BASE_RATE": "BASE_TARIFF",
    "FREE": "ZERO_RATE",
    "VAT": "TAX",
    "RENTAL": "MONTHLY_RENTAL",
    "SUBSCRIPTION": "RECURRING_CHARGE",
    "OTC": "ONE_TIME_CHARGE",
}

#: Charging-mode synonyms. ``HYBRID`` maps to ``BOTH`` for a *rule*: the legacy
#: ``account_type`` attribute keeps HYBRID because it describes a subscriber who
#: holds both balances, which is a different statement from a rule that is
#: correct either way.
CHARGING_MODE_ALIASES: dict[str, str] = {
    "PRE_PAID": "PREPAID",
    "PRE": "PREPAID",
    "POST_PAID": "POSTPAID",
    "POST": "POSTPAID",
    "HYBRID": "BOTH",
    "ANY": "BOTH",
    "ALL": "BOTH",
}


#: Service-type synonyms. ``ALL`` is the common one and the one that bites: a
#: vendor writing "this rule applies to all services" means our ``ANY``, and
#: rejecting it forces an operator to hand-edit an export to satisfy a spelling
#: preference of ours.
SERVICE_TYPE_ALIASES: dict[str, str] = {
    "ALL": "ANY",
    "*": "ANY",
    "EVERY": "ANY",
    "GENERIC": "ANY",
    "TELEPHONY": "VOICE",
    "CALL": "VOICE",
    "VOICE_CALL": "VOICE",
    "GPRS": "DATA",
    "PS": "DATA",
    "PACKET": "DATA",
    "CS": "VOICE",
    "TEXT": "SMS",
    "MESSAGE": "SMS",
    "MESSAGING": "SMS",
}


def _resolve(table: dict[str, str], token: str | None) -> tuple[str | None, str | None]:
    """Return ``(canonical, alias_applied)``.

    ``alias_applied`` is None when the token was already canonical, so a caller
    can record only the substitutions that actually happened.
    """
    if token is None:
        return None, None
    raw = token.strip()
    if not raw:
        return raw, None
    key = raw.upper()
    canonical = table.get(key)
    if canonical is None:
        return raw, None
    return canonical, raw


def resolve_action(code: str | None) -> tuple[str | None, str | None]:
    return _resolve(ACTION_ALIASES, code)


def resolve_rule_type(code: str | None) -> tuple[str | None, str | None]:
    return _resolve(RULE_TYPE_ALIASES, code)


def resolve_charging_mode(code: str | None) -> tuple[str | None, str | None]:
    return _resolve(CHARGING_MODE_ALIASES, code)


def resolve_service_type(code: str | None) -> tuple[str | None, str | None]:
    return _resolve(SERVICE_TYPE_ALIASES, code)


def resolve_attribute(key: str | None) -> tuple[str | None, str | None]:
    """Attribute keys are lower-case, so this table is matched case-insensitively
    and returns the canonical lower-case key."""
    if key is None:
        return None, None
    raw = key.strip()
    if not raw:
        return raw, None
    canonical = ATTRIBUTE_ALIASES.get(raw.lower())
    if canonical is None:
        return raw.lower() if raw.lower() != raw else raw, None
    return canonical, raw
