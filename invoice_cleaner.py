"""Clean and validate invoice records produced by OCR."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


# Known OCR correction supported by the sample data.
OCR_DIGIT_FIXES = {"O": "0"}
MISSING_TOKENS = {"N/A", "NA", "NONE", "NULL", "-", "--"}

AMOUNT_RE = re.compile(r"^-?\$?\s?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
NUMERIC_DATE_RE = re.compile(r"^[\dO/\-. ]+$")
DATE_SHAPE_RE = re.compile(
    r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3} \d{1,2}, \d{4})$"
)

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",   # Assumption: slash dates in this batch are month-first.
    "%b %d, %Y",
)

# Used only to identify a valid date that conflicts with our month-first policy.
EXCLUDED_DATE_FORMAT = "%d/%m/%Y"

CENTS = Decimal("0.01")

# Business assumption for this exercise. A fixed range keeps the same input
# deterministic: it produces the same result whenever the program is run.
MIN_DATE = date(2023, 1, 1)
MAX_DATE = date(2024, 12, 31)

SUSPICIOUS_CODES = {
    "NEGATIVE_AMOUNT",
    "ZERO_AMOUNT",
    "EXCESS_PRECISION",
    "AMOUNT_OUT_OF_RANGE",
    "DATE_OUT_OF_RANGE",
    "POLICY_REJECTED_DATE",
    "DUPLICATE",
    "CONFLICT",
}


def parse_amount(raw) -> tuple[Decimal | None, str | None]:
    """Parse an amount without applying business-level validation rules."""
    if raw is None:
        return None, "MISSING_AMOUNT: field is null"

    original = str(raw).strip()
    if not original:
        return None, "MISSING_AMOUNT: field is empty or whitespace only"
    if original.upper() in MISSING_TOKENS:
        return None, f"MISSING_AMOUNT: placeholder value {original!r}"

    value_text = original
    for bad, good in OCR_DIGIT_FIXES.items():
        value_text = value_text.replace(bad, good)

    # Validate the structure before removing formatting characters. This avoids
    # silently repairing malformed values such as badly grouped commas/spaces.
    if not AMOUNT_RE.fullmatch(value_text):
        return None, f"UNPARSEABLE_AMOUNT: {original!r} is not a well-formed amount"

    value_text = value_text.replace("$", "").replace(",", "").strip()

    try:
        return Decimal(value_text), None
    except InvalidOperation:
        return None, f"UNPARSEABLE_AMOUNT: {original!r} is not a number"


def parse_date(raw) -> tuple[date | None, str | None]:
    """Parse a supported date format without applying the expected date range."""
    if raw is None:
        return None, "MISSING_DATE: field is null"

    original = str(raw).strip()
    if not original:
        return None, "MISSING_DATE: field is empty or whitespace only"
    if original.upper() in MISSING_TOKENS:
        return None, f"MISSING_DATE: placeholder value {original!r}"

    value_text = original
    if NUMERIC_DATE_RE.fullmatch(value_text):
        for bad, good in OCR_DIGIT_FIXES.items():
            value_text = value_text.replace(bad, good)

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value_text, fmt).date(), None
        except ValueError:
            continue

    # A valid DD/MM/YYYY date is readable, but conflicts with the month-first
    # assumption used for slash dates in this batch.
    try:
        alternative = datetime.strptime(value_text, EXCLUDED_DATE_FORMAT).date()
        return None, (
            f"POLICY_REJECTED_DATE: {original!r} is valid as DD/MM/YYYY "
            f"({alternative.isoformat()}); slash dates are assumed month-first"
        )
    except ValueError:
        pass

    if DATE_SHAPE_RE.fullmatch(value_text):
        return None, f"IMPOSSIBLE_DATE: {original!r} contains invalid date values"

    return None, f"UNPARSEABLE_DATE: {original!r} does not match a supported date format"


def severity_of(reasons: list[str]) -> str:
    """Return the highest severity represented by the collected reasons."""
    for reason in reasons:
        code = reason.split(":", 1)[0]
        if code not in SUSPICIOUS_CODES:
            return "invalid"
    return "suspicious"


def validate_record(raw: dict) -> tuple[dict | None, list[str]]:
    """Normalize one record and collect all reasons that require review."""
    reasons: list[str] = []

    # Presence is required, but the assignment does not establish one mandatory
    # invoice-id format, so readable IDs are not rejected by pattern.
    invoice_id = str(raw.get("invoice_id") or "").strip()
    if not invoice_id:
        reasons.append("MISSING_INVOICE_ID: field is empty or absent")

    amount, amount_error = parse_amount(raw.get("amount"))
    if amount_error:
        reasons.append(amount_error)
    elif amount is not None:
        # Parsing and business validation are deliberately separate: -450 is a
        # valid number, but an unusual invoice amount that deserves review.
        if amount < 0:
            reasons.append(f"NEGATIVE_AMOUNT: {amount} may represent a credit note")
        elif amount == 0:
            reasons.append("ZERO_AMOUNT: amount is zero")

        if amount.as_tuple().exponent < -2:
            reasons.append(
                f"EXCESS_PRECISION: {amount} has more than 2 decimal places"
            )

    parsed_date, date_error = parse_date(raw.get("date"))
    if date_error:
        reasons.append(date_error)
    elif parsed_date is not None:
        if parsed_date < MIN_DATE:
            reasons.append(
                f"DATE_OUT_OF_RANGE: {parsed_date.isoformat()} predates "
                f"{MIN_DATE.isoformat()}"
            )
        elif parsed_date > MAX_DATE:
            reasons.append(
                f"DATE_OUT_OF_RANGE: {parsed_date.isoformat()} postdates "
                f"{MAX_DATE.isoformat()}"
            )

    vendor = str(raw.get("vendor") or "").strip()
    if not vendor:
        reasons.append("MISSING_VENDOR: field is empty or absent")

    normalized_amount = None
    if amount is not None and not any(
        reason.startswith("EXCESS_PRECISION") for reason in reasons
    ):
        try:
            normalized_amount = amount.quantize(CENTS)
        except InvalidOperation:
            reasons.append(
                f"AMOUNT_OUT_OF_RANGE: {amount} is too large to normalize safely"
            )

    if reasons:
        return None, reasons

    return {
        "invoice_id": invoice_id,
        "amount": normalized_amount,
        "date": parsed_date.isoformat(),
        "vendor": vendor,
    }, []


def _flag(raw: dict, reasons: list[str]) -> dict:
    """Return the original OCR record with review metadata attached."""
    entry = dict(raw)
    entry["reason"] = list(reasons)
    entry["severity"] = severity_of(reasons)
    return entry


def process_records(raw_records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (clean_records, flagged_records) for the supplied OCR records."""
    flagged_records: list[dict] = []
    survivors: list[tuple[dict, dict]] = []

    # First validate each record independently. Only otherwise-valid records
    # participate in duplicate detection.
    for raw in raw_records:
        normalized, reasons = validate_record(raw)
        if reasons:
            flagged_records.append(_flag(raw, reasons))
        else:
            survivors.append((raw, normalized))

    # Group by invoice id so duplicate/conflict decisions consider every valid
    # occurrence, rather than depending on the order records happen to arrive.
    groups: dict[str, list[tuple[dict, dict]]] = {}
    for raw, normalized in survivors:
        groups.setdefault(normalized["invoice_id"], []).append((raw, normalized))

    clean_records: list[dict] = []

    for invoice_id, group in groups.items():
        if len(group) == 1:
            clean_records.append(group[0][1])
            continue

        normalized_versions = [normalized for _, normalized in group]
        first_normalized = normalized_versions[0]

        if all(record == first_normalized for record in normalized_versions[1:]):
            # Exact repeats are redundant: keep the first and flag later copies.
            clean_records.append(first_normalized)
            for raw, _ in group[1:]:
                flagged_records.append(
                    _flag(raw, [f"DUPLICATE: identical to another {invoice_id}"])
                )
            continue

        # Same id but different normalized content is a real conflict. Row order
        # is not evidence of which version is correct, so none is accepted.
        reason = f"CONFLICT: records share id {invoice_id} but contain different values"
        for raw, _ in group:
            flagged_records.append(_flag(raw, [reason]))

    assert len(clean_records) + len(flagged_records) == len(raw_records)
    return clean_records, flagged_records
