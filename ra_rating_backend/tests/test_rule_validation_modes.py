"""Mode coherence — the tier that makes "one engine, not two" enforceable.

Every test here describes a rule that is *authorable*, *compiles*, and then never
fires. That is the failure class this tier exists for: not a crash, not a bad
number, but a rule that looks correct on screen and does nothing.
"""

from __future__ import annotations

from datetime import date

from app.modules.rules.canonical.draft import (
    CanonicalDraft,
    DraftAction,
    DraftBehaviour,
    DraftCondition,
    DraftConditionGroup,
    DraftParameter,
    DraftValidity,
)
from app.modules.rules.validation import modes
from app.modules.rules.vocabulary.modes import ChargingMode, ExecutionMode
from app.modules.rules.vocabulary.stages import pipeline_for, stage_codes_for
from app.modules.rules.vocabulary.values import ValueType


def _draft(**overrides) -> CanonicalDraft:
    defaults: dict = {
        "rule_name": "Test rule",
        "charging_mode": ChargingMode.PREPAID,
        "rule_type_code": "BASE_TARIFF",
        "service_type": "VOICE",
        "validity": DraftValidity(effective_from=date(2026, 1, 1), currency_code="GBP"),
        "actions": (
            DraftAction(
                "SET_RATE",
                parameters=(
                    DraftParameter("rate", "0.01", ValueType.MONEY, currency="GBP"),
                    DraftParameter("unit", "MINUTE", ValueType.ENUM),
                ),
            ),
        ),
    }
    defaults.update(overrides)
    return CanonicalDraft(**defaults)


def _codes(issues) -> set[str]:
    return {i.code for i in issues}


# --- The pipeline is derived, not branched ----------------------------------


def test_the_pipelines_are_derived_from_one_registry():
    """Prepaid and postpaid share every common stage and no mode-specific one.

    If this ever fails, the "single canonical model" claim has quietly become two
    engines that happen to live in one module.
    """
    common = stage_codes_for(ChargingMode.BOTH)
    prepaid = stage_codes_for(ChargingMode.PREPAID)
    postpaid = stage_codes_for(ChargingMode.POSTPAID)

    assert common < prepaid and common < postpaid
    assert not (prepaid - common) & (postpaid - common)
    # Ordering is the charging sequence, and it must be strictly increasing.
    orders = [s.execution_order for s in pipeline_for(ChargingMode.PREPAID)]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)


# --- Actions against the pipeline (plan I5) ---------------------------------


def test_a_both_rule_cannot_deduct_a_prepaid_balance():
    """The plan's headline example. ``BOTH`` means "correct either way", and
    deducting a prepaid balance is emphatically not correct for a postpaid
    account — but nothing about the rule looks wrong until an invoice is short."""
    issues = modes.check(
        _draft(
            charging_mode=ChargingMode.BOTH,
            rule_type_code="TAX",
            actions=(
                DraftAction("APPLY_TAX", parameters=(
                    DraftParameter("tax_rule", "VAT_20", ValueType.REFERENCE),
                )),
                DraftAction("DEDUCT_BALANCE", parameters=(
                    DraftParameter("balance_type", "MAIN", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert "action_outside_pipeline" in _codes(issues)


def test_a_prepaid_rule_cannot_assemble_an_invoice_line():
    issues = modes.check(
        _draft(
            charging_mode=ChargingMode.PREPAID,
            rule_type_code="BASE_TARIFF",
            actions=(
                DraftAction("ADD_INVOICE_COMPONENT", parameters=(
                    DraftParameter("invoice_component", "USAGE", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert "action_outside_pipeline" in _codes(issues)


def test_a_postpaid_rule_cannot_reserve_session_quota():
    issues = modes.check(
        _draft(
            charging_mode=ChargingMode.POSTPAID,
            rule_type_code="USAGE_RATING",
            actions=(
                DraftAction("RESERVE_BALANCE", parameters=(
                    DraftParameter("balance_type", "MAIN", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert "action_outside_pipeline" in _codes(issues)


def test_a_coherent_prepaid_rule_passes():
    assert modes.check(_draft()) == []


def test_a_coherent_postpaid_rental_passes():
    issues = modes.check(
        _draft(
            charging_mode=ChargingMode.POSTPAID,
            rule_type_code="MONTHLY_RENTAL",
            behaviour=DraftBehaviour(execution_mode=ExecutionMode.OFFLINE),
            actions=(
                DraftAction("ADD_RECURRING_CHARGE", parameters=(
                    DraftParameter("recurring_charge", "RENT_20", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert issues == []


# --- Rule type against mode -------------------------------------------------


def test_a_prepaid_rule_cannot_use_a_postpaid_rule_type():
    issues = modes.check(
        _draft(charging_mode=ChargingMode.PREPAID, rule_type_code="MONTHLY_RENTAL")
    )
    assert "rule_type_wrong_mode" in _codes(issues)


def test_both_requires_a_common_rule_type():
    issues = modes.check(
        _draft(charging_mode=ChargingMode.BOTH, rule_type_code="BALANCE_DEDUCTION")
    )
    assert _codes(issues) & {"rule_type_wrong_mode", "both_requires_common_type"}


def test_an_unknown_charging_mode_is_rejected_before_anything_else():
    issues = modes.check(_draft(charging_mode="HYBRID"))
    assert _codes(issues) == {"unknown_charging_mode"}


# --- Condition attributes against mode --------------------------------------


def test_a_prepaid_rule_conditioning_on_a_billing_cycle_is_warned_about():
    """A warning, not an error: a converged operator may enrich a prepaid event
    with account data we do not model, and blocking that asserts more than we know."""
    issues = modes.check(
        _draft(
            root_group=DraftConditionGroup(
                conditions=(DraftCondition("billing_cycle", "EQUALS", ("MONTHLY",)),)
            )
        )
    )
    assert "attribute_outside_mode" in _codes(issues)
    assert all(i.severity == "WARNING" for i in issues)


# --- Execution mode against stage (plan I2) ---------------------------------


def test_an_offline_rule_cannot_reserve_quota():
    """Reservation only means anything inside a live session. Marked OFFLINE it is
    dead configuration that an operator will nonetheless see listed as active."""
    issues = modes.check(
        _draft(
            rule_type_code="SESSION_RESERVATION",
            behaviour=DraftBehaviour(execution_mode=ExecutionMode.OFFLINE),
            actions=(
                DraftAction("RESERVE_BALANCE", parameters=(
                    DraftParameter("balance_type", "MAIN", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert "offline_rule_at_online_stage" in _codes(issues)


def test_an_online_rule_cannot_add_a_monthly_rental():
    issues = modes.check(
        _draft(
            charging_mode=ChargingMode.POSTPAID,
            rule_type_code="MONTHLY_RENTAL",
            behaviour=DraftBehaviour(execution_mode=ExecutionMode.ONLINE),
            actions=(
                DraftAction("ADD_RECURRING_CHARGE", parameters=(
                    DraftParameter("recurring_charge", "RENT_20", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert "online_rule_at_offline_stage" in _codes(issues)


def test_execution_mode_both_constrains_nothing():
    """The default has to stay permissive, or every rule authored before anyone
    thought about runtimes starts failing validation."""
    issues = modes.check(
        _draft(
            rule_type_code="SESSION_RESERVATION",
            behaviour=DraftBehaviour(execution_mode=ExecutionMode.BOTH),
            actions=(
                DraftAction("RESERVE_BALANCE", parameters=(
                    DraftParameter("balance_type", "MAIN", ValueType.REFERENCE),
                )),
            ),
        )
    )
    assert issues == []
