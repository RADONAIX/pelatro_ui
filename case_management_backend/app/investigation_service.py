"""Postpaid billing-shock investigation — Billing Assurance only.

Reconstructs what happened to one subscriber in one bill cycle by reading the
canonical rating and billing tables, in the order an analyst would:

    subscriber + invoice  ->  was usage captured?  ->  was it rated correctly?
                          ->  did we already know?  ->  what do we do about it?

Nothing is keyed to a particular case. The subscriber is resolved from whatever
the case actually carries — its MSISDN column, its invoice, or the number in its
own title — so a case raised this morning investigates exactly like one seeded
last month.

Two figures matter and they are not the same number:

    actual   = billing_invoice.total_invoice_amount   (what the customer was
                                                       billed, tax included)
    expected = rating_reconciliation.expected_final_charge

The overcharge is the difference between them. `rating_reconciliation` carries
its own `charge_variance`, but that compares the *pre-tax* rated amount against
the expected final charge and so understates what the customer actually paid.
It is still reported as `sourceVariance` for traceability; every customer-facing
figure uses the invoice.

Data records are out of scope throughout: this is a voice/SMS tariff fault, and
counting data events made the mediation totals look unbalanced when they were
not.

The Billing Assurance gate is enforced once, in `require_billing_case`, and
every entry point goes through it. A Usage, Partner or Network case never
reaches a canonical_rating query: those assurances answer from the mismatch
rows their own control emitted, and joining them to postpaid invoicing would
invent a relationship that does not exist.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.canonical import (
    BILLING_TYPE,
    BillingInvoice,
    MscVsPostMediation,
    RatingReconciliation,
)
from app.catalog import (
    BILLING_ASSURANCE_CODE,
    RATING_ASSURANCE_CODE,
    TERMINAL_STATUSES,
)
from app.config import settings
from app.models import Case

logger = logging.getLogger("ra.investigation")

ZERO = Decimal("0")

#: The rating category whose cases are a detection rather than someone's note —
#: see `related_cases`.
_RATING_ROOT_CAUSE_CATEGORY = "Reconciliation"

#: How many upstream cases the existing-case check lists. The rating control
#: raises one per run, and the newest already says everything the older ones do.
_RELATED_CASE_LIMIT = 1

# Services this investigation covers. Data is excluded deliberately — see the
# module docstring.
BILLABLE_SERVICES = ("VOICE", "SMS")

# A subscriber number embedded in free text — how cases raised before the
# `msisdn` column carry it ("Tariff Mismatch for MSISDN 9876000003"). Ten digits
# minimum, so a case reference (CASE-2067) can never match.
_MSISDN_IN_TEXT = re.compile(r"\b(\d{10,15})\b")


def _num(value: Decimal | None) -> float | None:
    """Numeric -> float at the API edge only; the arithmetic stays exact."""
    return None if value is None else float(value)


def _d(value: Decimal | None) -> Decimal:
    return ZERO if value is None else value


def require_billing_case(db: Session, case_id: str) -> Case:
    """The case, if it exists and belongs to Billing Assurance.

    404 for an unknown case; 404 for a case from another assurance — the
    investigation resource genuinely does not exist for it, and saying so is
    more honest than an empty payload that reads like "no data found for this
    subscriber".
    """
    case = db.execute(
        select(Case).where(or_(Case.id == case_id, Case.reference == case_id.upper()))
    ).scalars().first()
    if case is None:
        raise HTTPException(404, f"Case '{case_id}' not found")
    if case.assurance_code != BILLING_ASSURANCE_CODE:
        raise HTTPException(
            404,
            f"{case.reference} is a {case.assurance_name} case. The postpaid billing "
            "investigation is available for Billing Assurance cases only.",
        )
    return case


def _case_refs(case: Case) -> set[str]:
    """Both spellings of the reference — billing emits CASE2067, we store CASE-2067."""
    return {case.reference, case.reference.replace("-", "")}


def _invoice_for(db: Session, case: Case, msisdn: str | None = None) -> BillingInvoice | None:
    """The invoice for this case, by case reference first and MSISDN second.

    The MSISDN fallback matters for a case raised after the invoice was issued:
    that invoice row predates the case and cannot name it.
    """
    invoice = db.execute(
        select(BillingInvoice).where(BillingInvoice.case_id.in_(_case_refs(case)))
    ).scalars().first()
    if invoice is not None:
        return invoice

    subscriber_no = msisdn or case.msisdn
    if not subscriber_no:
        return None
    return db.execute(
        select(BillingInvoice).where(BillingInvoice.msisdn == subscriber_no)
    ).scalars().first()


def _msisdn_from_text(db: Session, case: Case) -> str | None:
    """A subscriber number written into the case itself.

    Every candidate is checked against the rating table before it is trusted: a
    ten-digit run in a title is only a subscriber if the rating platform has
    heard of it, which keeps an invoice number or a batch id from being adopted
    as an MSISDN.
    """
    for text in (case.title, case.description):
        for candidate in _MSISDN_IN_TEXT.findall(text or ""):
            known = db.execute(
                select(RatingReconciliation.msisdn)
                .where(RatingReconciliation.msisdn == candidate)
                .limit(1)
            ).scalars().first()
            if known:
                return known
    return None


def _subscriber_msisdn(db: Session, case: Case) -> str | None:
    """The MSISDN under investigation, resolved from whatever the case carries.

    In order:
      1. the case's own column, set at creation for Billing Assurance;
      2. the invoice raised against the case;
      3. a subscriber number written into the case title or description;
      4. `settings.demo_msisdn`, so a case that names no subscriber still walks
         the full investigation.

    Step 4 is a DEMO fallback: it shows another subscriber's rating and invoice
    records against a case that has no subscriber of its own. Callers are told
    which step answered via `resolution`, and blanking DEMO_MSISDN disables it.

    The first hit is written back to the case, so a case resolved once is
    properly linked from then on and the derivation happens exactly once.
    """
    if case.msisdn:
        return case.msisdn

    invoice = _invoice_for(db, case)
    resolved = invoice.msisdn if invoice and invoice.msisdn else None
    if resolved is None:
        resolved = _msisdn_from_text(db, case)
    if resolved is None:
        resolved = settings.demo_msisdn.strip() or None

    if resolved:
        # Backfill. The link is real once it resolves, and persisting it keeps
        # every later read — and the related-case search — on the indexed
        # column rather than re-deriving it from text.
        case.msisdn = resolved
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("could not link %s to %s", case.reference, resolved, exc_info=True)
    return resolved


def _rating_for(db: Session, case: Case, msisdn: str | None) -> RatingReconciliation | None:
    """The rated event this case is about.

    Preference order:
      1. an event whose id carries the case reference (CASE2067-E001) — the
         source system tied that event to this case explicitly;
      2. otherwise the subscriber's largest absolute variance among billable
         services, which is the event actually worth investigating, latest
         first on a tie.
    """
    for ref in sorted(_case_refs(case), key=len, reverse=True):
        row = db.execute(
            select(RatingReconciliation).where(RatingReconciliation.event_id.startswith(ref))
        ).scalars().first()
        if row is not None:
            return row

    if not msisdn:
        return None
    return db.execute(
        select(RatingReconciliation)
        .where(
            RatingReconciliation.msisdn == msisdn,
            func.upper(func.coalesce(RatingReconciliation.service_type, "")).in_(BILLABLE_SERVICES),
        )
        .order_by(
            func.abs(func.coalesce(RatingReconciliation.charge_variance, 0)).desc(),
            RatingReconciliation.event_time.desc().nulls_last(),
        )
    ).scalars().first()


def _explanation(row: RatingReconciliation) -> dict[str, Any]:
    """The rating engine's working, defensively typed.

    It is JSONB written by another system; a missing or reshaped key must
    degrade the drill-down, not 500 the investigation.
    """
    raw = row.explanation
    return raw if isinstance(raw, dict) else {}


def _applied_rule(row: RatingReconciliation) -> dict[str, Any]:
    applied = _explanation(row).get("applied_rule")
    return applied if isinstance(applied, dict) else {}


def _root_cause(row: RatingReconciliation) -> dict[str, Any]:
    cause = _explanation(row).get("root_cause")
    return cause if isinstance(cause, dict) else {}


def _billed_amount(
    invoice: BillingInvoice | None, row: RatingReconciliation | None
) -> tuple[Decimal | None, bool]:
    """What the customer was actually billed, and whether it came from the invoice.

    The invoice total including tax is the number on the bill being disputed.
    Only when no invoice exists does this fall back to the rated amount, which
    is pre-tax — the caller reports that distinction rather than hiding it.
    """
    if invoice is not None and invoice.total_invoice_amount is not None:
        return invoice.total_invoice_amount, True
    return (row.actual_charge if row is not None else None), False


# ---------------------------------------------------------------------------
# 1. Subscriber & invoice
# ---------------------------------------------------------------------------


def subscriber(db: Session, case_id: str) -> dict:
    case = require_billing_case(db, case_id)
    msisdn = _subscriber_msisdn(db, case)
    rating_row = _rating_for(db, case, msisdn)
    invoice = _invoice_for(db, case, msisdn)

    return {
        "caseId": case.id,
        "caseReference": case.reference,
        "msisdn": msisdn,
        "available": rating_row is not None or invoice is not None,
        "subscriber": None if rating_row is None else {
            "msisdn": rating_row.msisdn,
            "accountType": rating_row.account_type,
            "offer": rating_row.offer_code,
            # Fixed for this platform: every subscriber here bills on a cycle,
            # and the source tables carry no billing-type column.
            "billingType": BILLING_TYPE,
        },
        "invoice": None if invoice is None else {
            "invoiceId": invoice.invoice_id,
            "usageCharge": _num(invoice.usage_charge),
            "taxAmount": _num(invoice.tax_amount),
            "totalInvoiceAmount": _num(invoice.total_invoice_amount),
            "currency": invoice.currency,
            "createdAt": invoice.created_at,
        },
    }


# ---------------------------------------------------------------------------
# 2. MSC vs post mediation
# ---------------------------------------------------------------------------


def mediation(db: Session, case_id: str) -> dict:
    """Voice and SMS event counts either side of mediation.

    Totals are recomputed as voice + SMS rather than read from
    `*_total_usage_events`, which counts data as well.
    """
    case = require_billing_case(db, case_id)
    msisdn = _subscriber_msisdn(db, case)

    row = None
    if msisdn:
        row = db.execute(
            select(MscVsPostMediation)
            .where(MscVsPostMediation.msisdn == msisdn)
            .order_by(MscVsPostMediation.billing_date.desc())
        ).scalars().first()

    if row is None:
        return {"caseReference": case.reference, "msisdn": msisdn, "available": False}

    msc_voice = row.msc_voice_count or 0
    msc_sms = row.msc_sms_count or 0
    pm_voice = row.pm_voice_count or 0
    pm_sms = row.pm_sms_count or 0
    matched = msc_voice == pm_voice and msc_sms == pm_sms

    return {
        "caseReference": case.reference,
        "msisdn": row.msisdn,
        "available": True,
        "billingDate": row.billing_date,
        "services": list(BILLABLE_SERVICES),
        "msc": {"voice": msc_voice, "sms": msc_sms, "totalEvents": msc_voice + msc_sms},
        "postMediation": {"voice": pm_voice, "sms": pm_sms, "totalEvents": pm_voice + pm_sms},
        # Derived from the counts above rather than read from `result`, so the
        # verdict can never disagree with the rows beside it — and so dropping
        # data cannot leave a stale MATCHED/UNMATCHED contradicting them.
        "result": "PASS" if matched else "FAIL",
        "sourceResult": row.result,
        "finding": (
            "All voice and SMS usage captured and mediated successfully."
            if matched
            else "Voice or SMS event counts differ between the switch and post mediation."
        ),
    }


# ---------------------------------------------------------------------------
# 3. Rating analysis
# ---------------------------------------------------------------------------


def rating(db: Session, case_id: str) -> dict:
    case = require_billing_case(db, case_id)
    msisdn = _subscriber_msisdn(db, case)
    row = _rating_for(db, case, msisdn)

    if row is None:
        return {"caseReference": case.reference, "msisdn": msisdn, "available": False}

    invoice = _invoice_for(db, case, msisdn)
    applied = _applied_rule(row)
    root_cause = _root_cause(row)

    expected_final = _d(row.expected_final_charge)
    billed, from_invoice = _billed_amount(invoice, row)
    variance = _d(billed) - expected_final

    return {
        "caseReference": case.reference,
        "msisdn": row.msisdn,
        "available": True,
        "eventId": row.event_id,
        "currency": row.expected_currency or (invoice.currency if invoice else None),
        "serviceType": row.service_type,
        "destination": row.destination_zone,
        "calledNumber": row.called_number,
        "eventTime": row.event_time,
        "expected": {
            "ruleId": row.base_rule_id,
            "ruleName": row.base_rule_name,
            "rate": _num(row.rate),
            "durationSeconds": row.duration_sec,
            "durationLabel": _duration_label(row.duration_sec),
            "expectedCharge": _num(row.expected_base_charge),
            "discount": _num(row.expected_discount),
            "surcharge": _num(row.expected_surcharge),
            "beforeTax": _num(row.expected_before_tax),
            "tax": _num(row.expected_tax),
            "finalExpectedAmount": _num(row.expected_final_charge),
            "taxRules": row.selected_tax_rules or [],
            "discountRules": row.selected_discount_rules or [],
        },
        "actual": {
            # The applied (wrong) rule is only recorded inside the engine's
            # explanation payload — there is no column for it.
            "appliedRuleId": applied.get("rule_id"),
            "appliedRule": applied.get("rule_name"),
            "appliedRate": applied.get("rate"),
            # What the customer was billed: the invoice total, tax included.
            # This is the actual side of every customer-facing figure.
            "actualCharge": _num(billed),
            "billedFromInvoice": from_invoice,
            # The components behind it, and what rating alone produced.
            "usageCharge": _num(invoice.usage_charge) if invoice else None,
            "taxAmount": _num(invoice.tax_amount) if invoice else None,
            "ratedCharge": _num(row.actual_charge),
            # Recomputed from the two amounts shown, so the row can never
            # disagree with the subtraction a reader does in their head.
            "variance": _num(variance),
            # The source table's own pre-tax comparison, kept for traceability.
            "sourceVariance": _num(row.charge_variance),
        },
        "result": "PASS" if variance == ZERO else "FAIL",
        "sourceStatus": row.reconciliation_status,
        "finding": root_cause.get("description") or "",
        "rootCauseType": root_cause.get("type") or "",
    }


def _duration_label(seconds: int | None) -> str:
    if not seconds:
        return "—"
    minutes, remainder = divmod(int(seconds), 60)
    if not minutes:
        return f"{remainder}s"
    return f"{minutes} min" if not remainder else f"{minutes}m {remainder}s"


# ---------------------------------------------------------------------------
# 4. Existing case check
# ---------------------------------------------------------------------------


def related_cases(db: Session, case_id: str) -> dict:
    """The case that already recorded the fault this bill inherited.

    This looks UPSTREAM rather than sideways. A billing shock is a rating fault
    that reached an invoice — billing added tax to the charge rating produced —
    so the case worth linking is the Rating Assurance control that detected the
    wrong tariff, not another billing case on the same subscriber. Those were
    siblings with the same cause; this is the cause.

    Which is also why it is not matched on MSISDN: the rating control runs over
    the whole rated stream and its case covers every affected subscriber at
    once, so it carries no single number to match against.

    Billing Assurance only — `require_billing_case` rejects anything else, and
    no other assurance has this upstream relationship to reason about.
    """
    case = require_billing_case(db, case_id)
    msisdn = _subscriber_msisdn(db, case)

    rows = db.execute(
        select(Case)
        .where(
            Case.id != case.id,
            Case.assurance_code == RATING_ASSURANCE_CODE,
            # A reconciliation case: the control comparing what was rated
            # against what should have been. A hand-raised rating case is
            # somebody's note, not a detection.
            Case.rule_category == _RATING_ROOT_CAUSE_CATEGORY,
            Case.status.notin_(TERMINAL_STATUSES),
            # Only a case that predates this one. The finding is "the fault was
            # already known and billing ran anyway", which a case opened after
            # the invoice cannot show.
            Case.created_at <= case.created_at,
        )
        # Newest first, capped: the rating control raises a case per run, so an
        # uncapped list would grow with every execution and say nothing more
        # than its most recent entry already does.
        .order_by(Case.created_at.desc())
        .limit(_RELATED_CASE_LIMIT)
    ).scalars().all()

    # Named from THIS subscriber's rating fault rather than the linked case's
    # own description. The upstream case counts its breached rows across every
    # affected subscriber, which says nothing about why this bill is wrong; the
    # root cause does, and it is the same fault in both.
    rating_row = _rating_for(db, case, msisdn)
    issue = _root_cause(rating_row).get("description", "") if rating_row is not None else ""

    cases = [
        {
            "id": c.id,
            "reference": c.reference,
            "title": c.title,
            "issue": issue or c.rule_category or "—",
            "status": c.status,
            "severity": c.severity,
            "owner": c.owner,
            "detectedAt": c.detected_at,
            "createdAt": c.created_at,
            "resolved": c.status in TERMINAL_STATUSES,
        }
        for c in rows
    ]
    unresolved = [c for c in cases if not c["resolved"]]

    return {
        "caseReference": case.reference,
        "msisdn": msisdn,
        "cases": cases,
        "unresolvedCount": len(unresolved),
        "message": (
            "Existing unresolved case found. Billing was generated before correction."
            if unresolved else ""
        ),
        "impact": "Billing generated before correction." if unresolved else "",
    }


# ---------------------------------------------------------------------------
# 5. Recommended actions
# ---------------------------------------------------------------------------


def actions(db: Session, case_id: str) -> dict:
    """Actions derived from the finding, not a fixed list.

    Each is offered only when the data supports it: no refund without a
    positive variance, no tariff correction unless the rule that was applied
    differs from the rule that should have been.
    """
    case = require_billing_case(db, case_id)
    msisdn = _subscriber_msisdn(db, case)
    row = _rating_for(db, case, msisdn)

    if row is None:
        return {"caseReference": case.reference, "msisdn": msisdn, "actions": []}

    invoice = _invoice_for(db, case, msisdn)
    applied = _applied_rule(row)
    billed, _ = _billed_amount(invoice, row)
    expected_final = _d(row.expected_final_charge)
    variance = _d(billed) - expected_final
    currency = row.expected_currency or (invoice.currency if invoice else "") or ""
    amount = f"{currency} {variance:.2f}".strip()

    items: list[dict] = []

    if variance != ZERO:
        items.append({
            "key": "rerate",
            "title": "Re-rate subscriber",
            "detail": f"Re-run rating for {row.msisdn} under "
                      f"{row.base_rule_id} — {row.base_rule_name}.",
            "primary": True,
        })
    if variance > ZERO:
        items.append({
            "key": "refund",
            "title": f"Refund customer ({amount})",
            "detail": f"Credit {amount} against "
                      f"{invoice.invoice_id if invoice else 'the invoice for this cycle'} — "
                      f"billed {currency} {_d(billed):.2f} against an expected "
                      f"{currency} {expected_final:.2f}.",
            "primary": False,
        })

    applied_id = applied.get("rule_id")
    applied_name = applied.get("rule_name")
    if applied_id and applied_id != row.base_rule_id:
        applied_label = f"{applied_id} — {applied_name}" if applied_name else applied_id
        items.append({
            "key": "tariff",
            "title": "Correct tariff rule version",
            "detail": f"Rating applied {applied_label}; repoint "
                      f"{row.offer_code or 'the offer'} at {row.base_rule_id}.",
            "primary": False,
        })

    items.append({
        "key": "impacted",
        "title": "Check impacted subscribers",
        "detail": f"Sweep every {row.service_type or 'rated'} event on "
                  f"{row.offer_code or 'this offer'} rated with "
                  f"{applied_id or 'the same tariff'}.",
        "primary": False,
    })

    return {
        "caseReference": case.reference,
        "msisdn": row.msisdn,
        "currency": currency,
        "variance": _num(variance),
        "actions": items,
    }
