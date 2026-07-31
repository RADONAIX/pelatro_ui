"""The Snapshot Details screen: four questions, answered from one page.

A snapshot is the single source of truth for what the rating engine is using, so
the page describing one has to say what is in it, what changed, whether it is
safe to activate, and what activating it would do.

The test that matters most is not any single section — it is
``test_the_existing_snapshot_endpoints_are_unchanged``. Every section here was
added beside the existing API rather than through it, and the screens already
built against `/rule-snapshots` must not shift under them.

The second-most-important is that impact is honest about having nothing to
measure. "No rated traffic in this window" and "an impact of zero" look the same
in a number and mean opposite things, and only one of them is ever true of a
platform that has not rated anything yet.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import pytest
from sqlalchemy import select, text

from app.modules.compiler import snapshot_detail as detail
from app.modules.compiler import snapshot_export as export
from app.modules.compiler.models import ExecutableRule, RuleSnapshot
from app.modules.rules.constants import STAGE_ORDER

pytestmark = pytest.mark.asyncio


async def _snapshots(db) -> list[RuleSnapshot]:
    present = (
        await db.execute(
            text("SELECT count(*) FROM pg_tables WHERE tablename = 'rule_snapshots'")
        )
    ).scalar_one()
    if not present:
        pytest.skip("the rating schema is not migrated")
    rows = (
        await db.execute(select(RuleSnapshot).order_by(RuleSnapshot.version.desc()))
    ).scalars().all()
    if not rows:
        pytest.skip("no compiled snapshots to inspect")
    return list(rows)


# --- The guard that matters most --------------------------------------------


def test_the_existing_snapshot_endpoints_are_unchanged():
    """Everything in this feature was added beside the existing API, not through
    it. The screens already built against these paths must not shift."""
    from app.main import create_app

    paths = {r.path for r in create_app().routes}
    for existing in (
        "/api/rating/rule-snapshots",
        "/api/rating/rule-snapshots/active",
        "/api/rating/rule-snapshots/stats",
        "/api/rating/rule-snapshots/diff",
        "/api/rating/rule-snapshots/rollback",
        "/api/rating/rule-snapshots/{snapshot_id}",
        "/api/rating/rule-snapshots/{snapshot_id}/rules",
        "/api/rating/rule-snapshots/{snapshot_id}/activate",
    ):
        assert existing in paths, f"lost existing route {existing}"


def test_the_detail_sections_are_registered():
    from app.main import create_app

    paths = {r.path for r in create_app().routes}
    for added in (
        "/api/rating/rule-snapshots/{snapshot_id}/diff",
        "/api/rating/rule-snapshots/{snapshot_id}/report",
        "/api/rating/rule-snapshots/{snapshot_id}/execution-order",
        "/api/rating/rule-snapshots/{snapshot_id}/impact",
        "/api/rating/rule-snapshots/{snapshot_id}/export",
    ):
        assert added in paths, f"missing route {added}"


def test_the_two_diff_endpoints_do_not_shadow_each_other():
    """`/diff?from_id&to_id` compares any two; `/{id}/diff` compares with the
    predecessor. A literal segment declared after a path parameter would make the
    first unreachable, and Starlette matches in declaration order."""
    from starlette.routing import Match

    from app.main import create_app

    target = "/api/rating/rule-snapshots/diff"
    matched = next(
        r.path
        for r in create_app().routes
        if r.matches(
            {"type": "http", "method": "GET", "path": target,
             "path_params": {}, "headers": [], "root_path": ""}
        )[0]
        == Match.FULL
    )
    assert matched == target


def test_nothing_in_the_detail_module_can_write():
    """A snapshot is immutable once compiled, and a details screen that could
    alter one would undermine the only property that makes it worth trusting."""
    import inspect

    source = inspect.getsource(detail) + inspect.getsource(export)
    for forbidden in ("db.add(", "db.delete(", "session.add(", ".commit()"):
        assert forbidden not in source, f"detail module performs {forbidden}"


# --- Section 3: compare with the previous snapshot --------------------------


async def test_the_previous_snapshot_is_found_by_version(db_session):
    """By version rather than by supersession: a snapshot compiled and never
    activated still has a meaningful predecessor."""
    snapshots = await _snapshots(db_session)
    if len(snapshots) < 2:
        pytest.skip("need two snapshots to compare")
    newest = snapshots[0]
    previous = await detail.previous_snapshot(db_session, newest)
    assert previous is not None
    assert previous.version < newest.version
    assert previous.version == max(
        s.version for s in snapshots if s.version < newest.version
    )


async def test_the_first_snapshot_has_no_predecessor(db_session):
    snapshots = await _snapshots(db_session)
    oldest = snapshots[-1]
    assert await detail.previous_snapshot(db_session, oldest) is None


# --- Section 4: the compile report -------------------------------------------


async def test_the_report_says_whether_activation_is_safe(db_session):
    snapshots = await _snapshots(db_session)
    report = detail.compile_report(snapshots[0])
    assert report.version == snapshots[0].version
    assert report.checksum == snapshots[0].checksum
    # The claim under test: safety is errors *and* the absence of an override.
    assert report.safe_to_activate == (
        report.error_count == 0 and not report.forced
    )


async def test_a_forced_compile_is_never_reported_as_safe(db_session):
    """A forced compile is safe only in the sense that somebody already decided
    it was. The screen should say so rather than agreeing on their behalf."""
    snapshots = await _snapshots(db_session)
    snapshot = snapshots[0]
    original = dict(snapshot.stats or {})
    snapshot.stats = {**original, "forced": True}
    try:
        report = detail.compile_report(snapshot)
        assert report.forced
        assert not report.safe_to_activate
    finally:
        snapshot.stats = original


async def test_report_issues_are_grouped_by_cause(db_session):
    """Forty findings from one check read as one problem, not forty."""
    snapshots = await _snapshots(db_session)
    report = detail.compile_report(snapshots[0])
    if not report.issues:
        pytest.skip("this snapshot compiled cleanly")
    assert len(report.grouped_issues) <= len(report.issues)
    assert sum(g["count"] for g in report.grouped_issues) == len(report.issues)
    # Most-common first, so the biggest fix is at the top.
    counts = [g["count"] for g in report.grouped_issues]
    assert counts == sorted(counts, reverse=True)


# --- Section 7: execution order ---------------------------------------------


async def test_execution_order_follows_the_charging_sequence(db_session):
    snapshots = await _snapshots(db_session)
    groups = await detail.execution_order(db_session, snapshots[0].id)
    orders = [g.stage_order for g in groups]
    assert orders == sorted(orders)


async def test_empty_stages_are_reported_too(db_session):
    """A pipeline with nothing at ROUNDING rounds nothing, and that absence is
    invisible in a list of what is present."""
    snapshots = await _snapshots(db_session)
    groups = await detail.execution_order(db_session, snapshots[0].id)
    assert {g.stage for g in groups} == set(STAGE_ORDER)
    assert any(g.rule_count == 0 for g in groups), "expected some empty stages"


async def test_rules_within_a_stage_are_ranked_the_way_selection_ranks_them(db_session):
    """Specificity first, then priority — because "why did that rule win?" is the
    most common question a snapshot has to answer."""
    snapshots = await _snapshots(db_session)
    groups = await detail.execution_order(db_session, snapshots[0].id, sample=50)
    populated = [g for g in groups if len(g.rules) > 1]
    if not populated:
        pytest.skip("no stage has two rules to rank")
    ranks = [(-r["specificity"], -r["priority"]) for r in populated[0].rules]
    assert ranks == sorted(ranks)


# --- Section 5: impact -------------------------------------------------------


async def test_reach_counts_wildcards_separately_from_named_entities(db_session):
    """Forty rules that apply to every product are a wider blast radius than
    twelve that name one each, and a list of codes alone implies the opposite."""
    snapshots = await _snapshots(db_session)
    result = await detail.impact(db_session, snapshots[0].id)
    products = next(r for r in result.reach if r.dimension == "product_code")
    from sqlalchemy import func

    total = (
        await db_session.execute(
            select(func.count())
            .select_from(ExecutableRule)
            .where(ExecutableRule.snapshot_id == snapshots[0].id)
        )
    ).scalar_one()
    named = (
        await db_session.execute(
            select(func.count())
            .select_from(ExecutableRule)
            .where(
                ExecutableRule.snapshot_id == snapshots[0].id,
                ExecutableRule.product_code.isnot(None),
            )
        )
    ).scalar_one()
    assert products.wildcard_rules == total - named


async def test_impact_is_honest_about_having_no_traffic_to_measure(db_session):
    """"No rated traffic in this window" and "an impact of zero" look the same in
    a number and mean opposite things."""
    snapshots = await _snapshots(db_session)
    result = await detail.impact(db_session, snapshots[0].id)
    assert result.traffic is not None
    if result.traffic.rated_events == 0:
        assert result.traffic.has_traffic is False
        assert result.traffic.affected_events == 0
    else:
        assert result.traffic.has_traffic is True


async def test_impact_narrows_to_what_actually_changed(db_session):
    """Not "how big is this tariff" but "how much of my traffic does this change
    move" — which is the question an operator is really asking."""
    snapshots = await _snapshots(db_session)
    if len(snapshots) < 2:
        pytest.skip("need two snapshots")
    narrowed = await detail.impact(db_session, snapshots[0].id, changed_only=True)
    whole = await detail.impact(db_session, snapshots[0].id, changed_only=False)

    assert narrowed.compared_with_version is not None
    assert whole.compared_with_version is None
    assert len(narrowed.changed_rule_keys) <= narrowed.rule_count


async def test_an_unchanged_snapshot_says_it_moves_nothing(db_session):
    """The most reassuring answer a pre-activation screen can give, and one a
    rule count cannot express."""
    snapshots = await _snapshots(db_session)
    if len(snapshots) < 2:
        pytest.skip("need two snapshots")
    result = await detail.impact(db_session, snapshots[0].id)
    if not result.changed_rule_keys and result.compared_with_version is not None:
        assert "no traffic" in result.note or "moves no traffic" in result.note


# --- Section 6: export -------------------------------------------------------


async def test_csv_export_has_one_row_per_rule(db_session):
    snapshots = await _snapshots(db_session)
    body = await export.to_csv(db_session, snapshots[0])
    lines = [line for line in body.splitlines() if line.strip()]
    assert lines[0].split(",")[0] == "rule_key"
    assert len(lines) - 1 == snapshots[0].rule_count


async def test_csv_keeps_a_rate_readable_rather_than_scientific(db_session):
    """A CSV that renders 0.012345 as 1.2345E-2 because a spreadsheet guessed at
    the type has lost the thing it was exported to preserve."""
    assert export._scalar(0.012345) == "0.012345"
    from decimal import Decimal

    assert export._scalar(Decimal("0.012345")) == "0.012345"
    assert export._scalar(Decimal("20.00")) == "20.00"


async def test_json_export_carries_the_checksum(db_session):
    """An export that cannot be tied back to the snapshot it came from is a
    document with no provenance, and provenance is what this exports for."""
    snapshots = await _snapshots(db_session)
    payload = await export.to_json(db_session, snapshots[0])
    assert payload["snapshot"]["checksum"] == snapshots[0].checksum
    assert payload["snapshot"]["version"] == snapshots[0].version
    assert len(payload["rules"]) == snapshots[0].rule_count


async def test_xml_export_is_well_formed_and_structured(db_session):
    """A tree rather than attributes holding JSON: a compliance archive is read
    by people and by XSLT, and both cope better with real elements."""
    snapshots = await _snapshots(db_session)
    body = await export.to_xml(db_session, snapshots[0])
    root = ET.fromstring(body)
    assert root.tag == "RuleSnapshot"
    assert root.find("Snapshot/Version").text == str(snapshots[0].version)
    assert root.find("Rules").get("count") == str(snapshots[0].rule_count)
    if snapshots[0].rule_count:
        first = root.find("Rules/Rule")
        assert first.find("RuleKey") is not None
        assert first.find("Match") is not None
        assert first.find("Actions") is not None


def test_xml_tags_are_pascal_case():
    """A document full of snake_case reads as a JSON dump wearing brackets."""
    assert export._tag("rule_key") == "RuleKey"
    assert export._tag("destination_zone") == "DestinationZone"
    assert export._tag("") == "Value"


async def test_the_export_filename_is_sortable_and_says_what_it_is(db_session):
    snapshots = await _snapshots(db_session)
    name = export.filename(snapshots[0], "csv")
    assert name.startswith(f"snapshot-v{snapshots[0].version}-")
    assert name.endswith(".csv")
    # The date makes a folder of exports sort chronologically.
    assert date.fromisoformat(name.split("-", 2)[2][:10])
