"""Vendor adapters: native export → canonical rule. Pure logic, no I/O."""

from __future__ import annotations

import pytest

from app.modules.connectors import adapters, service
from app.modules.connectors.adapters import AdapterError, map_records

# --- Real-shaped vendor exports ---------------------------------------------

ORACLE_BRM = {
    "rate_plans": [
        {
            "rate_plan_name": "GSM Voice Standard",
            "product_name": "PREPAID_A",
            "service_type": "/service/telco/gsm/voice",
            "impact_category": "LOCAL_ONNET",
            "time_model": "PEAK",
            "rum": "duration",
            "quantity": 60,
            "amount": 0.10,
            "beat": 60,
            "currency": "GBP",
            "start_t": "2026-01-01",
        },
        {
            "rate_plan_name": "GSM Voice Standard",
            "product_name": "PREPAID_A",
            "service_type": "/service/telco/gsm/voice",
            "impact_category": "INTERNATIONAL",
            "rum": "duration",
            "quantity": 60,
            "amount": 0.85,
            "currency": "GBP",
        },
    ]
}

ERICSSON_CS = {
    "tariffClasses": [
        {
            "tariffClassId": "TC_1001",
            "tariffClassName": "Prepaid Voice Domestic",
            "serviceType": "TELEPHONY",
            "offerName": "PREPAID_A_LOYALTY",
            "currency": "GBP",
            "validFrom": "2026-01-01",
            "rateSteps": [
                {
                    "destinationGroup": "LOCAL_ONNET",
                    "timeBand": "PEAK",
                    "ratePerUnit": 0.10,
                    "chargingUnit": 60,
                    "firstInterval": 60,
                    "nextInterval": 30,
                    "minimumCharge": 0.05,
                },
                {
                    "destinationGroup": "LOCAL_OFFNET",
                    "timeBand": "OFF_PEAK",
                    "ratePerUnit": 0.06,
                    "chargingUnit": 60,
                    "firstInterval": 60,
                },
            ],
        }
    ]
}

HUAWEI_CBS = {
    "pricingPlans": [
        {
            "pricingPlanId": "PP2001",
            "pricingPlanName": "Prepaid A Voice",
            "productOfferingId": "PREPAID_A",
            "serviceFlag": "1",
            "currency": "GBP",
            "effDate": "20260101",
            "ratingSegments": [
                {
                    "areaCode": "LOCAL_ONNET",
                    "timeSegment": "PEAK",
                    "unitPrice": 0.10,
                    "unitSize": 60,
                    "pulseSize": 60,
                    "taxSchema": "VAT_STANDARD",
                }
            ],
        }
    ]
}


# --- Registry ---------------------------------------------------------------


def test_three_named_vendors_have_adapters():
    for code in ("ORACLE_BRM", "ERICSSON_CS", "HUAWEI_CBS"):
        assert code in adapters.ADAPTERS


def test_vendors_without_an_adapter_are_declared_not_hidden():
    """The requirement names more vendors than we have mapped. Listing the gap
    is honest; omitting them would look like they are unsupported."""
    planned = {code for code, _ in adapters.PLANNED_VENDORS}
    assert {"NOKIA_CS", "AMDOCS", "NETCRACKER"} <= planned
    assert not (planned & set(adapters.ADAPTERS))


# --- Oracle BRM -------------------------------------------------------------


def test_brm_rate_plan_becomes_a_base_tariff():
    mapped, spec = map_records("ORACLE_BRM", ORACLE_BRM)
    assert spec.vendor == "Oracle"
    assert len(mapped) == 2

    rule = mapped[0].canonical
    assert rule is not None
    # BRM service paths are hierarchical — only the leaf is the service.
    assert rule["service_type"] == "VOICE"
    assert rule["rule_type"] == "BASE_TARIFF"

    conditions = {c["attribute"]: c["values"] for c in rule["conditions"]}
    assert conditions["destination_zone"] == ["LOCAL_ONNET"]
    assert conditions["time_band"] == ["PEAK"]
    assert conditions["product"] == ["PREPAID_A"]

    actions = {a["action_type"]: a["params"] for a in rule["actions"]}
    assert actions["SET_RATE"]["rate"] == 0.10
    assert actions["SET_RATE"]["per_units"] == 60
    # `beat` is BRM's pulse.
    assert actions["SET_PULSE"]["initial_seconds"] == 60


def test_brm_rum_selects_the_charging_unit():
    for rum, unit in (("duration", "SECOND"), ("occurrence", "EVENT"), ("volume", "BYTE")):
        mapped, _ = map_records(
            "ORACLE_BRM",
            {"rate_plans": [{"rate_plan_name": "P", "rum": rum, "amount": 1, "quantity": 1}]},
        )
        assert mapped[0].canonical["actions"][0]["params"]["unit"] == unit, rum


def test_brm_without_a_beat_emits_no_pulse_rule():
    mapped, _ = map_records("ORACLE_BRM", ORACLE_BRM)
    types = {a["action_type"] for a in mapped[1].canonical["actions"]}
    assert types == {"SET_RATE"}


# --- Ericsson ---------------------------------------------------------------


def test_ericsson_rate_steps_are_flattened_into_one_rule_each():
    """A tariff class holds several rate steps; each is a separate canonical
    rule, because each matches a different destination and time band."""
    mapped, spec = map_records("ERICSSON_CS", ERICSSON_CS)
    assert spec.vendor == "Ericsson"
    assert len(mapped) == 2

    first = mapped[0].canonical
    conditions = {c["attribute"]: c["values"] for c in first["conditions"]}
    assert conditions["destination_zone"] == ["LOCAL_ONNET"]
    assert conditions["time_band"] == ["PEAK"]
    # The class header's offer applies to every step under it.
    assert conditions["offer"] == ["PREPAID_A_LOYALTY"]


def test_ericsson_intervals_become_a_pulse_and_minimum_charge():
    mapped, _ = map_records("ERICSSON_CS", ERICSSON_CS)
    actions = {a["action_type"]: a["params"] for a in mapped[0].canonical["actions"]}
    assert actions["SET_PULSE"] == {"initial_seconds": 60.0, "subsequent_seconds": 30.0}
    assert actions["SET_MINIMUM_CHARGE"]["amount"] == 0.05


def test_ericsson_next_interval_defaults_to_the_first():
    mapped, _ = map_records("ERICSSON_CS", ERICSSON_CS)
    pulse = next(
        a for a in mapped[1].canonical["actions"] if a["action_type"] == "SET_PULSE"
    )
    assert pulse["params"]["subsequent_seconds"] == 60.0


def test_ericsson_service_names_are_translated():
    mapped, _ = map_records(
        "ERICSSON_CS",
        {"tariffClasses": [{"tariffClassName": "T", "serviceType": "GPRS",
                            "rateSteps": [{"ratePerUnit": 1, "chargingUnit": 1}]}]},
    )
    assert mapped[0].canonical["service_type"] == "DATA"


# --- Huawei -----------------------------------------------------------------


def test_huawei_segments_map_with_numeric_service_flags():
    mapped, spec = map_records("HUAWEI_CBS", HUAWEI_CBS)
    assert spec.vendor == "Huawei"
    rule = mapped[0].canonical
    # Huawei encodes the service numerically; 1 is voice.
    assert rule["service_type"] == "VOICE"
    conditions = {c["attribute"]: c["values"] for c in rule["conditions"]}
    assert conditions["destination_zone"] == ["LOCAL_ONNET"]
    assert conditions["product"] == ["PREPAID_A"]

    actions = {a["action_type"]: a["params"] for a in rule["actions"]}
    assert actions["APPLY_TAX"]["tax_rule"] == "VAT_STANDARD"
    assert actions["SET_PULSE"]["initial_seconds"] == 60


def test_huawei_compact_dates_are_parsed():
    mapped, _ = map_records("HUAWEI_CBS", HUAWEI_CBS)
    assert mapped[0].canonical["effective_from"] == "2026-01-01"


@pytest.mark.parametrize("flag,service", [("1", "VOICE"), ("2", "SMS"), ("3", "DATA")])
def test_huawei_service_flags(flag, service):
    mapped, _ = map_records(
        "HUAWEI_CBS",
        {"pricingPlans": [{"pricingPlanName": "P", "serviceFlag": flag,
                           "ratingSegments": [{"unitPrice": 1, "unitSize": 1}]}]},
    )
    assert mapped[0].canonical["service_type"] == service


# --- Failure handling -------------------------------------------------------


def test_one_bad_record_does_not_fail_the_export():
    payload = {
        "rate_plans": [
            {"rate_plan_name": "Good", "amount": 0.1, "quantity": 60},
            {"rate_plan_name": "Bad", "amount": "free"},
            {"rate_plan_name": "AlsoGood", "amount": 0.2, "quantity": 60},
        ]
    }
    mapped, _ = map_records("ORACLE_BRM", payload)
    assert [bool(m.canonical) for m in mapped] == [True, False, True]
    assert "not a number" in mapped[1].error


def test_a_record_missing_its_identity_is_rejected_against_that_field():
    mapped, _ = map_records("ORACLE_BRM", {"rate_plans": [{"amount": 1}]})
    assert mapped[0].canonical is None
    assert mapped[0].field == "rate_plan_name"


def test_an_unknown_vendor_is_refused():
    with pytest.raises(AdapterError, match="No adapter"):
        map_records("SOME_VENDOR", {})


def test_rule_keys_are_stable_and_distinct_per_destination():
    mapped, _ = map_records("ORACLE_BRM", ORACLE_BRM)
    keys = [m.canonical["rule_key"] for m in mapped]
    assert len(set(keys)) == 2
    assert all(k.startswith("BRM_") for k in keys)
    # Re-mapping the same export must produce the same keys, or every import
    # would create new rules instead of updating existing ones.
    again, _ = map_records("ORACLE_BRM", ORACLE_BRM)
    assert [m.canonical["rule_key"] for m in again] == keys


# --- Change detection -------------------------------------------------------


def test_fingerprint_ignores_cosmetic_changes():
    """A vendor renaming a plan is not a tariff change; versioning every rule
    for it would make the audit trail useless."""
    base = map_records("ORACLE_BRM", ORACLE_BRM)[0][0].canonical
    renamed = {**base, "name": "Totally different name", "description": "new"}
    assert service.rule_fingerprint(base) == service.rule_fingerprint(renamed)


def test_fingerprint_changes_when_the_rate_changes():
    base = map_records("ORACLE_BRM", ORACLE_BRM)[0][0].canonical
    repriced = {
        **base,
        "actions": [
            {"action_type": "SET_RATE", "params": {"rate": 0.99, "unit": "SECOND"}}
        ],
    }
    assert service.rule_fingerprint(base) != service.rule_fingerprint(repriced)


def test_fingerprint_changes_when_a_condition_changes():
    base = map_records("ORACLE_BRM", ORACLE_BRM)[0][0].canonical
    retargeted = {
        **base,
        "conditions": [
            {"attribute": "destination_zone", "operator": "EQUALS", "values": ["ELSEWHERE"]}
        ],
    }
    assert service.rule_fingerprint(base) != service.rule_fingerprint(retargeted)


# --- Credentials ------------------------------------------------------------


def test_secrets_are_masked_but_their_presence_is_visible():
    redacted = service.redact(
        {"username": "svc_ra", "password": "hunter2", "token": "abc", "port": 1521}
    )
    assert redacted["username"] == "svc_ra"
    assert redacted["port"] == 1521
    assert redacted["password"] == "********"
    assert redacted["token"] == "********"


def test_an_unset_secret_is_not_masked_into_looking_set():
    assert service.redact({"password": ""})["password"] == ""
