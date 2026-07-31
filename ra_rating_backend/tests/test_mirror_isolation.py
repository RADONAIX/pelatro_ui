"""The "no behavioural change" guarantee, as assertions rather than a claim.

The whole design rests on one property: with `MIRROR_ENABLED` false, nothing in
the mirror package does any work, opens any connection, or appears in the
platform's own metadata. These tests fail loudly if a future change quietly
breaks that.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.database import Base
from app.modules.mirror import engine as mirror_engine
from app.modules.mirror import hooks, tables


class _FakeSession:
    """Just enough of AsyncSession for the recorders: they only touch `.info`."""

    def __init__(self) -> None:
        self.info: dict = {}


def test_the_mirror_is_off_by_default():
    # Asserted against the FIELD DEFAULT, not the loaded value: a developer whose
    # own .env enables the mirror must not turn this guarantee green-by-accident
    # for everyone else. What matters is that a deployment which has never heard
    # of the feature behaves as it did before.
    fields = type(settings).model_fields
    assert fields["mirror_enabled"].default is False
    assert fields["mirror_subscriber_enabled"].default is False


def test_no_engine_is_constructed_while_disabled(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", False)
    session = _FakeSession()
    hooks.record_rule_version(session, "v1")
    hooks.record_prefix(session, "p1")
    hooks.record_prefix_delete(session, "233", "LOCAL")
    assert session.info == {}
    assert mirror_engine.is_open() is False


@pytest.mark.asyncio
async def test_drain_is_a_no_op_while_disabled(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", False)
    session = _FakeSession()
    assert await hooks.drain(session) == 0
    assert mirror_engine.is_open() is False


def test_recording_while_enabled_only_touches_session_info(monkeypatch):
    # The recorder must stay pure bookkeeping — no I/O on the request path.
    monkeypatch.setattr(settings, "mirror_enabled", True)
    monkeypatch.setattr(settings, "mirror_rule_source", "CANONICAL")
    session = _FakeSession()
    hooks.record_rule_version(session, "v1")
    assert session.info["_mirror_queue"] == [(hooks.RULE_VERSION, "v1")]
    assert mirror_engine.is_open() is False


# --- Rule source selection ---------------------------------------------------
#
# Both rule models are live during the cut-over and describe the same estate.
# Mirroring both would put one logical rule in the target twice, under two
# different rule_ids, which reads as a duplicated tariff.


def test_auto_follows_the_compiler_source(monkeypatch):
    monkeypatch.setattr(settings, "mirror_rule_source", "AUTO")
    monkeypatch.setattr(settings, "rule_compile_source", "LEGACY")
    assert hooks.rule_source() == "LEGACY"
    monkeypatch.setattr(settings, "rule_compile_source", "CANONICAL")
    assert hooks.rule_source() == "CANONICAL"


def test_the_unselected_rule_model_is_not_recorded(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", True)
    monkeypatch.setattr(settings, "mirror_rule_source", "LEGACY")
    session = _FakeSession()
    hooks.record_rule_version(session, "v1")  # canonical — suppressed
    assert session.info.get("_mirror_queue") is None
    hooks.record_legacy_rule(session, "lr1")
    assert session.info["_mirror_queue"] == [(hooks.LEGACY_RULE, "lr1")]


def test_both_records_either_model(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", True)
    monkeypatch.setattr(settings, "mirror_rule_source", "BOTH")
    session = _FakeSession()
    hooks.record_rule_version(session, "v1")
    hooks.record_legacy_rule(session, "lr1")
    assert session.info["_mirror_queue"] == [
        (hooks.RULE_VERSION, "v1"),
        (hooks.LEGACY_RULE, "lr1"),
    ]


def test_only_time_bands_are_recorded_from_the_generic_catalogue(monkeypatch):
    from app.modules.catalog.models import Currency, TimeBand

    monkeypatch.setattr(settings, "mirror_enabled", True)
    session = _FakeSession()
    hooks.record_catalog_entity(session, Currency, "c1")
    assert session.info.get("_mirror_queue") is None
    hooks.record_catalog_entity(session, TimeBand, "t1")
    assert session.info["_mirror_queue"] == [(hooks.TIME_BAND, "t1")]


def test_subscriber_tables_stay_behind_their_own_flag(monkeypatch):
    monkeypatch.setattr(settings, "mirror_enabled", True)
    monkeypatch.setattr(settings, "mirror_subscriber_enabled", False)
    session = _FakeSession()
    hooks.record_bundle_balance(session, "b1")
    hooks.record_subscriber_offer(session, "s1")
    assert session.info.get("_mirror_queue") is None


def test_mirror_tables_are_not_on_the_platforms_metadata():
    # If they were, Alembic autogenerate and the test bootstrap's create_all
    # would try to build `canonical_rating` inside the primary database — a
    # behavioural change, and the reason these are Core tables on their own
    # MetaData rather than ORM models on Base.
    platform = set(Base.metadata.tables)
    mirrored = {t.name for t in tables.metadata.tables.values()}
    assert mirrored & platform == set()
    assert tables.metadata is not Base.metadata


def test_the_ten_target_tables_are_all_defined():
    expected = {
        "rating_rule",
        "rating_rule_condition",
        "rating_rule_action",
        "rule_action_type",
        "rule_operator",
        "rule_attribute",
        "subscriber_offer",
        "subscriber_bundle_balance",
        "destination_prefix",
        "time_band",
    }
    assert {t.name for t in tables.metadata.tables.values()} == expected


@pytest.mark.asyncio
async def test_a_failing_mirror_write_never_escapes(monkeypatch):
    """The fail-open guarantee: an unreachable mirror is a log line, not a 500."""
    monkeypatch.setattr(settings, "mirror_enabled", True)
    monkeypatch.setattr(settings, "mirror_rule_source", "CANONICAL")

    async def _boom(*_args, **_kwargs):
        raise ConnectionRefusedError("mirror database is down")

    from app.modules.mirror import sync

    monkeypatch.setattr(sync, "push_rule_version", _boom)
    session = _FakeSession()
    hooks.record_rule_version(session, "v1")
    # No exception, and the queue is consumed rather than left to grow.
    assert await hooks.drain(session) == 1
    assert session.info.get("_mirror_queue") is None


# --- Vocabulary self-healing -------------------------------------------------
#
# The lookup tables are projections of Python registries, seeded at startup. If
# they are truncated, or the mirror database is recreated, or the service booted
# before the mirror was reachable, then every rule write fails on a foreign key
# and KEEPS failing until someone restarts the process. Recovering without a
# restart is the difference between a blip and a mirror that silently stopped.


@pytest.mark.asyncio
async def test_ensure_reconciles_once_per_process(monkeypatch):
    from app.modules.mirror import vocabulary

    calls = []

    async def _fake_reconcile(_db):
        calls.append(1)
        return {"rule_attribute": 1}

    monkeypatch.setattr(vocabulary, "reconcile", _fake_reconcile)
    vocabulary.invalidate()

    assert await vocabulary.ensure(object()) is True
    assert await vocabulary.ensure(object()) is False  # cached
    assert len(calls) == 1

    vocabulary.invalidate()
    assert await vocabulary.ensure(object()) is True
    assert len(calls) == 2
    vocabulary.invalidate()


@pytest.mark.asyncio
async def test_a_foreign_key_failure_reseeds_the_vocabulary_and_retries(monkeypatch):
    """The exact production failure: `fk_action_type` on an empty lookup table."""
    from sqlalchemy.exc import IntegrityError

    from app.modules.mirror import sync, vocabulary

    attempts = {"n": 0}
    reseeded = {"n": 0}

    class _Mirror:
        async def execute(self, *_a, **_k):
            return None

    class _Ctx:
        async def __aenter__(self):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise IntegrityError("insert", {}, Exception("fk_action_type"))
            return _Mirror()

        async def __aexit__(self, *_a):
            return False

    async def _ensure(_db):
        reseeded["n"] += 1
        return True

    monkeypatch.setattr(sync, "mirror_session", lambda: _Ctx())
    monkeypatch.setattr(vocabulary, "ensure", _ensure)

    await sync._write_rule({"rule_id": "r1"}, [], [])

    assert attempts["n"] == 2, "the write should be retried exactly once"
    assert reseeded["n"] == 1, "the vocabulary should be re-seeded on the retry"


@pytest.mark.asyncio
async def test_a_second_failure_is_not_retried_again(monkeypatch):
    # Two failures mean the rule is the problem, not the lookups. Retrying
    # further would only delay the log line that says so.
    from sqlalchemy.exc import IntegrityError

    from app.modules.mirror import sync, vocabulary

    attempts = {"n": 0}

    class _Ctx:
        async def __aenter__(self):
            attempts["n"] += 1
            raise IntegrityError("insert", {}, Exception("fk_action_type"))

        async def __aexit__(self, *_a):
            return False

    async def _ensure(_db):
        return True

    monkeypatch.setattr(sync, "mirror_session", lambda: _Ctx())
    monkeypatch.setattr(vocabulary, "ensure", _ensure)

    with pytest.raises(IntegrityError):
        await sync._write_rule({"rule_id": "r1"}, [], [])
    assert attempts["n"] == 2


# --- Vocabulary rows satisfy the target's CHECK constraints ------------------


def test_generated_vocabulary_rows_satisfy_the_target_checks():
    from app.modules.mirror import valuemaps as vm
    from app.modules.mirror import vocabulary

    attr_types = {"STRING", "INTEGER", "DECIMAL", "BOOLEAN", "DATE", "TIMESTAMP", "ARRAY"}
    for row in vocabulary.attribute_rows():
        assert row["data_type"] in attr_types
        assert row["attribute_description"]  # NOT NULL
        assert len(row["attribute_name"]) <= 100

    for row in vocabulary.operator_rows():
        assert row["operator_name"]  # NOT NULL
        assert len(row["operator_code"]) <= 30

    for row in vocabulary.action_type_rows():
        assert row["rule_stage"] in vm.TARGET_STAGES
        assert row["action_name"]  # NOT NULL
        assert len(row["action_type"]) <= 50


def test_every_operator_the_platform_can_store_is_registered():
    # `fk_condition_operator` means an unregistered operator is a failed insert.
    # Every platform operator must translate to a target code, and every target
    # code the translation can emit must be seeded.
    from app.modules.mirror import targetvocab, vocabulary
    from app.modules.rules.constants import Operator

    assert {str(o) for o in Operator} <= set(targetvocab.OPERATOR_MAP)
    seeded = {r["operator_code"] for r in vocabulary.operator_rows()}
    assert set(targetvocab.OPERATOR_MAP.values()) <= seeded


def test_every_attribute_the_writer_accepts_is_registered():
    # `fk_condition_attribute` — same reasoning: whatever name a condition can
    # land on (mapped target name or pass-through) must be a seeded row.
    from app.modules.mirror import targetvocab, vocabulary
    from app.modules.rules.vocabulary.attributes import CANONICAL_ATTRIBUTES

    seeded = {r["attribute_name"] for r in vocabulary.attribute_rows()}
    for attr in CANONICAL_ATTRIBUTES:
        target_name, _, _ = targetvocab.attribute_target(attr.key)
        assert target_name in seeded, target_name


def test_the_seeded_vocabulary_is_the_operators_reference_data():
    # The lookup tables belong to the target's own model: the 14-action registry
    # with its handler names, the operator's 19 attribute rows verbatim, and the
    # short operator codes its sample conditions use — never the platform's
    # registries projected across.
    from app.modules.mirror import vocabulary

    actions = {r["action_type"]: r for r in vocabulary.action_type_rows()}
    assert len(actions) == 14
    assert actions["SET_RATE"]["handler_name"] == "BaseRateHandler"
    assert actions["CONSUME_ALLOWANCE"]["rule_stage"] == "ALLOWANCE"
    assert "SET_RATING_UNIT" in actions and "APPLY_PERCENT_TAX" in actions

    operators = {r["operator_code"] for r in vocabulary.operator_rows()}
    assert {"EQ", "NEQ", "GT", "LT", "GTE", "LTE"} <= operators
    assert "EQUALS" not in operators

    attributes = {r["attribute_name"]: r for r in vocabulary.attribute_rows()}
    assert attributes["duration_sec"]["source_entity"] == "msc_usage_event"
    assert attributes["bundle_code"]["source_entity"] == "subscriber_bundle_balance"
    # Pass-throughs are visibly foreign: no source entity.
    assert attributes["tariff_plan"]["source_entity"] is None
