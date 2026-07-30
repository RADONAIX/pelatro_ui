"""The canonical condition-attribute registry.

The legacy twenty-four attributes are *imported* rather than retyped, so the two
registries cannot drift apart while both exist — there is one definition of
"Destination Zone scores 45 specificity", and it stays in ``constants.py`` until
the R4 cut-over relocates it.

Nine attributes are added: the ones prepaid and postpaid conditions need, plus
``charging_mode`` itself, which the spec lists first among the example attributes.
"""

from __future__ import annotations

from app.modules.rules.constants import (
    RULE_ATTRIBUTES as _LEGACY_ATTRIBUTES,
)
from app.modules.rules.constants import (
    DataType,
    RuleAttribute,
)
from app.modules.rules.vocabulary.modes import ChargingMode

_A = RuleAttribute

#: Attributes the canonical model adds. Specificity weights are set relative to
#: the existing scale: an attribute that pins one subscriber's bucket is narrow,
#: one that only says "this is a postpaid event" is broad.
NEW_ATTRIBUTES: tuple[RuleAttribute, ...] = (
    _A("charging_mode", "Charging Mode", DataType.ENUM, "Service",
       values=("PREPAID", "POSTPAID", "BOTH"), specificity=30,
       description="Prepaid or postpaid. Usually implied by the rule itself, but "
                   "explicit when one rule set serves both."),
    _A("contract_type", "Contract Type", DataType.ENUM, "Subscriber",
       values=("PREPAID", "POSTPAID", "SIM_ONLY", "BUNDLED"), specificity=20),
    _A("account_status", "Account Status", DataType.ENUM, "Subscriber",
       values=("ACTIVE", "SUSPENDED", "BARRED", "TERMINATED"), specificity=25),
    # --- Prepaid ----------------------------------------------------------
    _A("balance_type", "Balance Type", DataType.REFERENCE, "Prepaid",
       reference="balance-types", specificity=35,
       description="Main, promotional, or a service-specific bucket."),
    _A("balance_amount", "Balance Amount", DataType.NUMBER, "Prepaid", specificity=15,
       description="Current balance at the time of the event — 'if main balance < 5'."),
    _A("bundle", "Bundle", DataType.REFERENCE, "Prepaid", reference="bundles",
       specificity=40),
    _A("session_type", "Session Type", DataType.ENUM, "Prepaid",
       values=("INITIAL", "UPDATE", "TERMINATE"), specificity=20,
       description="Online charging only: which Gy/Ro request this is."),
    # --- Postpaid ---------------------------------------------------------
    _A("billing_cycle", "Billing Cycle", DataType.REFERENCE, "Postpaid",
       reference="billing-cycles", specificity=30),
    _A("invoice_type", "Invoice Type", DataType.ENUM, "Postpaid",
       values=("REGULAR", "FINAL", "INTERIM", "CREDIT_NOTE"), specificity=25),
)

CANONICAL_ATTRIBUTES: tuple[RuleAttribute, ...] = (*_LEGACY_ATTRIBUTES, *NEW_ATTRIBUTES)

CANONICAL_ATTRIBUTE_BY_KEY: dict[str, RuleAttribute] = {
    a.key: a for a in CANONICAL_ATTRIBUTES
}

#: Which charging modes each attribute is offerable for. Absent = every mode.
#: This is what keeps a prepaid author from being shown "Invoice Type" — and,
#: more importantly, what lets the mode-coherence validator reject an imported
#: prepaid rule that conditions on a billing cycle.
ATTRIBUTE_MODE_SCOPE: dict[str, tuple[str, ...]] = {
    "balance_type": (ChargingMode.PREPAID,),
    "balance_amount": (ChargingMode.PREPAID,),
    "session_type": (ChargingMode.PREPAID,),
    "bundle": (ChargingMode.PREPAID, ChargingMode.POSTPAID),
    "billing_cycle": (ChargingMode.POSTPAID,),
    "invoice_type": (ChargingMode.POSTPAID,),
}


def attributes_for_mode(charging_mode: str) -> tuple[RuleAttribute, ...]:
    """Attributes offerable for a charging mode."""
    return tuple(
        a for a in CANONICAL_ATTRIBUTES
        if charging_mode in ATTRIBUTE_MODE_SCOPE.get(a.key, (
            ChargingMode.PREPAID, ChargingMode.POSTPAID, ChargingMode.BOTH,
        ))
    )


#: Catalogue slugs the attribute registry references. Same purpose as the action
#: registry's equivalent: a missing resolver model should fail at startup.
REFERENCED_CATALOGUES: frozenset[str] = frozenset(
    a.reference for a in CANONICAL_ATTRIBUTES if a.reference
)
