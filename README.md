# Invoice Cleaner — Home Assignment

Junior Software Engineer · Genpact · August 2026

Takes raw, OCR-extracted invoice records and splits them into records that are
safe to process automatically and records that need a human to look at them.

## Running

No external dependencies. Python 3.10+ (uses `X | None` type syntax).

```bash
python -m unittest test_invoice_cleaner.py -v
```

30 tests: every row of the sample data, plus a test for each design decision.

## Files

| File | |
|---|---|
| `invoice_cleaner.py` | The module. `process_records` is the entry point. |
| `test_invoice_cleaner.py` | Test suite. |
| `WRITEUP.md` | Assumptions, edge cases, and how AI tools were used. |

## Interface

```python
def process_records(raw_records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (clean_records, flagged_records)."""
```

**Clean records** are normalized: `amount` is a `Decimal`, `date` is an
ISO-8601 string.

```python
{"invoice_id": "INV-1001", "amount": Decimal("1200.00"),
 "date": "2024-01-05", "vendor": "Acme Corp"}
```

**Flagged records** keep the original raw values, so a reviewer comparing
against the scan sees what the extractor produced. Two fields are added:

```python
{"invoice_id": "INV-1005", "amount": "-450.00", "date": "2024-13-40",
 "vendor": "Gamma Inc",
 "reason": ["NEGATIVE_AMOUNT: -450.00 may represent a credit note",
            "IMPOSSIBLE_DATE: '2024-13-40' contains invalid date values"],
 "severity": "invalid"}
```

`reason` is a list, because a record can fail several checks at once. Each
entry starts with a fixed code, so filtering is a prefix match rather than a
brittle substring search:

```python
date_failures = [r for r in flagged
                 if any(x.startswith("IMPOSSIBLE_DATE") for x in r["reason"])]
```

`severity` is `invalid` when a required field could not be read (fixing it
means going back to the source document) and `suspicious` when everything read
cleanly and a validation rule rejected it.

## Guarantee

Every input record lands in exactly one output list:

```
len(clean_records) + len(flagged_records) == len(raw_records)
```

Asserted in the code and tested. Nothing is silently dropped, and no invoice
can be paid twice by appearing in both lists.

## Result on the sample data

2 clean, 6 flagged.

| Record | Outcome |
|---|---|
| INV-1001 | clean — `$1,200.00` → `1200.00` |
| INV-1002 | clean — OCR `O`→`0` gives `950.50`; `01/06/2024` read as 6 January |
| INV-1003 | flagged · invalid — amount is the placeholder `N/A` |
| INV-1004 | flagged · invalid — vendor is empty |
| INV-1001 | flagged · suspicious — duplicate of the first occurrence |
| INV-1005 | flagged · invalid — negative amount *and* an impossible date |
| INV-1006 | flagged · invalid — amount is whitespace |
| INV-1007 | flagged · suspicious — 2019 falls outside the expected window |

Reasoning for each of these is in `WRITEUP.md`.
