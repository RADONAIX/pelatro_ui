"""Decoding for the wire formats an MSC actually emits.

The switch does not store numbers as text. ``served_msisdn`` arrives as
``915885519585F0`` — TBCD (telephony binary-coded decimal), the ASN.1 encoding
3GPP TS 32.005 mandates for AddressString. Read naively it is a meaningless hex
string, and every prefix match, on-net decision and subscriber lookup built on
it is wrong in a way that looks like a data problem rather than a decoding bug.

Everything here is **pure** — a string in, a string out — so the whole surface
is testable against real switch output with no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: 0xF is the TBCD filler nibble, emitted to pad an odd digit count to a whole
#: octet. It terminates the number; digits after it do not exist.
_FILLER = 0xF

#: TBCD reserves b–f for the extended alphabet (``*``, ``#``, a, b, c) used by
#: supplementary-service strings. Mapped rather than dropped so an SS-code
#: dialled string round-trips instead of silently losing characters.
_EXTENDED = {0xA: "*", 0xB: "#", 0xC: "a", 0xD: "b", 0xE: "c"}


class DecodeError(ValueError):
    """One field could not be decoded. Never fatal to a batch."""


def decode_tbcd(raw: str | None) -> str | None:
    """Decode a TBCD-encoded hex string into digits.

    Each octet carries two digits with the **low nibble first**, so ``91``
    is the digit pair ``1, 9``. Returns ``None`` for an empty field rather than
    an empty string, because "absent" and "decoded to nothing" are different
    facts downstream.
    """
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text:
        return None
    if len(text) % 2:
        # An odd hex length cannot be a whole number of octets. Truncated
        # source data, not something to guess at.
        raise DecodeError(f"'{raw}' is not a whole number of octets.")

    digits: list[str] = []
    for position in range(0, len(text), 2):
        octet = text[position : position + 2]
        try:
            value = int(octet, 16)
        except ValueError as exc:
            raise DecodeError(f"'{raw}' is not valid hexadecimal.") from exc
        for nibble in (value & 0x0F, value >> 4):
            if nibble == _FILLER:
                # Filler ends the number; anything after it is padding.
                return "".join(digits) or None
            digits.append(_EXTENDED.get(nibble, str(nibble)))
    return "".join(digits) or None


def _has_ton_npi(raw: str) -> bool:
    """Whether the first octet is a nature-of-address / numbering-plan byte.

    3GPP AddressString prefixes the digits with one octet whose extension bit
    (0x80) is always set — 0x91 international, 0xA1 national, 0x81 unknown,
    0xC1 subscriber. An IMSI carries no such octet, which is why the two
    decoders are not the same function.
    """
    try:
        return bool(int(raw[:2], 16) & 0x80)
    except (ValueError, IndexError):
        return False


#: Nature-of-address, from bits 6-4 of the TON/NPI octet.
TON_UNKNOWN = 0
TON_INTERNATIONAL = 1
TON_NATIONAL = 2
TON_SUBSCRIBER = 4


def decode_address(raw: str | None) -> tuple[str | None, int | None]:
    """Decode an AddressString into ``(digits, nature_of_address)``.

    The nature of address is returned rather than discarded because it is the
    only reliable way to know whether the digits are already international.
    """
    if raw is None or not str(raw).strip():
        return None, None
    text = str(raw).strip().upper()
    ton: int | None = None
    if _has_ton_npi(text):
        ton = (int(text[:2], 16) >> 4) & 0x07
        text = text[2:]
    return decode_tbcd(text), ton


def normalise_msisdn(
    raw: str | None,
    *,
    country_code: str,
    trunk_prefix: str = "0",
    international_prefix: str = "00",
) -> str | None:
    """Decode an MSC address field to a bare international number.

    Prefix matching (§26) selects the longest match, so the same subscriber
    reaching the table as ``0965200990`` and ``855965200990`` would be scored
    against two different destination zones. Normalising here — once, at the
    edge — is what makes the longest-prefix result meaningful.
    """
    digits, ton = decode_address(raw)
    if not digits:
        return None

    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith(international_prefix) and len(digits) > len(international_prefix):
        return digits[len(international_prefix) :]
    if ton == TON_INTERNATIONAL:
        return digits
    if digits.startswith(country_code):
        return digits
    if trunk_prefix and digits.startswith(trunk_prefix):
        # National format: the trunk prefix is a dialling convention, not part
        # of the number.
        return country_code + digits[len(trunk_prefix) :]
    if ton in (TON_NATIONAL, TON_SUBSCRIBER, TON_UNKNOWN, None):
        return country_code + digits
    return digits


def decode_imsi(raw: str | None) -> str | None:
    """Decode an IMSI. Plain TBCD — no TON/NPI octet."""
    if raw is None or not str(raw).strip():
        return None
    return decode_tbcd(str(raw).strip())


#: 3GPP TS 32.005 TimeStamp: YYMMDDhhmmss, then the sign, then the offset from
#: UTC as hours and minutes. The sign reaches us as the *hex of its ASCII byte*
#: — ``2B`` for ``+``, ``2D`` for ``-`` — because the whole field is dumped as
#: hex rather than decoded, so ``1810250101592B0700`` is 2018-10-25 01:01:59
#: +07:00. Both that and a literal ``+``/``-`` are accepted: two loaders feed
#: this table and they do not agree.
_SIGN_HEX = {"2B": "+", "2D": "-"}


def decode_timestamp(raw: str | None) -> datetime | None:
    """Decode a 3GPP BCD timestamp into an aware ``datetime``.

    The loader on this source already materialises ``*_ts`` columns for most
    time fields, so this is the fallback for the ones it did not — and the
    check that the two agree.
    """
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if len(text) < 12:
        raise DecodeError(f"'{raw}' is too short to be a timestamp.")

    try:
        year = 2000 + int(text[0:2])
        moment = datetime(
            year, int(text[2:4]), int(text[4:6]),
            int(text[6:8]), int(text[8:10]), int(text[10:12]),
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise DecodeError(f"'{raw}' is not a valid timestamp.") from exc

    tail = text[12:]
    if not tail:
        return moment

    if tail[:2].upper() in _SIGN_HEX:
        sign, offset_digits = _SIGN_HEX[tail[:2].upper()], tail[2:]
    elif tail[0] in "+-":
        sign, offset_digits = tail[0], tail[1:]
    else:
        raise DecodeError(f"'{raw}' has an unrecognised UTC offset sign.")

    if len(offset_digits) < 4:
        raise DecodeError(f"'{raw}' has a truncated UTC offset.")
    try:
        offset = timedelta(hours=int(offset_digits[:2]), minutes=int(offset_digits[2:4]))
    except ValueError as exc:
        raise DecodeError(f"'{raw}' has an invalid UTC offset.") from exc

    # The digits are local time; subtracting the offset gives the UTC instant.
    # Storing local time as if it were UTC shifts every call into the wrong time
    # band, which silently misprices peak traffic.
    return moment - offset if sign == "+" else moment + offset


def decode_duration(raw: str | None) -> int | None:
    """Call duration in whole seconds.

    Returns ``None`` for absent, and raises for a negative value — a negative
    duration is corrupt source data and must not be quietly rated as zero.
    """
    if raw is None or not str(raw).strip():
        return None
    try:
        seconds = int(float(str(raw).strip()))
    except ValueError as exc:
        raise DecodeError(f"'{raw}' is not a duration in seconds.") from exc
    if seconds < 0:
        raise DecodeError(f"Duration {seconds} is negative.")
    return seconds
