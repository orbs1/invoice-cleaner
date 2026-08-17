"""Tests for invoice_cleaner.py.

Run with:
    python -m unittest test_invoice_cleaner.py -v
"""

import unittest
from datetime import date
from decimal import Decimal

from invoice_cleaner import parse_amount, parse_date, process_records


SAMPLE_RECORDS = [
    {"invoice_id": "INV-1001", "amount": "$1,200.00", "date": "2024-01-05", "vendor": "Acme Corp"},
    {"invoice_id": "INV-1002", "amount": "95O.5", "date": "01/06/2024", "vendor": "Beta LLC"},
    {"invoice_id": "INV-1003", "amount": "N/A", "date": "2024-01-07", "vendor": "Acme Corp"},
    {"invoice_id": "INV-1004", "amount": "2,340", "date": "Jan 8, 2024", "vendor": ""},
    {"invoice_id": "INV-1001", "amount": "$1,200.00", "date": "2024-01-05", "vendor": "Acme Corp"},
    {"invoice_id": "INV-1005", "amount": "-450.00", "date": "2024-13-40", "vendor": "Gamma Inc"},
    {"invoice_id": "INV-1006", "amount": " ", "date": "2024/01/09", "vendor": "Delta Co"},
    {"invoice_id": "INV-1007", "amount": "3200.00", "date": "2019-01-10", "vendor": "Acme Corp"},
]


def has_code(record: dict, code: str) -> bool:
    """True if the flagged record carries a reason with this code.

    'reason' is a list, and every entry starts with a fixed code followed by
    a human-readable message. Matching on the prefix is the machine interface
    that structure was designed for: it keeps working when message wording
    changes.
    """
    return any(reason.startswith(code) for reason in record["reason"])


class TestAmountParsing(unittest.TestCase):
    def test_currency_and_grouping_are_normalized(self):
        amount, error = parse_amount("$1,200.00")
        self.assertIsNone(error)
        self.assertEqual(amount, Decimal("1200.00"))

    def test_known_ocr_o_is_corrected(self):
        amount, error = parse_amount("95O.5")
        self.assertIsNone(error)
        self.assertEqual(amount, Decimal("950.5"))

    def test_negative_number_parses_before_business_validation(self):
        # parse_amount is a parser: -450 is a perfectly good number. Deciding
        # that a negative invoice needs review happens in validate_record.
        amount, error = parse_amount("-450.00")
        self.assertIsNone(error)
        self.assertEqual(amount, Decimal("-450.00"))

    def test_malformed_grouping_is_not_silently_repaired(self):
        amount, error = parse_amount("12,34.00")
        self.assertIsNone(amount)
        self.assertTrue(error.startswith("UNPARSEABLE_AMOUNT"))

    def test_structure_is_checked_before_cleaning(self):
        # Stripping noise first and validating after would reassemble each of
        # these into a plausible-looking number. Internal spaces and stray
        # commas are evidence the extractor broke something.
        for raw in ("$ 1,2O0 . 0 0", "1,2,,,340", "1.200,00"):
            with self.subTest(raw=raw):
                amount, error = parse_amount(raw)
                self.assertIsNone(amount)
                self.assertTrue(error.startswith("UNPARSEABLE_AMOUNT"))

    def test_placeholder_and_blank_are_missing_not_unparseable(self):
        for raw in ("N/A", " ", None):
            with self.subTest(raw=raw):
                amount, error = parse_amount(raw)
                self.assertIsNone(amount)
                self.assertTrue(error.startswith("MISSING_AMOUNT"))


class TestDateParsing(unittest.TestCase):
    def test_supported_date_formats(self):
        cases = {
            "2024-01-05": date(2024, 1, 5),
            "2024/01/09": date(2024, 1, 9),
            "01/06/2024": date(2024, 1, 6),
            "Jan 8, 2024": date(2024, 1, 8),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                parsed, error = parse_date(raw)
                self.assertIsNone(error)
                self.assertEqual(parsed, expected)

    def test_day_first_date_is_reported_as_policy_rejection(self):
        # "13/06/2024" is a real date. It is rejected only because this batch
        # is assumed month-first, and the reason says so explicitly -- a row
        # like this is evidence the assumption may be wrong.
        parsed, error = parse_date("13/06/2024")
        self.assertIsNone(parsed)
        self.assertTrue(error.startswith("POLICY_REJECTED_DATE"))
        self.assertIn("2024-06-13", error)

    def test_impossible_date_is_rejected(self):
        parsed, error = parse_date("2024-13-40")
        self.assertIsNone(parsed)
        self.assertTrue(error.startswith("IMPOSSIBLE_DATE"))

    def test_garbage_is_distinguished_from_impossible(self):
        parsed, error = parse_date("not a date")
        self.assertIsNone(parsed)
        self.assertTrue(error.startswith("UNPARSEABLE_DATE"))

    def test_ocr_fix_applies_to_digits_but_not_month_names(self):
        # O->0 must not run on "Oct", which would become "0ct".
        parsed, error = parse_date("2O24-01-05")
        self.assertIsNone(error)
        self.assertEqual(parsed, date(2024, 1, 5))

        parsed, error = parse_date("Oct 5, 2024")
        self.assertIsNone(error)
        self.assertEqual(parsed, date(2024, 10, 5))


class TestProcessRecords(unittest.TestCase):
    def test_assignment_sample(self):
        clean, flagged = process_records(SAMPLE_RECORDS)

        self.assertEqual(len(clean), 2)
        self.assertEqual(len(flagged), 6)

        clean_by_id = {record["invoice_id"]: record for record in clean}
        self.assertEqual(set(clean_by_id), {"INV-1001", "INV-1002"})
        self.assertEqual(clean_by_id["INV-1001"]["amount"], Decimal("1200.00"))
        self.assertEqual(clean_by_id["INV-1002"]["amount"], Decimal("950.50"))
        self.assertEqual(clean_by_id["INV-1002"]["date"], "2024-01-06")

        flagged_by_id = {}
        for record in flagged:
            flagged_by_id.setdefault(record["invoice_id"], []).append(record)

        def flagged_for(invoice_id, code):
            return any(has_code(r, code) for r in flagged_by_id[invoice_id])

        self.assertTrue(flagged_for("INV-1001", "DUPLICATE"))
        self.assertTrue(flagged_for("INV-1003", "MISSING_AMOUNT"))
        self.assertTrue(flagged_for("INV-1004", "MISSING_VENDOR"))
        self.assertTrue(flagged_for("INV-1005", "NEGATIVE_AMOUNT"))
        self.assertTrue(flagged_for("INV-1005", "IMPOSSIBLE_DATE"))
        self.assertTrue(flagged_for("INV-1006", "MISSING_AMOUNT"))
        self.assertTrue(flagged_for("INV-1007", "DATE_OUT_OF_RANGE"))

    def test_every_record_lands_in_exactly_one_list(self):
        # The invariant the whole design rests on: nothing is silently
        # dropped, and nothing can be paid twice by appearing in both lists.
        clean, flagged = process_records(SAMPLE_RECORDS)
        self.assertEqual(len(clean) + len(flagged), len(SAMPLE_RECORDS))

    def test_all_reasons_are_collected_not_just_the_first(self):
        # INV-1005 fails two independent checks. Short-circuiting on the first
        # would send a reviewer round the loop twice.
        _, flagged = process_records(SAMPLE_RECORDS)
        record = next(r for r in flagged if r["invoice_id"] == "INV-1005")
        self.assertEqual(len(record["reason"]), 2)

    def test_exact_duplicate_keeps_one_and_flags_later_copy(self):
        record = {
            "invoice_id": "A-1",
            "amount": "100",
            "date": "2024-01-01",
            "vendor": "Acme",
        }
        clean, flagged = process_records([record, dict(record)])

        self.assertEqual(len(clean), 1)
        self.assertEqual(len(flagged), 1)
        self.assertTrue(has_code(flagged[0], "DUPLICATE"))

    def test_duplicates_are_matched_on_normalized_values(self):
        # Same invoice, different OCR readings. Comparing raw strings would
        # call these a conflict; comparing normalized values gets it right.
        records = [
            {"invoice_id": "A-1", "amount": "$1,200.00", "date": "2024-01-05", "vendor": "Acme"},
            {"invoice_id": "A-1", "amount": "1200", "date": "01/05/2024", "vendor": "Acme"},
        ]
        clean, flagged = process_records(records)

        self.assertEqual(len(clean), 1)
        self.assertTrue(has_code(flagged[0], "DUPLICATE"))

    def test_conflicting_same_id_flags_every_version(self):
        # Row order is not evidence of which version is correct, so neither
        # copy is accepted -- including the one that arrived first.
        records = [
            {"invoice_id": "A-1", "amount": "100", "date": "2024-01-01", "vendor": "Acme"},
            {"invoice_id": "A-1", "amount": "101", "date": "2024-01-01", "vendor": "Acme"},
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertEqual(len(flagged), 2)
        self.assertTrue(all(has_code(r, "CONFLICT") for r in flagged))

    def test_duplicate_then_conflict_marks_entire_group_as_conflict(self):
        records = [
            {"invoice_id": "A-1", "amount": "100", "date": "2024-01-01", "vendor": "Acme"},
            {"invoice_id": "A-1", "amount": "100.00", "date": "2024-01-01", "vendor": "Acme"},
            {"invoice_id": "A-1", "amount": "101", "date": "2024-01-01", "vendor": "Acme"},
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertEqual(len(flagged), 3)
        self.assertTrue(all(has_code(r, "CONFLICT") for r in flagged))

    def test_invoice_id_format_is_not_over_constrained(self):
        # Every sample id is INV-####, but enforcing that pattern on the
        # strength of eight rows would reject suppliers who number differently.
        records = [
            {"invoice_id": "ABC-42", "amount": "100", "date": "2024-01-01", "vendor": "Acme"}
        ]
        clean, flagged = process_records(records)

        self.assertEqual(len(clean), 1)
        self.assertEqual(flagged, [])

    def test_date_after_window_is_suspicious(self):
        # Fixed date, not date.today(): the window is a hard-coded constant so
        # the same input gives the same output whenever this runs.
        records = [
            {"invoice_id": "A-1", "amount": "100", "date": "2025-06-01", "vendor": "Acme"}
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertTrue(has_code(flagged[0], "DATE_OUT_OF_RANGE"))
        self.assertEqual(flagged[0]["severity"], "suspicious")

    def test_date_before_window_is_suspicious(self):
        records = [
            {"invoice_id": "A-1", "amount": "100", "date": "2020-01-01", "vendor": "Acme"}
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertTrue(has_code(flagged[0], "DATE_OUT_OF_RANGE"))

    def test_output_is_deterministic(self):
        # Guards against a time-dependent rule creeping back in: two runs on
        # the same input must be identical.
        self.assertEqual(
            process_records(SAMPLE_RECORDS),
            process_records(SAMPLE_RECORDS),
        )

    def test_excess_decimal_precision_is_suspicious_not_rounded(self):
        # Padding 950.5 -> 950.50 is lossless. Rounding 10.555 -> 10.56 is a
        # silent edit to a financial figure, so the record is flagged instead.
        records = [
            {"invoice_id": "A-1", "amount": "10.555", "date": "2024-01-01", "vendor": "Acme"}
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertTrue(has_code(flagged[0], "EXCESS_PRECISION"))
        self.assertEqual(flagged[0]["severity"], "suspicious")

    def test_absurdly_large_amount_is_reported_not_raised(self):
        # quantize() overflows decimal's context on numbers this size. The
        # contract is (value, error), so this must come back as a reason.
        records = [
            {"invoice_id": "A-1", "amount": "1" * 30, "date": "2024-01-01", "vendor": "Acme"}
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertTrue(has_code(flagged[0], "AMOUNT_OUT_OF_RANGE"))

    def test_missing_required_field_is_invalid(self):
        records = [
            {"invoice_id": "A-1", "amount": "100", "date": "2024-01-01", "vendor": ""}
        ]
        clean, flagged = process_records(records)

        self.assertEqual(clean, [])
        self.assertTrue(has_code(flagged[0], "MISSING_VENDOR"))
        self.assertEqual(flagged[0]["severity"], "invalid")

    def test_severity_separates_unreadable_from_merely_odd(self):
        # invalid    -> a required field could not be read.
        # suspicious -> everything read cleanly; a rule I chose rejected it.
        _, flagged = process_records(SAMPLE_RECORDS)
        severity = {r["invoice_id"]: r["severity"] for r in flagged}

        self.assertEqual(severity["INV-1003"], "invalid")
        self.assertEqual(severity["INV-1007"], "suspicious")

    def test_flagged_records_keep_the_raw_values(self):
        # The consumer of flagged_records is a human holding the scan. They
        # need what the extractor produced, not what my parser made of it.
        _, flagged = process_records(SAMPLE_RECORDS)
        record = next(r for r in flagged if r["invoice_id"] == "INV-1005")
        self.assertEqual(record["amount"], "-450.00")
        self.assertEqual(record["date"], "2024-13-40")

    def test_missing_keys_do_not_raise(self):
        clean, flagged = process_records([{}])

        self.assertEqual(clean, [])
        for code in ("MISSING_INVOICE_ID", "MISSING_AMOUNT", "MISSING_DATE", "MISSING_VENDOR"):
            with self.subTest(code=code):
                self.assertTrue(has_code(flagged[0], code))

    def test_empty_input(self):
        self.assertEqual(process_records([]), ([], []))

    def test_input_records_are_not_mutated(self):
        records = [
            {"invoice_id": "A-1", "amount": "$100.00", "date": "2024-01-01", "vendor": " Acme "}
        ]
        original = [dict(records[0])]

        process_records(records)

        self.assertEqual(records, original)


if __name__ == "__main__":
    unittest.main()
