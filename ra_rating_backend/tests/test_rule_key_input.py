"""A blank rule key means "I did not fill this in", not "reject my request".

The wizard sends ``rule_key: ""`` for a field the author never touched, which is
what every form library does with an optional text input. Declaring the field
``str | None`` accepts ``None`` and rejects ``""``, so the author got a 422
quoting a regex they had never seen against a field they had not used.

The API is the side that knows better, and it is the side that has to be
forgiving here: there is exactly one sensible reading of an empty optional
string, and the client cannot be expected to know that ``null`` and ``""`` differ
to us when they do not differ to the person typing.
"""

from __future__ import annotations

import pytest

from app.modules.rules.api.schemas import RuleWrite
from app.modules.rules.ingest import keys
from app.modules.rules.schemas import CloneRequest, RuleCreate

LEGACY = {
    "name": "Peak on-net",
    "rule_type": "BASE_TARIFF",
    "service_type": "VOICE",
    "effective_from": "2026-01-01",
}
CANONICAL = {
    "rule_name": "Peak on-net",
    "charging_mode": "PREPAID",
    "rule_type_code": "BASE_TARIFF",
    "service_type": "VOICE",
    "validity": {"effective_from": "2026-01-01"},
}


# --- The helper -------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t", None])
def test_a_blank_key_is_absent(blank):
    assert keys.clean_optional(blank) is None


def test_a_key_is_trimmed_rather_than_rejected_for_stray_whitespace():
    """Copy-pasting a key out of a spreadsheet brings whitespace with it. That is
    not a mistake worth a 422."""
    assert keys.clean_optional("  PEAK_ON_NET  ") == "PEAK_ON_NET"


def test_a_short_key_says_so_in_words():
    with pytest.raises(ValueError, match="at least three characters"):
        keys.validate_format("AB")


def test_a_malformed_key_suggests_a_valid_one():
    """Pydantic's `pattern=` reports the regex, which tells an author their key is
    wrong without telling them what a right one looks like."""
    with pytest.raises(ValueError, match="PEAK_ONNET"):
        keys.validate_format("peak onnet")


def test_an_over_long_key_reports_its_length():
    with pytest.raises(ValueError, match="97"):
        keys.validate_format("A" * 97)


@pytest.mark.parametrize(
    "key", ["ABC", "PEAK_ON_NET", "ERICSSON:TC-4471", "PLAN.A-1", "V2_2026"]
)
def test_the_shapes_a_real_key_takes_are_accepted(key):
    assert keys.validate_format(key) == key


# --- The legacy surface -----------------------------------------------------


def test_the_legacy_create_accepts_a_blank_key():
    """The reported failure, pinned. The service derives a key from the name when
    none is supplied, which is what the author expected to happen."""
    assert RuleCreate(**LEGACY, rule_key="").rule_key is None


def test_the_legacy_create_still_keeps_a_real_key():
    assert RuleCreate(**LEGACY, rule_key="PEAK_ON_NET").rule_key == "PEAK_ON_NET"


def test_the_legacy_create_still_refuses_a_malformed_key():
    with pytest.raises(ValueError, match="not a valid rule key"):
        RuleCreate(**LEGACY, rule_key="peak onnet")


def test_the_clone_request_accepts_a_blank_key():
    """Same field, same form, same empty string — and cloning is where an author
    is *most* likely to leave the key for us to derive."""
    assert CloneRequest(name="Copy of peak", rule_key="").rule_key is None


# --- The canonical surface --------------------------------------------------


def test_the_canonical_create_accepts_a_blank_key():
    assert RuleWrite(**CANONICAL, rule_key="").rule_key is None


def test_the_canonical_create_still_refuses_a_malformed_key():
    with pytest.raises(ValueError, match="not a valid rule key"):
        RuleWrite(**CANONICAL, rule_key="peak onnet")


def test_a_vendor_namespaced_key_survives_the_canonical_surface():
    """`SOURCE:external_ref` is what the kernel derives for an imported rule, so
    the API must accept the shape it produces — otherwise a rule can be created
    by an import and not by an edit."""
    assert RuleWrite(**CANONICAL, rule_key="ERICSSON:TC-4471").rule_key == (
        "ERICSSON:TC-4471"
    )
