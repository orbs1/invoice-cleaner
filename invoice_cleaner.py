"""Step 1: amount normalization."""

import re
from decimal import Decimal, InvalidOperation

# Only O->0. The assignment points at this one explicitly, and it's the only
# substitution the sample data provides evidence for. Every extra character
# in this table is a chance to "fix" something that was never broken.
OCR_DIGIT_FIXES = {"O": "0"}

# Values that mean "the extractor found nothing here".
MISSING_TOKENS = {"N/A", "NA", "NONE", "NULL", "-", "--"}

# Structure of an acceptable amount, checked BEFORE any cleaning.
# Optional sign, optional currency symbol, then either properly grouped
# thousands (1,200) or plain digits (3200), then optional decimals.
# Deliberately strict: internal whitespace, stray commas and malformed
# grouping all fail here rather than being cleaned into a number.
AMOUNT_RE = re.compile(r"^-?\$?\s?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")

# Characters removed only after the structure above has been verified.
NOISE_CHARS = "$, \u00a0\t"

CENTS = Decimal("0.01")

# Business rule, documented as an assumption. Also keeps quantize() inside
# decimal's 28-digit context so it cannot raise on absurd input.
MAX_AMOUNT = Decimal("1000000.00")


def parse_amount(raw) -> tuple[Decimal | None, str | None]:
    """Normalize a raw amount field.

    Returns (value, None) on success, (None, "CODE: message") on failure.
    Exactly one element is ever non-None, and this function never raises.
    """
    if raw is None:
        return None, "MISSING_AMOUNT: field is null"

    original = str(raw).strip()

    if not original:
        return None, "MISSING_AMOUNT: field is empty or whitespace only"

    # Check placeholders BEFORE the OCR fix, so we never turn "NO DATA"
    # into "N0 DATA" and then fail to recognise it.
    if original.upper() in MISSING_TOKENS:
        return None, f"MISSING_AMOUNT: placeholder value {original!r}"

    s = original
    for bad, good in OCR_DIGIT_FIXES.items():
        s = s.replace(bad, good)

    # Validate the SHAPE first. Cleaning first and validating after would let
    # "$ 1,2O0 . 0 0" be reassembled into a plausible-looking 1200.00 -- and a
    # space inside a number is evidence the extractor broke something, which
    # is exactly the record a human needs to see.
    if not AMOUNT_RE.match(s):
        return None, f"UNPARSEABLE_AMOUNT: {original!r} is not a well-formed amount"

    for ch in NOISE_CHARS:
        s = s.replace(ch, "")

    try:
        # Constructed from the STRING, never Decimal(float(s)) -- that would
        # inherit exactly the binary error we chose Decimal to avoid.
        value = Decimal(s)
    except InvalidOperation:
        return None, f"UNPARSEABLE_AMOUNT: {original!r} is not a number"

    if value < 0:
        return None, f"NEGATIVE_AMOUNT: {value}"
    if value == 0:
        return None, "ZERO_AMOUNT: amount is zero"
    if value > MAX_AMOUNT:
        return None, f"AMOUNT_OUT_OF_RANGE: {value} exceeds {MAX_AMOUNT}"

    # More than two decimals means quantize would ROUND, not pad -- it would
    # change the value. Padding 950.5 -> 950.50 is lossless and fine; turning
    # 950.555 into 950.56 is a silent edit to a financial figure.
    if value.as_tuple().exponent < -2:
        return None, f"EXCESS_PRECISION: {value} has more than 2 decimal places"

    return value.quantize(CENTS), None


if __name__ == "__main__":
    cases = [
        # the sample
        "$1,200.00", "95O.5", "N/A", "2,340", "-450.00", " ", "3200.00",
        # previously reported bugs
        "1" * 30,          # used to raise InvalidOperation
        "950.555",         # used to silently become 950.56
        "1200.005",        # used to silently become 1200.00 (banker's rounding)
        "$ 1,2O0 . 0 0",   # used to silently become 1200.00
        "1,2,,,340",       # used to silently become 12340.00
        # other probes
        "1.200,00", "1,2,3,4", "12X4", "0.00", None, "$O.50", "$ 1,200.00",
    ]
    for c in cases:
        try:
            value, err = parse_amount(c)
            print(f"{c!r:<20} -> value={value!r:<20} error={err}")
        except Exception as exc:
            print(f"{c!r:<20} -> RAISED {type(exc).__name__}")

            