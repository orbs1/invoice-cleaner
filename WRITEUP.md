## Assumptions

I use `Decimal` for amounts to avoid floating-point precision issues in financial calculations. Amounts are normalized to two decimal places; values with more than two decimal digits are flagged rather than silently rounded.

Dates are normalized to ISO-8601 (`YYYY-MM-DD`). I use a fixed accepted range of `2023-01-01` to `2024-12-31` so the same input always produces the same output.

Slash-separated dates are treated as US format (`MM/DD/YYYY`). This is an assumption based on the sample: interpreting `01/06/2024` as January 6 fits the sequence of nearby invoices dated January 5, 7, 8 and 9. If a date is only valid as `DD/MM/YYYY`, I flag it as a policy rejection rather than treating it as malformed.

OCR correction is intentionally conservative. I only replace uppercase `O` with `0`, because this is the specific OCR error demonstrated in the sample.

Duplicate detection is performed on normalized records. Identical records with the same `invoice_id` are treated as duplicates and only one is kept. If the same id appears with different values, all versions are flagged as a conflict because row order is not evidence of which one is correct.

## Edge cases

The main edge cases were `95O.5`, the ambiguous date `01/06/2024`, the invalid date `2024-13-40`, missing fields, negative amounts, duplicate invoices, and the old 2019 invoice. I also tested malformed number formatting and very large amounts to make sure parsing fails safely instead of raising an exception.

## AI usage

I used AI both to help draft the solution and to review it. I did not accept the first version as-is. I ran the code against the provided sample and additional edge cases, then changed several parts based on failures I found.

For example, I replaced a date rule based on `date.today()` because it made the same input produce different results over time. I also fixed a `Decimal.quantize()` overflow case and changed duplicate handling so conflicting records with the same id are all flagged instead of trusting the first occurrence.

The AI conversations are included as requested.
