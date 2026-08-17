"""Invoice record cleaning and validation.

Work in progress: field-level parsing only. Record-level validation and the
deduplication pass come next.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------
# Shared configuration
# --------------------------------------------------------------------------

# Only O->0. The assignment points at this one explicitly, and it's the only
# substitution the sample data provides evidence for. Every extra character
# in this table is a chance to "fix" something that was never broken.
# Shared by both parsers -- it's the same OCR failure in both fields.
OCR_DIGIT_FIXES = {"O": "0"}


# --------------------------------------------------------------------------
# Amount configuration
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Date configuration
# --------------------------------------------------------------------------

# The OCR fix applies to dates too, but only where the field is numeric.
# Applying O->0 blindly turns "Oct 5, 2024" into "0ct 5, 2024" and breaks
# every October and no other month. This guard says: only touch the string if
# it contains nothing but digits and separators, i.e. there is no month name
# to damage.
NUMERIC_DATE_RE = re.compile(r"^[\dO/\-. ]+$")

# Explicit format list. No dateutil: it silently picks an interpretation for
# ambiguous input, and that is a business decision I want visible in code.
#
# Assumption: bare D/M/YYYY is US-ordered (month first). Evidence: invoice ids
# appear to be assigned chronologically -- 1001->Jan 5, 1003->Jan 7,
# 1004->Jan 8, 1006->Jan 9 -- so INV-1002 at "01/06/2024" = Jan 6 completes
# the run, while 1 June would sit five months outside the whole batch.
# %d/%m/%Y is therefore deliberately absent.
#
# Two slash formats here is not a contradiction. What's forbidden is not
# multiple formats but multiple readings of one string: %m/%d/%Y and %d/%m/%Y
# match the SAME strings and disagree, while %Y/%m/%d cannot collide with
# either (four-digit leading year). Verified exhaustively over every date in
# 2023-2024 formatted every way: 2,924 strings, zero collisions.
DATE_FORMATS = [
    "%Y-%m-%d",     # 2024-01-05
    "%Y/%m/%d",     # 2024/01/09
    "%m/%d/%Y",     # 01/06/2024
    "%b %d, %Y",    # Jan 8, 2024
]

# Business rules, enforced INSIDE parse_date -- same convention as
# parse_amount, which enforces sign, zero and MAX_AMOUNT internally.
# Hard-coded, not datetime.now(): the same input must give the same output
# next year. Not derived from the batch either: a batch-derived window is set
# by the least trustworthy record in it.
MIN_DATE = date(2023, 1, 1)     # start of the prior fiscal year
MAX_DATE = date(2024, 12, 31)   # end of the batch's fiscal year


# --------------------------------------------------------------------------
# Field-level parsing
# --------------------------------------------------------------------------

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


def parse_date(raw) -> tuple[str | None, str | None]:
    """Normalize a raw date field to an ISO-8601 string.

    Returns (iso_string, None) on success, (None, "CODE: message") on failure.
    Exactly one element is ever non-None, and this function never raises.
    """
    if raw is None:
        return None, "MISSING_DATE: field is null"

    original = str(raw).strip()

    if not original:
        return None, "MISSING_DATE: field is empty or whitespace only"

    s = original
    if NUMERIC_DATE_RE.match(s):
        for bad, good in OCR_DIGIT_FIXES.items():
            s = s.replace(bad, good)

    # Try every format instead of stopping at the first match. First-match-wins
    # would let two rows in one batch be read under two different conventions,
    # silently. Here a disagreement becomes an explicit AMBIGUOUS_DATE, so a
    # future overlapping format fails loudly instead of guessing.
    parsed_by_format = {}
    for fmt in DATE_FORMATS:
        try:
            parsed_by_format[fmt] = datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    if not parsed_by_format:
        return None, f"INVALID_DATE: {original!r} does not match any known format"

    distinct = set(parsed_by_format.values())
    if len(distinct) > 1:
        readings = ", ".join(sorted(d.isoformat() for d in distinct))
        return None, f"AMBIGUOUS_DATE: {original!r} could be {readings}"

    parsed = distinct.pop()

    if parsed < MIN_DATE:
        return None, (
            f"DATE_OUT_OF_RANGE: {parsed.isoformat()} predates {MIN_DATE.isoformat()}"
        )
    if parsed > MAX_DATE:
        return None, (
            f"DATE_OUT_OF_RANGE: {parsed.isoformat()} postdates {MAX_DATE.isoformat()}"
        )

    return parsed.isoformat(), None


# --------------------------------------------------------------------------

if __name__ == "__main__":
    amount_cases = [
        # the sample
        "$1,200.00", "95O.5", "N/A", "2,340", "-450.00", " ", "3200.00",
        # regression: these used to crash or silently produce wrong numbers
        "1" * 30, "950.555", "1200.005", "$ 1,2O0 . 0 0", "1,2,,,340",
        # other probes
        "1.200,00", "1,2,3,4", "12X4", "0.00", None, "$O.50",
    ]

    date_cases = [
        # the sample
        "2024-01-05", "01/06/2024", "2024-01-07", "Jan 8, 2024",
        "2024-13-40", "2024/01/09", "2019-01-10",
        # OCR probes: fix the first, leave the second alone
        "2O24-01-05", "Oct 5, 2024",
        # rule probes
        "13/06/2024", "2024-02-30", "2025-06-01", "", None, "not a date",
    ]

    print("=== parse_amount ===")
    for c in amount_cases:
        value, err = parse_amount(c)
        print(f"  {c!r:<20} -> value={value!r:<20} error={err}")

    print("\n=== parse_date ===")
    for c in date_cases:
        value, err = parse_date(c)
        print(f"  {c!r:<16} -> value={value!r:<14} error={err}")

        