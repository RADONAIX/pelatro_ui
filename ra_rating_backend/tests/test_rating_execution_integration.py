"""End-to-end rating assurance against Postgres.

Runs the §28 worked example through the real batch — enrichment, rule
resolution, calculation, comparison and persistence — and asserts the whole
audit trail, not just the final number. A charge that happens to be right for
the wrong reason is a defect waiting for the next tariff change.

Requires the seeded estate:

    python -m scripts.seed_assurance
    (approve the rules, compile and activate a snapshot)

Skips cleanly when the database or the seed is absent, so the pure-logic suite
still runs anywhere.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.modules.cdr.models import CdrEnriched, SubscriberGroupMembership
from app.modules.compiler import service as snapshot_svc
from app.modules.rating import execution
from app.modules.rating.audit_models import (
    CalculationComponent,
    ComponentType,
    EvaluationStatus,
    RatingResultFinal,
    RatingStatus,
    RejectionReason,
    RuleEvaluationAudit,
)

USAGE_ID = "MSC01-FILE100-3563"
MSISDN = "233241234567"

pytestmark = pytest.mark.asyncio


async def _require_seed(db) -> None:
    usage = (
        await db.execute(select(CdrEnriched).where(CdrEnriched.usage_id == USAGE_ID))
    ).scalar_one_or_none()
    if usage is None:
        pytest.skip("run `python -m scripts.seed_assurance` first")
    if await snapshot_svc.active_snapshot(db) is None:
        pytest.skip("no active rule snapshot — compile and activate one first")


async def _rate(db) -> RatingResultFinal:
    await execution.execute_rating_batch(db, usage_ids=[USAGE_ID], reprocess=True)
    return (
        await db.execute(
            select(RatingResultFinal).where(RatingResultFinal.usage_id == USAGE_ID)
        )
    ).scalar_one()


class TestWorkedExampleEndToEnd:
    async def test_the_expected_charge_is_0_16(self, db_session):
        """§28: 195s − 120s bundle → 90s pulsed → 0.15 → −10% → +15% → 0.16."""
        await _require_seed(db_session)
        result = await _rate(db_session)
        assert result.expected_charge == Decimal("0.160000")
        await db_session.rollback()

    async def test_the_calculation_is_right_step_by_step(self, db_session):
        await _require_seed(db_session)
        await _rate(db_session)

        components = (
            await db_session.execute(
                select(CalculationComponent)
                .where(CalculationComponent.usage_id == USAGE_ID)
                .order_by(CalculationComponent.sequence_number)
            )
        ).scalars().all()
        by_type = {c.component_type: c for c in components}

        # The allowance reduces the quantity; it does not produce its own charge.
        assert by_type[ComponentType.BUNDLE_DEDUCTION].input_quantity == Decimal("75.0000")
        assert by_type[ComponentType.PULSE].input_quantity == Decimal("90.0000")
        assert by_type[ComponentType.BASE_CHARGE].output_amount == Decimal("0.150000")
        assert by_type[ComponentType.DISCOUNT].output_amount == Decimal("0.135000")
        # Tax on the discounted amount, never the other way round (§20).
        assert by_type[ComponentType.TAX].output_amount == Decimal("0.155250")
        assert by_type[ComponentType.ROUNDING].output_amount == Decimal("0.160000")
        await db_session.rollback()

    async def test_the_right_rule_won_and_the_others_say_why_they_lost(self, db_session):
        """§28: R200 SELECTED; R100 and R300 MATCHED_NOT_SELECTED."""
        await _require_seed(db_session)
        await _rate(db_session)

        audit = {
            a.rule_key: a
            for a in (
                await db_session.execute(
                    select(RuleEvaluationAudit).where(
                        RuleEvaluationAudit.usage_id == USAGE_ID
                    )
                )
            ).scalars()
        }
        assert audit["R200_SMART20_OFFNET_PEAK"].evaluation_status == EvaluationStatus.SELECTED
        for key in ("R100_GENERAL_OFFNET", "R300_GOLD_CUSTOMER"):
            assert audit[key].evaluation_status == EvaluationStatus.MATCHED_NOT_SELECTED
            assert audit[key].rejection_reason == RejectionReason.LOWER_PRIORITY
            # The sentence, not just the code — this is what an analyst reads.
            assert "R200_SMART20_OFFNET_PEAK" in audit[key].detail
        await db_session.rollback()

    async def test_no_actual_charge_because_an_msc_record_carries_none(self, db_session):
        """§21: the expected charge stands; the comparison honestly says it has
        nothing to compare against."""
        await _require_seed(db_session)
        result = await _rate(db_session)
        assert result.rating_status == RatingStatus.NO_ACTUAL_CHARGE
        assert result.actual_charge is None
        # Critically NOT zero, and no variance is claimed.
        assert result.variance_amount == Decimal("0")
        assert "no billed amount" in result.explanation
        await db_session.rollback()


class TestOneCdrOneResult:
    """§2 — the constraint the whole design exists to protect."""

    async def test_a_subscriber_in_six_groups_gets_exactly_one_result(self, db_session):
        await _require_seed(db_session)

        groups = int(
            (
                await db_session.execute(
                    select(func.count()).select_from(SubscriberGroupMembership).where(
                        SubscriberGroupMembership.msisdn == MSISDN
                    )
                )
            ).scalar_one()
        )
        assert groups >= 6, "the seed should give this subscriber several groups"

        await _rate(db_session)

        results = int(
            (
                await db_session.execute(
                    select(func.count()).select_from(RatingResultFinal).where(
                        RatingResultFinal.usage_id == USAGE_ID
                    )
                )
            ).scalar_one()
        )
        # Six groups, ONE result. Not six.
        assert results == 1

        usage = (
            await db_session.execute(
                select(CdrEnriched).where(CdrEnriched.usage_id == USAGE_ID)
            )
        ).scalar_one()
        assert len(usage.subscriber_groups) == groups
        assert "GOLD" in usage.subscriber_groups
        await db_session.rollback()


class TestIdempotency:
    """§7 — re-running must correct, never duplicate."""

    async def test_rating_the_same_record_twice_leaves_one_of_everything(self, db_session):
        await _require_seed(db_session)

        await _rate(db_session)
        first = await _counts(db_session)
        await _rate(db_session)
        second = await _counts(db_session)

        assert first == second, f"a second run changed the row counts: {first} -> {second}"
        assert second["results"] == 1
        await db_session.rollback()

    async def test_the_result_is_the_same_both_times(self, db_session):
        await _require_seed(db_session)
        first = (await _rate(db_session)).expected_charge
        second = (await _rate(db_session)).expected_charge
        assert first == second
        await db_session.rollback()


async def _counts(db) -> dict[str, int]:
    async def count(model, column) -> int:
        return int(
            (
                await db.execute(
                    select(func.count()).select_from(model).where(column == USAGE_ID)
                )
            ).scalar_one()
        )

    return {
        "results": await count(RatingResultFinal, RatingResultFinal.usage_id),
        "audit": await count(RuleEvaluationAudit, RuleEvaluationAudit.usage_id),
        "components": await count(CalculationComponent, CalculationComponent.usage_id),
    }


class TestBatchProcessing:
    """§25 — the batch loop itself."""

    async def test_a_batch_reports_what_it_did(self, db_session):
        await _require_seed(db_session)
        summary = await execution.execute_rating_batch(
            db_session, usage_ids=[USAGE_ID], reprocess=True
        )
        assert summary.status == "COMPLETED"
        assert summary.processed_records == 1
        assert summary.rules_loaded > 0
        assert summary.batch_id.startswith("RATE-")
        await db_session.rollback()

    async def test_a_limit_of_zero_processes_nothing(self, db_session):
        await _require_seed(db_session)
        summary = await execution.execute_rating_batch(db_session, limit=0, reprocess=True)
        assert summary.processed_records == 0
        await db_session.rollback()

    async def test_paging_covers_every_record_exactly_once(self, db_session):
        """A batch_size smaller than the work must not skip or repeat rows."""
        await _require_seed(db_session)
        pending = int(
            (
                await db_session.execute(
                    select(func.count()).select_from(CdrEnriched).where(
                        CdrEnriched.usage_id.is_not(None)
                    )
                )
            ).scalar_one()
        )
        summary = await execution.execute_rating_batch(
            db_session, batch_size=7, limit=min(pending, 40), reprocess=True
        )
        assert summary.processed_records == min(pending, 40)
        await db_session.rollback()
