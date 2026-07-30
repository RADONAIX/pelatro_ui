"""Missing metadata: reported as a shape, and creatable only where that is safe.

An import that says "'STANDARD' does not exist in the offers catalogue" once per
row has told the truth and given the operator nothing to act on. Forty rows
failing on one missing offer and forty rows failing on forty missing zones look
identical in that report and are completely different problems — one is a
five-second fix, the other is a data exercise.

The other half is knowing when *not* to help. A placeholder is only acceptable
where it is inert: a destination zone with no prefixes matches nothing, so rules
referencing it simply do not fire. A time band is the opposite — its hours decide
which events fall inside it, so a stub does not wait to be completed, it
mis-classifies traffic immediately.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.modules.catalog import models as cm
from app.modules.rules.ingest import files, references, resolver
from app.modules.rules.vocabulary.sync import sync_vocabulary

pytestmark = pytest.mark.asyncio

HEADER = (
    "rule_key,name,charging_mode,rule_type,service_type,product,offer,tariff_plan,"
    "destination_zone,time_band,rate,currency,unit,priority,effective_from\n"
)


def _csv(*rows: str) -> bytes:
    return (HEADER + "".join(r + "\n" for r in rows)).encode()


def _row(key: str, *, product="", offer="", plan="", zone="LOCAL_ONNET", band=""):
    return (
        f"{key},{key} rule,PREPAID,BASE_TARIFF,VOICE,{product},{offer},{plan},"
        f"{zone},{band},0.01,GBP,MINUTE,100,2026-01-01"
    )


async def _ready(db) -> None:
    present = (
        await db.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'ra_rule'")
        )
    ).scalar_one()
    if not present:
        pytest.skip("ra_rule is not migrated (run alembic upgrade head)")
    await sync_vocabulary(db)
    await db.flush()


async def _survey(db, data: bytes):
    parsed = files.parse_file("t.csv", data, default_effective_from=date(2026, 1, 1))
    cache = await resolver.build(db, settings.default_tenant_id, with_rule_index=False)
    return parsed, await references.survey(db, parsed.drafts, cache)


# --- Reporting ---------------------------------------------------------------


async def test_missing_codes_are_grouped_by_catalogue(db_session):
    """Not one line per row. The operator needs the shape of the problem."""
    await _ready(db_session)
    _, missing = await _survey(
        db_session,
        _csv(
            _row("G1", plan="NO_SUCH_PLAN"),
            _row("G2", plan="NO_SUCH_PLAN"),
            _row("G3", plan="NO_SUCH_PLAN"),
        ),
    )
    plans = next(m for m in missing if m.catalogue == "tariff-plans")
    assert plans.codes == ["NO_SUCH_PLAN"]
    # One missing plan, three blocked rules — the distinction that matters.
    assert plans.affected_rules == 3


async def test_catalogues_are_ordered_by_how_much_they_block(db_session):
    """Fix the one blocking forty rows before the one blocking one."""
    await _ready(db_session)
    _, missing = await _survey(
        db_session,
        _csv(
            _row("O1", plan="MISSING_PLAN"),
            _row("O2", plan="MISSING_PLAN"),
            _row("O3", plan="MISSING_PLAN", zone="MISSING_ZONE"),
        ),
    )
    assert [m.affected_rules for m in missing] == sorted(
        (m.affected_rules for m in missing), reverse=True
    )


async def test_a_batch_with_nothing_missing_reports_nothing(db_session):
    """The report must be silent when there is nothing to say, or it becomes
    noise an operator learns to scroll past."""
    await _ready(db_session)
    _, missing = await _survey(db_session, _csv(_row("OK", zone="LOCAL_ONNET")))
    assert missing == []


# --- What may and may not be stubbed ----------------------------------------


def test_a_placeholder_is_only_allowed_where_it_is_inert():
    """The test is not "has a code and a name" but "does a stub change what gets
    priced". A zone with no prefixes matches nothing; a time band with invented
    hours mis-classifies traffic from the moment it exists."""
    assert "destination-zones" in references.CREATABLE
    assert "products" in references.CREATABLE
    for unsafe in ("time-bands", "offers", "bundles", "currencies", "tax-rules",
                   "rounding-rules", "discounts"):
        assert unsafe not in references.CREATABLE, unsafe


def test_every_refusal_explains_itself_in_actionable_terms():
    """A refusal with no reason is indistinguishable from a bug."""
    for slug, reason in references._NOT_CREATABLE.items():
        assert len(reason) > 40, slug
        assert reason.rstrip().endswith("."), slug


async def test_an_offer_is_refused_because_it_needs_a_product(db_session):
    """The structural dependency, not a limitation of the stub builder: an offer
    has a NOT NULL product_id, and this file does not say which product."""
    await _ready(db_session)
    _, missing = await _survey(db_session, _csv(_row("OF", offer="NO_SUCH_OFFER")))
    offers = next(m for m in missing if m.catalogue == "offers")
    assert not offers.creatable
    assert "belongs to a product" in offers.reason


# --- Creating them -----------------------------------------------------------


async def test_stubs_are_created_only_for_the_safe_catalogues(db_session):
    await _ready(db_session)
    parsed, missing = await _survey(
        db_session,
        _csv(
            _row("S1", product="STUB_PRODUCT", zone="STUB_ZONE"),
            _row("S2", offer="STUB_OFFER", band="STUB_BAND"),
        ),
    )
    created = await references.create_stubs(
        db_session, missing, parsed.drafts,
        actor_id=None, source_label="a test",
    )
    assert "STUB_PRODUCT" in created.get("products", [])
    assert "STUB_ZONE" in created.get("destination-zones", [])
    # And nothing was invented where a placeholder would be wrong.
    assert "offers" not in created
    assert "time-bands" not in created
    await db_session.rollback()


async def test_a_stub_is_marked_as_auto_created(db_session):
    """An operator must be able to tell what they defined from what an import
    invented on their behalf."""
    await _ready(db_session)
    parsed, missing = await _survey(db_session, _csv(_row("M1", product="MARKED_PRODUCT")))
    await references.create_stubs(
        db_session, missing, parsed.drafts, actor_id=None, source_label="a test",
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(cm.Product).where(cm.Product.code == "MARKED_PRODUCT")
        )
    ).scalar_one()
    assert row.source_system == "FILE_IMPORT"
    assert row.attributes.get("auto_created") is True
    assert "Review and complete" in row.description
    await db_session.rollback()


async def test_a_tariff_plan_stub_takes_its_currency_from_the_file(db_session):
    """`tariff_plans.currency_code` is NOT NULL, and the file states it. Read
    rather than defaulted — a guessed currency is a wrong answer that looks like
    a placeholder."""
    await _ready(db_session)
    parsed, missing = await _survey(db_session, _csv(_row("C1", plan="CURRENCY_PLAN")))
    await references.create_stubs(
        db_session, missing, parsed.drafts, actor_id=None, source_label="a test",
    )
    await db_session.flush()

    plan = (
        await db_session.execute(
            select(cm.TariffPlan).where(cm.TariffPlan.code == "CURRENCY_PLAN")
        )
    ).scalar_one()
    assert plan.currency_code == "GBP"
    assert plan.service_type == "VOICE"
    await db_session.rollback()


async def test_creating_stubs_unblocks_the_rules_that_needed_them(db_session):
    """The point of the exercise. After creation the survey is quiet about the
    catalogues that were filled and still loud about the ones that were not."""
    await _ready(db_session)
    data = _csv(_row("U1", product="UNBLOCK_PRODUCT", zone="UNBLOCK_ZONE"))
    parsed, missing = await _survey(db_session, data)
    assert missing

    await references.create_stubs(
        db_session, missing, parsed.drafts, actor_id=None, source_label="a test",
    )
    await db_session.flush()

    _, after = await _survey(db_session, data)
    assert after == []
    await db_session.rollback()


# --- The columns that used to be ignored -------------------------------------


@pytest.mark.parametrize(
    ("column", "value", "expected_action", "mode"),
    [
        ("pulse_seconds", "60", "SET_PULSE", "BOTH"),
        ("tax_percentage", "20", "APPLY_TAX", "BOTH"),
        ("rounding_scale", "2", "APPLY_ROUNDING", "BOTH"),
        # Postpaid, because a recurring charge only exists in a bill run — a
        # BOTH rule carrying one is refused by mode coherence, correctly.
        ("recurring_charge", "20.00", "ADD_RECURRING_CHARGE", "POSTPAID"),
    ],
)
def test_the_columns_that_produced_no_actions_now_produce_one(
    column, value, expected_action, mode
):
    """Each of these was silently ignored, so a row carrying only it was rejected
    as "produced no actions" — which sends an operator looking for a rate column
    they never meant to supply."""
    header = f"rule_key,name,charging_mode,service_type,currency,{column},effective_from\n"
    row = f"K1,Rule,{mode},ANY,GBP,{value},2026-01-01\n"
    parsed = files.parse_file(
        "t.csv", (header + row).encode(), default_effective_from=date(2026, 1, 1)
    )
    assert not parsed.rejections, [r.message for r in parsed.rejections]
    assert [a.action_type for a in parsed.drafts[0].actions] == [expected_action]


def test_an_inline_tax_rate_no_longer_needs_a_catalogue_rule():
    """A tariff sheet stating "VAT 20%" had nowhere to go: APPLY_TAX required a
    catalogue tax rule, so importing a tariff meant building a tax catalogue
    first."""
    from app.modules.rules.vocabulary.actions import ACTION_BY_CODE

    params = {p.key: p for p in ACTION_BY_CODE["APPLY_TAX"].params}
    assert "rate_percent" in params
    assert not params["tax_rule"].required


def test_a_mode_incoherent_action_column_names_the_column_to_change():
    """A recurring charge on a BOTH rule is genuinely incoherent — it only exists
    in a bill run. The message must point at the Charging Mode column rather than
    at the rule type, because that is the cell the operator has to edit."""
    header = "rule_key,name,charging_mode,service_type,currency,recurring_charge,effective_from\n"
    parsed = files.parse_file(
        "t.csv",
        (header + "K2,Rule,BOTH,ANY,GBP,20.00,2026-01-01\n").encode(),
        default_effective_from=date(2026, 1, 1),
    )
    rejection = parsed.rejections[0]
    assert rejection.column == "charging_mode"
    assert "POSTPAID" in rejection.message
