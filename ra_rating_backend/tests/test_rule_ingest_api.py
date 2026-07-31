"""Import APIs (group 4) — files and connectors through the kernel.

The properties tested here are the ones that separate this surface from the
legacy importer next door, and each corresponds to a defect the plan names:

* a preview that runs the *same* code as the commit (D5),
* a re-post of the same file that is one batch rather than two,
* a nightly re-import that cuts no versions for unchanged rules (D4/D7),
* a withdrawal that is proposed rather than applied (D8),
* every incoming record kept verbatim, including the ones we rejected (D6),
* money that is a ``Decimal`` from the cell to the column (D1).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.modules.imports.constants import suggest_mapping
from app.modules.rules.api.ingest_router import _ensure_batch_set_membership
from app.modules.rules.canonical.lineage import RuleIngestionRecord
from app.modules.rules.canonical.logic import RuleParameter
from app.modules.rules.canonical.rule import CanonicalRule
from app.modules.rules.canonical.sets import CanonicalRuleSet, RuleSetMember
from app.modules.rules.ingest import files, kernel
from app.modules.rules.ingest.adapters import payload as payload_adapter
from app.modules.rules.ingest.adapters import tabular
from app.modules.rules.ingest.reconcile import Decision, ImportMode
from app.modules.rules.vocabulary.sync import sync_vocabulary

pytestmark = pytest.mark.asyncio

ACTOR = kernel.Actor("11111111-1111-1111-1111-111111111111", "Import Test")

HEADER = (
    "Rule Name,Service Type,Charging Mode,Destination Zone,Rate,Rate Unit,"
    "Currency,Effective From,Vendor ID\n"
)


def _csv(*rows: str) -> bytes:
    return (HEADER + "".join(r if r.endswith("\n") else r + "\n" for r in rows)).encode()


def _row(name: str, zone: str = "LOCAL_ONNET", rate: str = "0.012345",
         ref: str = "T-1") -> str:
    return f"{name},VOICE,PREPAID,{zone},{rate},MINUTE,GBP,2026-01-01,{ref}"


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


async def _source(db, code: str = "TEST_INGEST_SRC") -> str:
    from app.modules.connectors.models import SourceSystem

    existing = (
        await db.execute(select(SourceSystem).where(SourceSystem.code == code))
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            SourceSystem(
                code=code, name="Ingest test source", vendor="GENERIC",
                category="RULES", source_type="FILE", status="ACTIVE",
            )
        )
        await db.flush()
    return code


async def _ingest(db, data: bytes, **kwargs):
    parsed = files.parse_file(
        "tariff.csv", data, default_effective_from=date(2026, 1, 1),
        source_system_code=kwargs.get("source_system_code"),
    )
    result = await kernel.ingest(
        db, parsed.drafts, actor=ACTOR, channel="FILE",
        filename="tariff.csv", content_hash=parsed.content_hash,
        raw_records=parsed.raw, **kwargs,
    )
    return parsed, result


# --- Parsing and mapping (no database) --------------------------------------


def test_the_mapping_is_suggested_including_the_canonical_only_columns():
    headings = ["Rule Name", "Charging Mode", "Vendor ID", "Rate"]
    mapping = tabular.extend_mapping(headings, suggest_mapping(headings))
    assert mapping["Rule Name"] == "name"
    # Neither of these exists in the legacy registry — they are what the
    # canonical model needs and the legacy shape cannot carry.
    assert mapping["Charging Mode"] == "charging_mode"
    assert mapping["Vendor ID"] == "external_ref"


def test_a_rate_is_decimal_from_the_cell_onwards():
    """The legacy mapper's `_parse_number` returns a float, so 0.012345 is
    already inexact before storage ever sees it."""
    parsed = files.parse_file("t.csv", _csv(_row("Peak")),
                              default_effective_from=date(2026, 1, 1))
    rate = next(
        p for a in parsed.drafts[0].actions for p in a.parameters if p.name == "rate"
    )
    assert rate.raw == Decimal("0.012345")
    assert not isinstance(rate.raw, float)


def test_an_unmapped_heading_is_surfaced_not_swallowed():
    """An unrecognised "Peak Rate" column is the difference between a correct
    import and a catastrophic one, and only the operator can tell which."""
    data = (HEADER.rstrip("\n") + ",Peak Surcharge\n"
            + _row("Peak") + ",0.02\n").encode()
    parsed = files.parse_file("t.csv", data, default_effective_from=date(2026, 1, 1))
    assert "Peak Surcharge" in parsed.unmapped_headings
    # And it reaches the rule's extras rather than being dropped.
    assert "Peak Surcharge" in parsed.drafts[0].provenance.unmapped


def test_a_row_that_cannot_be_read_is_rejected_with_its_position():
    parsed = files.parse_file(
        "t.csv",
        _csv(_row("Good"), "No rate,VOICE,PREPAID,LOCAL_ONNET,,,GBP,2026-01-01,T-2"),
        default_effective_from=date(2026, 1, 1),
    )
    assert len(parsed.drafts) == 1
    assert len(parsed.rejections) == 1
    assert parsed.rejections[0].offset == 1
    assert "no actions" in parsed.rejections[0].message


def test_charging_mode_defaults_to_both_when_the_file_says_nothing():
    """The only value that cannot silently narrow who a rule applies to."""
    data = (
        b"Rule Name,Service Type,Destination Zone,Rate,Rate Unit,Currency,Effective From\n"
        b"Silent,VOICE,LOCAL_ONNET,0.01,MINUTE,GBP,2026-01-01\n"
    )
    parsed = files.parse_file("t.csv", data, default_effective_from=date(2026, 1, 1))
    assert parsed.drafts[0].charging_mode == "BOTH"
    assert any("defaulted to BOTH" in note for note in parsed.notes)


def test_the_rule_type_is_inferred_from_what_the_row_does():
    """A tariff sheet rarely carries a type column."""
    parsed = files.parse_file("t.csv", _csv(_row("Peak")),
                              default_effective_from=date(2026, 1, 1))
    assert parsed.drafts[0].rule_type_code == "BASE_TARIFF"


def test_a_bad_date_names_the_column_it_came_from():
    parsed = files.parse_file(
        "t.csv",
        _csv("Bad date,VOICE,PREPAID,LOCAL_ONNET,0.01,MINUTE,GBP,not-a-date,T-9"),
        default_effective_from=date(2026, 1, 1),
    )
    assert parsed.rejections[0].column == "effective_from"


def test_the_column_catalogue_covers_both_registries():
    keys = {c["key"] for c in files.column_catalogue()}
    assert {"name", "service_type", "rate"} <= keys        # legacy registry
    assert {"charging_mode", "external_ref"} <= keys        # canonical additions


# --- Connector payloads (no database) ---------------------------------------


def test_a_connector_payloads_floats_become_decimals():
    """The vendor adapters coerce with float(). This is where that stops."""
    conversion = payload_adapter.convert(
        {
            "name": "BRM peak", "service_type": "VOICE", "rule_type": "BASE_TARIFF",
            "effective_from": "2026-01-01", "currency_code": "GBP",
            "conditions": [{"attribute": "destination_zone", "operator": "EQUALS",
                            "values": ["LOCAL_ONNET"]}],
            "actions": [{"action_type": "SET_RATE",
                         "params": {"rate": 0.012345, "unit": "MINUTE"}}],
        },
        source_system_code="ORACLE_BRM",
    )
    rate = next(
        p for a in conversion.draft.actions for p in a.parameters if p.name == "rate"
    )
    assert rate.raw == Decimal("0.012345")


def test_the_vendor_adapters_own_key_becomes_the_external_ref():
    """Every vendor adapter emits a `rule_key` it derived from the export's own
    identifiers — that is the identity we take.

    Deliberately not a search through the raw record for something that looks
    like an id: `RATE_PLAN_ID` and `TARIFF_ID` and `CLASS_ID` all exist, they
    mean different things, and picking the wrong one silently merges two rules.
    Surfacing the key is the vendor adapter's job, because only it knows.
    """
    conversion = payload_adapter.convert(
        {
            "name": "BRM peak", "service_type": "VOICE", "rule_type": "BASE_TARIFF",
            "rule_key": "BRM_PLAN_A_PEAK",
            "effective_from": "2026-01-01", "currency_code": "GBP",
            "actions": [{"action_type": "SET_RATE",
                         "params": {"rate": 0.01, "unit": "MINUTE"}}],
        },
        source_system_code="ORACLE_BRM",
        raw={"RATE_PLAN_ID": "RP-88"},
    )
    assert conversion.draft.provenance.external_ref == "BRM_PLAN_A_PEAK"


def test_a_connector_payload_without_actions_is_rejected():
    with pytest.raises(payload_adapter.PayloadError, match="no actions"):
        payload_adapter.convert(
            {"name": "Empty", "service_type": "VOICE", "rule_type": "BASE_TARIFF",
             "effective_from": "2026-01-01", "actions": []}
        )


# --- The kernel path (against a real Postgres) ------------------------------


async def test_an_unregistered_source_fails_the_batch_not_the_rows(db_session):
    """One wrong argument must not look like a thousand wrong rows.

    Before this was a precondition, an unknown source surfaced as N quarantined
    records with a commit error — which reads as "my file is broken" when the
    truth is "that source system does not exist yet".
    """
    await _ready(db_session)
    with pytest.raises(kernel.SourceSystemUnknown, match="not registered"):
        await _ingest(db_session, _csv(_row("Peak")),
                      source_system_code="NOT_A_REGISTERED_SOURCE")


async def test_a_file_import_lands_and_keeps_every_record(db_session):
    await _ready(db_session)
    source = await _source(db_session)
    _, result = await _ingest(
        db_session,
        _csv(
            _row("Ingest peak", "LOCAL_ONNET", "0.012345", "IN-1"),
            _row("Ingest offnet", "LOCAL_OFFNET", "0.05", "IN-2"),
            _row("Ingest broken", "NOT_A_ZONE", "0.02", "IN-3"),
        ),
        source_system_code=source,
    )
    assert result.counts.get(Decision.NEW) == 2
    assert result.counts.get(Decision.REJECTED) == 1

    records = (
        await db_session.execute(
            select(RuleIngestionRecord).where(
                RuleIngestionRecord.batch_id == result.batch_id
            )
        )
    ).scalars().all()
    assert len(records) == 3
    # The rejected row is kept verbatim, which is what makes "what did they
    # actually send us?" answerable without re-running the parser.
    rejected = next(r for r in records if r.decision == Decision.REJECTED)
    assert rejected.raw_payload["Vendor ID"] == "IN-3"
    assert rejected.reason


async def test_money_reaches_the_column_exact(db_session):
    await _ready(db_session)
    source = await _source(db_session)
    _, result = await _ingest(
        db_session, _csv(_row("Ingest exact", rate="0.012345", ref="EX-1")),
        source_system_code=source,
    )
    version_id = next(r.rule_version_id for r in result.records if r.committed)
    stored = (
        await db_session.execute(
            select(RuleParameter.parameter_value_numeric).where(
                RuleParameter.rule_version_id == version_id,
                RuleParameter.parameter_name == "rate",
            )
        )
    ).scalar_one()
    assert stored == Decimal("0.012345")


async def test_re_posting_the_same_file_is_one_batch(db_session):
    await _ready(db_session)
    source = await _source(db_session)
    data = _csv(_row("Ingest idempotent", ref="ID-1"))

    _, first = await _ingest(db_session, data, source_system_code=source)
    _, second = await _ingest(db_session, data, source_system_code=source)
    assert second.batch_id == first.batch_id


async def test_a_replayed_batch_populates_the_new_import_set(db_session):
    """Idempotency must not turn the lifecycle selector into an empty set."""
    await _ready(db_session)
    source = await _source(db_session)
    _, result = await _ingest(
        db_session,
        _csv(_row("Replay membership", ref="REPLAY-SET-1")),
        source_system_code=source,
    )
    rule_set = CanonicalRuleSet(
        tenant_id="00000000-0000-0000-0000-000000000001",
        code="REPLAY_MEMBERSHIP_TEST",
        name="Replay membership test",
        set_type="VENDOR_IMPORT",
    )
    db_session.add(rule_set)
    await db_session.flush()

    for _ in range(2):
        await _ensure_batch_set_membership(
            db_session,
            batch_id=result.batch_id,
            rule_set_id=rule_set.rule_set_id,
            tenant_id=rule_set.tenant_id,
        )

    count = await db_session.scalar(
        select(func.count()).select_from(RuleSetMember).where(
            RuleSetMember.rule_set_id == rule_set.rule_set_id
        )
    )
    assert count == 1


async def test_a_nightly_re_import_cuts_no_versions_for_unchanged_rules(db_session):
    """The decision that keeps a full dump from producing 4,000 versions a night
    and burying the three that moved."""
    await _ready(db_session)
    source = await _source(db_session)
    rows = (_row("Ingest nightly A", ref="NA-1"),
            _row("Ingest nightly B", "LOCAL_OFFNET", "0.05", "NB-1"))

    _, first = await _ingest(db_session, _csv(*rows), source_system_code=source)
    assert first.counts.get(Decision.NEW) == 2

    # Same rules, one re-priced. A different content hash, so not the
    # idempotency path — this is real reconciliation.
    changed = (rows[0], _row("Ingest nightly B", "LOCAL_OFFNET", "0.06", "NB-1"))
    _, second = await _ingest(db_session, _csv(*changed), source_system_code=source)
    assert second.counts.get(Decision.UNCHANGED) == 1
    assert second.counts.get(Decision.CHANGED) == 1
    assert not second.safe_to_auto_commit  # it touched a live price


async def test_a_full_import_proposes_a_withdrawal_and_applies_nothing(db_session):
    """A rule the vendor stopped exporting keeps rating until a human says
    otherwise. The legacy connector retired drafts silently and ignored live
    rules, which is exactly backwards."""
    await _ready(db_session)
    source = await _source(db_session)
    kept = _row("Ingest kept", ref="WK-1")
    dropped = _row("Ingest dropped", "LOCAL_OFFNET", "0.05", "WD-1")

    await _ingest(db_session, _csv(kept, dropped),
                  source_system_code=source, import_mode=ImportMode.FULL)
    _, second = await _ingest(db_session, _csv(kept),
                              source_system_code=source, import_mode=ImportMode.FULL)

    assert second.counts.get(Decision.WITHDRAWN) == 1
    assert not second.safe_to_auto_commit

    still_there = (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.external_ref == "WD-1")
        )
    ).scalar_one()
    assert still_there.status != "RETIRED"


async def test_a_dry_run_writes_neither_rules_nor_records(db_session):
    await _ready(db_session)
    source = await _source(db_session)
    _, result = await _ingest(
        db_session, _csv(_row("Ingest dry", ref="DR-1")),
        source_system_code=source, dry_run=True,
    )
    assert result.counts.get(Decision.NEW) == 1

    await _ready(db_session)
    assert (
        await db_session.execute(
            select(CanonicalRule).where(CanonicalRule.external_ref == "DR-1")
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(RuleIngestionRecord).where(
                RuleIngestionRecord.batch_id == result.batch_id
            )
        )
    ).scalar_one_or_none() is None


async def test_the_wizard_writes_no_ingestion_records(db_session):
    """A hand-authored rule has no upstream record, and a table of invented
    payloads is worse than an empty one because it looks like evidence."""
    await _ready(db_session)
    parsed = files.parse_file("t.csv", _csv(_row("Ingest manual", ref="MN-1")),
                              default_effective_from=date(2026, 1, 1))
    result = await kernel.ingest(
        db_session, parsed.drafts, actor=ACTOR, channel="MANUAL"
    )
    assert (
        await db_session.execute(
            select(RuleIngestionRecord).where(
                RuleIngestionRecord.batch_id == result.batch_id
            )
        )
    ).scalar_one_or_none() is None


# --- Routing ----------------------------------------------------------------


def test_the_import_surface_is_registered():
    from app.main import create_app

    paths = {r.path for r in create_app().routes}
    for expected in (
        "/api/rating/rule-ingest/columns",
        "/api/rating/rule-ingest/preview",
        "/api/rating/rule-ingest/batches",
        "/api/rating/rule-ingest/batches/{batch_id}",
        "/api/rating/rule-ingest/batches/{batch_id}/records",
        "/api/rating/rule-ingest/batches/{batch_id}/rejects.csv",
        "/api/rating/rule-ingest/connectors/{source_id}/sync",
    ):
        assert expected in paths, f"missing route {expected}"


def test_the_legacy_import_surface_is_untouched():
    from app.main import create_app

    paths = {r.path for r in create_app().routes}
    for legacy in (
        "/api/rating/rule-imports",
        "/api/rating/rule-imports/preview",
        "/api/rating/source-systems/{source_id}/import",
    ):
        assert legacy in paths
