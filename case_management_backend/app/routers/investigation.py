"""Postpaid billing-shock investigation endpoints.

Five reads, one per step of the flow. They are deliberately separate rather
than one composite payload: the UI reveals the steps in order, and a stage that
has no source data should fail on its own instead of emptying the whole
investigation.

Every route is Billing Assurance only — `require_billing_case` (called inside
each service function) 404s anything else before it touches canonical_rating.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import investigation_service as service
from app.db import get_db

router = APIRouter(prefix="/api/cases/{case_id}/investigation", tags=["investigation"])


@router.get("/subscriber")
def get_subscriber(case_id: str, db: Session = Depends(get_db)) -> dict:
    """Subscriber identity and the invoice raised for the cycle."""
    return service.subscriber(db, case_id)


@router.get("/mediation")
def get_mediation(case_id: str, db: Session = Depends(get_db)) -> dict:
    """Event counts at the switch against post-mediation."""
    return service.mediation(db, case_id)


@router.get("/rating")
def get_rating(case_id: str, db: Session = Depends(get_db)) -> dict:
    """Expected rating against what the network actually charged."""
    return service.rating(db, case_id)


@router.get("/cases")
def get_related_cases(case_id: str, db: Session = Depends(get_db)) -> dict:
    """Other cases already raised against the same subscriber."""
    return service.related_cases(db, case_id)


@router.get("/actions")
def get_actions(case_id: str, db: Session = Depends(get_db)) -> dict:
    """Remediation derived from the rating variance."""
    return service.actions(db, case_id)
