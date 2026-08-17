# Write-up

## Assumptions

**Amounts.** I use `Decimal`, constructed from the cleaned string and never
from a `float` — the latter would inherit exactly the binary error `Decimal`
was chosen to avoid. Values are padded to two decimals, but anything with more
precision is flagged rather than rounded: padding `950.5` → `950.50` is
lossless, while turning `950.555` into `950.56` is a silent edit to a financial
figure.

**Dates.** Output is an ISO-8601 string; the `date` object is a processing
detail. ISO is unambiguous across locales and sorts correctly as text. Amounts
keep their rich type instead because there is no safe encoding for an exact
decimal in JSON — serializing a date costs nothing, serializing an amount costs
correctness.

**Date range: a fixed window of 2023-01-01 to 2024-12-31.** Deliberately not
`date.today()`, so the same input always produces the same output. Also not
derived from the batch (e.g. `max(dates)`), because a batch-derived window
would be set by the least trustworthy record in it.

**Slash dates are month-first.** `"01/06/2024"` parses cleanly as both 6 January
and 1 June, with no error either way, so this needed evidence rather than a
better parser. Invoice ids appear to be assigned chronologically — 1001→Jan 5,
1003→Jan 7, 1004→Jan 8, 1006→Jan 9 — so 6 January completes the run, while
1 June would sit five months outside the batch. A date that is only valid as
`DD/MM/YYYY` is therefore reported as `POLICY_REJECTED_DATE` with the
alternative reading shown, not as malformed data: such a row is evidence this
assumption may be wrong.

**OCR correction is limited to `O`→`0`.** That is the one substitution the
sample provides evidence for. Every extra entry in that table is a chance to
"fix" something that was never broken.

**Output lists are mutually exclusive**, with
`len(clean) + len(flagged) == len(raw)` asserted. "Clean" means *safe to act on
without a human*, not *valid* — the second copy of INV-1001 is a perfectly
valid record that still must not be paid twice. Flagged records keep the raw
values so a reviewer holding the scan sees what the extractor produced, and
carry a `reason` list (a conscious deviation from the singular in the spec,
since a record can fail several checks at once) plus a `severity`.

**Deduplication compares normalized values**, among records that passed
validation. Raw comparison would call `"$1,200.00"` and `"1200"` two different
invoices — removing exactly that noise is what this module is for. Identical
content is a `DUPLICATE` and the first copy wins, which is safe because no
information is lost. The same id with *different* content is a `CONFLICT` and
all copies are flagged: row order in a file is not evidence of which version is
correct.

## Edge cases

**`"01/06/2024"` — the interesting one.** Parsing catches *impossible* dates,
not *ambiguous* ones. `"2024-13-40"` fails to parse, so the parser finds it;
`"01/06/2024"` succeeds under two readings and errors under neither. No parser
could have resolved this — it needed the invoice-id sequence above.

**`"2019-01-10"` — flagged, not corrected.** Its day (10) continues the
sequence exactly; only the year is off, which suggests 2019 is an OCR misread
of 2024. I still refuse to correct it. `9`→`4` is not a shape-similarity
misread the way `O`→`0` is, and changing a year alters tax period and statute
of limitations. Choosing between two readings of the same characters is
normalization; changing characters is invention.

**Validation order — a bug I found by testing.** My first version cleaned the
string and validated afterwards, so `"$ 1,2O0 . 0 0"` was reassembled into a
plausible `1200.00` and `"1,2,,,340"` into `12340`. But a space inside a number
is evidence the extractor broke something, and that is precisely the record a
human should see. Moving the structural check *before* cleaning fixed it, and
as a side effect rejected European-format `"1.200,00"` — previously read as
`1.20`, a 1000× error — with no special-case code.

**`O`→`0` must not run on month names.** Applied blindly to a date field it
turns `"Oct 5, 2024"` into `"0ct 5, 2024"`, breaking every October and no other
month. The fix restricts the substitution to fields containing only digits and
separators.

**`quantize` overflow.** `Decimal` has a 28-digit context, so a 30-digit amount
made `quantize` raise — breaking the `(value, error)` contract the function
advertised. Now reported as `AMOUNT_OUT_OF_RANGE`.

**Date failures are not one failure.** `"2024-13-40"` (a real invoice the OCR
mangled), `"not a date"` (garbage), `"13/06/2024"` (a valid date rejected by my
policy) and `"N/A"` (the extractor found nothing) need completely different
handling from a reviewer, so they get four distinct codes rather than one.

## How I used AI

I used Claude in two roles: one conversation to draft and reason about the
code, and a second acting as a reviewer of it. Both transcripts are included.

I did not accept the first version. My working method was to take a draft, run
it against inputs *beyond* the eight sample rows, and see what broke — most of
the edge cases above came from that, because the sample itself passes almost
anything.

Things I changed or pushed back on:

- I was given an argument that `float` breaks equality comparisons during
  deduplication. That is wrong: parsing is deterministic, so
  `float("1200.00") == float("1200.00")` is always true — float equality breaks
  after *arithmetic*, not after parsing. I dropped it and kept the
  accumulation argument, which holds. Similarly,
  `Decimal("1200.00") == Decimal("1200.0")` is true, so what trailing zeros
  preserve is the display, not comparison fidelity.
- A suggested date rule anchored on `date.today()` made the output change over
  time; a test showed the same input producing different results in 2026 and
  2030. Replaced with fixed constants, plus a regression test that fails if
  time-dependence returns.
- A generated explanation claimed `"0000-01-01"` had an impossible
  day-in-month combination. The real problem is the year. A confident wrong
  explanation is worse than a vague one, so that code now asks `date()` for the
  actual error instead of assuming.
- I removed a hand-written month-name table that duplicated what `%b` already
  knows — under a non-English locale the two would have disagreed, and the
  table would have "confirmed" a valid date the parser had just rejected.

Ideas I considered and rejected: keeping both raw and normalized values in
flagged records (doubles the payload for a consumer I decided is human), and
enforcing an `INV-\d{4}` id pattern (too little evidence from eight rows).

## Known limitations

`%b` is locale-dependent, so `"Jan 8, 2024"` would fail under a non-English
`LC_TIME`. The date window is a plausible constant rather than something
derived from the data. There is no business ceiling on amounts —
`AMOUNT_OUT_OF_RANGE` only fires when `Decimal`'s context overflows. And the
invariant is enforced with `assert`, which `python -O` strips.

## Running it

No external dependencies, Python 3.10+.

```
python -m unittest test_invoice_cleaner.py -v
```

30 tests: all eight sample rows, plus one for each design decision above.
