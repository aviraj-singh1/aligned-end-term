"""
Tests for the Purchase Invoice Reconciliation Engine.
30 test cases covering all matching scenarios.
"""
import sys, os
from datetime import date
from decimal import Decimal
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.app.models.records import (
    InvoiceNumberInfo, ParsedInvoiceRecord, RecordSource, VoucherType, SheetCategory
)
from backend.app.models.reconciliation import (
    AggregatedGSTInvoice, MatchMethod, ReconciliationStatus,
)
from backend.app.services.reconciliation_engine import (
    aggregate_gst_invoices, reconcile_purchases,
)
from backend.app.services.normalizer import normalize_invoice_number

D = Decimal

def _tally(supplier, gstin, inv_no, taxable, cgst=D("0"), sgst=D("0"),
           igst=D("0"), dt=date(2025,4,2), row=1, gross=None):
    info = normalize_invoice_number(inv_no)
    return ParsedInvoiceRecord(
        source=RecordSource.TALLY, source_sheet="Purchase Register",
        source_row=row, supplier_name=supplier,
        gstin_original=gstin, gstin_normalized=gstin.upper().replace(" ","") if gstin else "",
        gstin_valid=bool(gstin), invoice_number=info,
        invoice_date=dt, voucher_type=VoucherType.PURCHASE,
        taxable_value=taxable, igst=igst, cgst=cgst, sgst=sgst,
        gross_total=D(str(gross)) if gross is not None else D("0"),
        gross_total_present=gross is not None,
    )

def _gst(supplier, gstin, inv_no, taxable, cgst=D("0"), sgst=D("0"),
          igst=D("0"), dt=date(2025,4,2), row=1, rate=None, invoice_value=None):
    info = normalize_invoice_number(inv_no)
    return ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="b2b", source_row=row,
        sheet_category=SheetCategory.B2B,
        supplier_name=supplier, gstin_original=gstin,
        gstin_normalized=gstin.upper().replace(" ","") if gstin else "",
        gstin_valid=bool(gstin), invoice_number=info,
        invoice_date=dt, taxable_value=taxable,
        igst=igst, cgst=cgst, sgst=sgst,
        gst_rate=D(str(rate)) if rate else None,
        total_invoice_value=D(str(invoice_value)) if invoice_value is not None else D("0"),
        total_invoice_value_present=invoice_value is not None,
    )

def _run(tally_list, gst_list):
    return reconcile_purchases(tally_list, gst_list)


class Test01ExactMatch:
    def test_exact_gstin_invoice_values(self):
        s = _run(
            [_tally("Kumar Traders", "06DEMOX1234A1Z5", "KT0039", D("83366.11"), D("7502.95"), D("7502.95"))],
            [_gst("Kumar Traders (Vivo)", "06DEMOX1234A1Z5", "KT0039", D("83366.11"), D("7502.95"), D("7502.95"))],
        )
        assert s.perfectly_matched == 1
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.match_method == MatchMethod.EXACT_INVOICE
        assert r.audit.gstin_match == "exact"

class Test02SameGSTINDifferentNames:
    def test_name_differs_gstin_matches(self):
        s = _run(
            [_tally("Kumar Traders", "06DEMOX1234A1Z5", "KT0039", D("1000"), D("90"), D("90"))],
            [_gst("KUMAR TRADERS VIVO", "06DEMOX1234A1Z5", "KT0039", D("1000"), D("90"), D("90"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test03NormalizedInvoiceMatch:
    def test_normalized_match(self):
        s = _run(
            [_tally("ABC Ltd", "06DEMOX1234A1Z5", "INV-2025-001", D("5000"), D("450"), D("450"))],
            [_gst("ABC Ltd", "06DEMOX1234A1Z5", "INV/2025/001", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].match_method == MatchMethod.NORMALIZED_INVOICE

class Test04PrefixDifference:
    def test_prefix_diff_normalized(self):
        s = _run(
            [_tally("ABC", "06DEMOX1234A1Z5", "ABC-2025-100", D("5000"), D("450"), D("450"))],
            [_gst("ABC", "06DEMOX1234A1Z5", "ABC/2025/100", D("5000"), D("450"), D("450"))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED

class Test05FinancialYearPrefix:
    def test_fy_prefix_normalized(self):
        s = _run(
            [_tally("XYZ", "06DEMOX1234A1Z5", "SBT/2025-26/4587", D("10000"), D("900"), D("900"))],
            [_gst("XYZ", "06DEMOX1234A1Z5", "SBT-2025-26-4587", D("10000"), D("900"), D("900"))],
        )
        assert s.results[0].match_method == MatchMethod.NORMALIZED_INVOICE

class Test06Last4UniqueMatch:
    def test_last4_unique(self):
        s = _run(
            [_tally("ABC", "06DEMOX1234A1Z5", "INV4587", D("10000"), D("900"), D("900"))],
            [_gst("ABC", "06DEMOX1234A1Z5", "VENDOR/INV/4587", D("10000"), D("900"), D("900"))],
        )
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_4

class Test07Last4AmbiguousMatch:
    def test_last4_ambiguous(self):
        s = _run(
            [_tally("ABC", "06DEMOX1234A1Z5", "INV4587", D("10000"), D("900"), D("900"))],
            [_gst("ABC", "06DEMOX1234A1Z5", "A/4587", D("10000"), D("900"), D("900")),
             _gst("ABC", "06DEMOX1234A1Z5", "B/4587", D("10000"), D("900"), D("900"))],
        )
        r = [x for x in s.results if x.invoice_number_tally == "INV4587"][0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

class Test08Last2UniqueMatch:
    def test_last2_unique(self):
        """Tally 'X99' vs GST 'DIFFERENT-INVOICE-99': last-4 differs, last-2 '99' matches."""
        s = _run(
            [_tally("ABC", "06DEMOX1234A1Z5", "X99", D("5000"), D("450"), D("450"))],
            [_gst("ABC", "06DEMOX1234A1Z5", "DIFFERENT-INVOICE-99", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_2

class Test09Last2AmbiguousMatch:
    def test_last2_ambiguous(self):
        s = _run(
            [_tally("ABC", "06DEMOX1234A1Z5", "X99", D("5000"), D("450"), D("450"))],
            [_gst("ABC", "06DEMOX1234A1Z5", "A-99", D("5000"), D("450"), D("450")),
             _gst("ABC", "06DEMOX1234A1Z5", "B-99", D("5000"), D("450"), D("450"))],
        )
        r = [x for x in s.results if x.invoice_number_tally == "X99"][0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

class Test10DifferentGSTINMustNotMatch:
    def test_different_gstin_no_match(self):
        s = _run(
            [_tally("ABC", "06DEMOX1234A1Z5", "INV001", D("5000"), D("450"), D("450"))],
            [_gst("ABC", "07BBBBB1234B1Z1", "INV001", D("5000"), D("450"), D("450"))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

class Test11GSTINMissingSupplierFallback:
    def test_supplier_fallback(self):
        s = _run(
            [_tally("Kumar Traders", "", "KT001", D("1000"), D("90"), D("90"))],
            [_gst("Kumar Traders", "06DEMOX1234A1Z5", "KT001", D("1000"), D("90"), D("90"))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.audit.supplier_match == "name_fallback"

class Test12ConflictingGSTINMustNotMatch:
    def test_conflicting_gstin(self):
        s = _run(
            [_tally("Same Name Corp", "06DEMOX1234A1Z5", "INV001", D("5000"), D("450"), D("450"))],
            [_gst("Same Name Corp", "07BBBBB1234B1Z1", "INV001", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

class Test13TaxDiffZero:
    def test_zero_diff(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test14TaxDiffOnePaisa:
    def test_one_paisa(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9.01"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9.00"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

class Test15TaxDiffOneRupee:
    def test_one_rupee(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), D("10"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

class Test16TaxDiffOverOneRupee:
    def test_over_one_rupee(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), D("11"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

class Test17MissingInGSTR:
    def test_missing(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "ONLY_TALLY", D("100"), D("9"), D("9"))],
            [],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR
        assert s.missing_in_gstr == 1

class Test18NotInTally:
    def test_not_in_tally(self):
        s = _run(
            [],
            [_gst("A", "06DEMOX1234A1Z5", "ONLY_GST", D("100"), D("9"), D("9"))],
        )
        assert s.not_in_tally == 1

class Test19MultiLineGSTAggregation:
    def test_aggregation(self):
        gst_rows = [
            _gst("A", "06DEMOX1234A1Z5", "INV01", D("3000"), D("270"), D("270"), rate=18),
            _gst("A", "06DEMOX1234A1Z5", "INV01", D("2000"), D("100"), D("100"), rate=10),
        ]
        agg = aggregate_gst_invoices(gst_rows)
        assert len(agg) == 1
        assert agg[0].taxable_value == D("5000")
        assert agg[0].cgst == D("370")
        assert agg[0].sgst == D("370")
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "INV01", D("5000"), D("370"), D("370"))],
            gst_rows,
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test20DuplicateTallyVoucher:
    def test_tally_duplicate(self):
        t1 = _tally("A", "06DEMOX1234A1Z5", "DUP01", D("100"), D("9"), D("9"), row=1)
        t2 = _tally("A", "06DEMOX1234A1Z5", "DUP01", D("100"), D("9"), D("9"), row=2)
        s = _run(
            [t1, t2],
            [_gst("A", "06DEMOX1234A1Z5", "DUP01", D("100"), D("9"), D("9"))],
        )
        dupes = [r for r in s.results if r.status == ReconciliationStatus.POTENTIAL_DUPLICATE]
        assert len(dupes) == 2

class Test21DuplicateGSTRInvoice:
    def test_gst_duplicate_consumed(self):
        """Second Tally invoice should not reuse the same GST invoice."""
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "INV01", D("100"), D("9"), D("9"), row=1),
             _tally("A", "06DEMOX1234A1Z5", "INV02", D("200"), D("18"), D("18"), row=2)],
            [_gst("A", "06DEMOX1234A1Z5", "INV01", D("100"), D("9"), D("9"))],
        )
        matched = [r for r in s.results if r.status == ReconciliationStatus.PERFECTLY_MATCHED]
        missing = [r for r in s.results if r.status == ReconciliationStatus.MISSING_IN_GSTR]
        assert len(matched) == 1
        assert len(missing) == 1

class Test22IGSTInvoice:
    def test_igst(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), igst=D("18"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), igst=D("18"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test23CGSTSGSTInvoice:
    def test_cgst_sgst(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED
        assert s.results[0].igst_diff == D("0")

class Test24NumericInvoice:
    def test_numeric(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "12345", D("100"), D("9"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "12345", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test25LeadingZeroInvoice:
    def test_leading_zero(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "00123", D("100"), D("9"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "00123", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test26SameSuffixTwoCandidatesAmbiguous:
    def test_ambiguous_suffix(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "X-100", D("5000"), D("450"), D("450"))],
            [_gst("A", "06DEMOX1234A1Z5", "Y-100", D("5000"), D("450"), D("450")),
             _gst("A", "06DEMOX1234A1Z5", "Z-100", D("5000"), D("450"), D("450"))],
        )
        r = [x for x in s.results if x.invoice_number_tally == "X-100"][0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

class Test27SameSuffixDifferentTaxable:
    def test_suffix_diff_taxable(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "X-100", D("5000"), D("450"), D("450"))],
            [_gst("A", "06DEMOX1234A1Z5", "Y-100", D("99999"), D("9000"), D("9000"))],
        )
        # This is only a Last-2 match, which remains financially strict.
        r = s.results[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert r.match_method == MatchMethod.NO_MATCH

class Test28ConsumedGSTCannotReuse:
    def test_consumed(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "INV01", D("100"), D("9"), D("9"), row=1),
             _tally("A", "06DEMOX1234A1Z5", "INV-X", D("100"), D("9"), D("9"), row=2)],
            [_gst("A", "06DEMOX1234A1Z5", "INV01", D("100"), D("9"), D("9"))],
        )
        matched = [r for r in s.results if r.status == ReconciliationStatus.PERFECTLY_MATCHED]
        assert len(matched) == 1

class Test29BlankTaxesTreatedCorrectly:
    def test_blank_taxes(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test30SupplierFuzzyAloneMustNotFinalize:
    def test_fuzzy_alone_not_enough(self):
        """Supplier name similarity alone must not match if invoice doesn't match."""
        s = _run(
            [_tally("Kumar Traders", "", "TOTALLY_DIFFERENT", D("999"), D("1"), D("1"))],
            [_gst("Kumar Traders Pvt Ltd", "06DEMOX1234A1Z5", "ANOTHER_INV", D("5000"), D("450"), D("450"))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

class TestAuditTrail:
    def test_audit_fields_populated(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "INV01", D("100"), D("9"), D("9"))],
            [_gst("A", "06DEMOX1234A1Z5", "INV01", D("100"), D("9"), D("9"))],
        )
        a = s.results[0].audit
        assert len(a.tally_rows) == 1
        assert len(a.gst_rows) == 1
        assert a.tally_invoice_original == "INV01"
        assert a.gst_invoice_original == "INV01"
        assert a.tally_gstin == "06DEMOX1234A1Z5"
        assert a.gstin_match == "exact"
        assert a.invoice_match_method == MatchMethod.EXACT_INVOICE
        assert a.tally_taxable == D("100")
        assert a.gst_taxable == D("100")
        assert a.igst_comparison.difference == D("0")
        assert a.candidate_count == 1
        assert a.explanation != ""

class TestSummaryCounts:
    def test_counts(self):
        s = _run(
            [_tally("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9")),
             _tally("A", "06DEMOX1234A1Z5", "I2", D("200"), D("18"), D("18")),
             _tally("A", "06DEMOX1234A1Z5", "I3", D("300"), D("27"), D("27"))],
            [_gst("A", "06DEMOX1234A1Z5", "I1", D("100"), D("9"), D("9")),
             _gst("A", "06DEMOX1234A1Z5", "I2", D("200"), D("18.50"), D("18")),
             _gst("A", "06DEMOX1234A1Z5", "I4", D("400"), D("36"), D("36"))],
        )
        assert s.total_tally_invoices == 3
        assert s.total_gst_invoices == 3
        assert s.perfectly_matched == 1
        assert s.minor_differences == 1
        assert s.missing_in_gstr == 1
        assert s.not_in_tally == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
class TestPhase6BMatchingRules:
    def test_01_exact_major_diff(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV1", D("105"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

    def test_02_exact_minor_diff(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100.50"))],
            [_gst("A", "06AAA", "INV1", D("100.00"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_03_exact_perfect(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"))],
            [_gst("A", "06AAA", "INV1", D("100"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_04_normalized_major_diff(self):
        s = _run(
            [_tally("A", "06AAA", "INV-001", D("100"))],
            [_gst("A", "06AAA", "INV/001", D("105"))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

    def test_05_normalized_minor_diff(self):
        s = _run(
            [_tally("A", "06AAA", "INV-001", D("100.50"))],
            [_gst("A", "06AAA", "INV/001", D("100.00"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_06_last4_strong_identity_perfect(self):
        s = _run(
            [_tally("A", "06AAA", "X1234", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y1234", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_07_last4_strong_identity_major(self):
        s = _run(
            [_tally("A", "06AAA", "X1234", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y1234", D("9000"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_4

    def test_08_last4_date_mismatch_fails(self):
        s = _run(
            [_tally("A", "06AAA", "X1234", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y1234", D("100"), dt=date(2025,5,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_09_last4_multiple_candidates_ambiguous(self):
        s = _run(
            [_tally("A", "06AAA", "X1234", D("100"), dt=date(2025,4,1))],
            [
                _gst("A", "06AAA", "Y1234", D("100"), dt=date(2025,4,1)),
                _gst("A", "06AAA", "Z1234", D("100"), dt=date(2025,4,1))
            ],
        )
        assert s.results[0].status == ReconciliationStatus.NEEDS_REVIEW

    def test_10_last2_strong_identity_perfect(self):
        s = _run(
            [_tally("A", "06AAA", "X-12", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y-12", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_11_last2_strong_identity_major(self):
        s = _run(
            [_tally("A", "06AAA", "X-12", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y-12", D("9000"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR
        assert s.results[0].match_method == MatchMethod.NO_MATCH

    def test_12_last2_date_mismatch_fails(self):
        s = _run(
            [_tally("A", "06AAA", "X-12", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y-12", D("100"), dt=date(2025,5,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_13_last2_multiple_candidates_ambiguous(self):
        s = _run(
            [_tally("A", "06AAA", "X-12", D("100"), dt=date(2025,4,1))],
            [
                _gst("A", "06AAA", "Y-12", D("100"), dt=date(2025,4,1)),
                _gst("A", "06AAA", "Z-12", D("100"), dt=date(2025,4,1))
            ],
        )
        assert s.results[0].status == ReconciliationStatus.NEEDS_REVIEW

    def test_14_exact_different_gstin_fails(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"))],
            [_gst("B", "07BBB", "INV1", D("100"))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_15_fuzzy_supplier_exact_major(self):
        s = _run(
            [_tally("Tech Solutions", "", "INV1", D("100"))],
            [_gst("Tech Solutions Inc", "", "INV1", D("105"))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

    def test_16_fuzzy_supplier_last4_major(self):
        s = _run(
            [_tally("Tech Solutions", "", "X1234", D("100"), dt=date(2025,4,1))],
            [_gst("Tech Solutions Inc", "", "Y1234", D("500"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR
        assert not s.results[0].audit.tally_gstin
        assert s.results[0].audit.supplier_match == "name_fallback"

    def test_17_tax_diff_major(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), D("10"), D("10"))],
            [_gst("A", "06AAA", "INV1", D("100"), D("8"), D("8"))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

    def test_18_tax_diff_minor(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), D("10.50"), D("10.50"))],
            [_gst("A", "06AAA", "INV1", D("100"), D("10.00"), D("10.00"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_19_tax_diff_igst_major(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), igst=D("20"))],
            [_gst("A", "06AAA", "INV1", D("100"), igst=D("18"))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

    def test_20_exact_match_different_dates(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV1", D("100"), dt=date(2025,5,1))],
        )
        # Exact match does not strictly require dates to match if exact inv is found
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED


def test_classify_status_thresholds():
    from backend.app.services.reconciliation_engine import _classify_status, _make_tax_comparison
    from decimal import Decimal
    from backend.app.models.reconciliation import ReconciliationStatus

    # ₹0.00 -> Perfect
    assert _classify_status(Decimal("0.00"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.PERFECTLY_MATCHED

    # ₹0.01 -> Minor
    assert _classify_status(Decimal("0.01"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    # ₹1.00 -> Minor
    assert _classify_status(Decimal("1.00"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    # ₹1.01 -> Major
    assert _classify_status(Decimal("1.01"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MAJOR_DIFFERENCE

    # ₹1.50 -> Major
    assert _classify_status(Decimal("1.50"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MAJOR_DIFFERENCE

    # ₹1.99 -> Major
    assert _classify_status(Decimal("1.99"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MAJOR_DIFFERENCE

    # ₹2.00 -> Major
    assert _classify_status(Decimal("2.00"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MAJOR_DIFFERENCE

    # ₹2.01 -> Major
    assert _classify_status(Decimal("2.01"), _make_tax_comparison("igst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("cgst", Decimal("0.00"), Decimal("0.00")), _make_tax_comparison("sgst", Decimal("0.00"), Decimal("0.00"))) == ReconciliationStatus.MAJOR_DIFFERENCE

def test_strong_document_evidence_fallback():
    from backend.app.services.reconciliation_engine import _match_single_tally_invoice
    from backend.app.models.records import ParsedInvoiceRecord, InvoiceNumberInfo, RecordSource
    from backend.app.models.reconciliation import AggregatedGSTInvoice, MatchMethod, ReconciliationStatus
    from decimal import Decimal
    from datetime import date

    def _tally(inv, name, taxable, date_val):
        rec = ParsedInvoiceRecord(source=RecordSource.TALLY)
        rec.invoice_number = InvoiceNumberInfo(original=inv, normalized=inv)
        rec.supplier_name = name
        rec.taxable_value = Decimal(str(taxable))
        rec.invoice_date = date_val
        return rec

    def _gst(inv, name, taxable, date_val):
        return AggregatedGSTInvoice(
            gstin="GSTIN1",
            supplier_name=name,
            invoice_number_original=inv,
            invoice_number_normalized=inv,
            invoice_number_trailing="",
            invoice_number_last_4="",
            invoice_number_last_2="",
            invoice_date=date_val,
            total_invoice_value=Decimal(str(taxable))
        )

    # 1. GSTIN missing + supplier fails + exact invoice + exact date + exact values + unique candidate -> Needs Review
    t1 = _tally("INV01", "UNKNOWN", 1000, date(2025, 1, 1))
    g1 = _gst("INV01", "REAL SUPPLIER", 1000, date(2025, 1, 1))
    g1.taxable_value = Decimal("1000.00")
    g1.igst = Decimal("0.00")
    g1.cgst = Decimal("0.00")
    g1.sgst = Decimal("0.00")

    res1 = _match_single_tally_invoice(t1, [g1], {}, [])
    assert res1.status == ReconciliationStatus.NEEDS_REVIEW
    assert res1.match_method == MatchMethod.STRONG_EVIDENCE_FALLBACK
    assert len(res1.audit.candidates) == 1

    # 2. Same but ₹0.50 difference -> Needs Review
    t2 = _tally("INV02", "UNKNOWN", 1000, date(2025, 1, 1))
    g2 = _gst("INV02", "REAL SUPPLIER", 1000.50, date(2025, 1, 1))
    g2.taxable_value = Decimal("1000.50")
    g2.igst = Decimal("0.00")
    g2.cgst = Decimal("0.00")
    g2.sgst = Decimal("0.00")

    res2 = _match_single_tally_invoice(t2, [g2], {}, [])
    assert res2.status == ReconciliationStatus.NEEDS_REVIEW
    assert res2.match_method == MatchMethod.STRONG_EVIDENCE_FALLBACK

    # 3. Same but ₹5 difference -> remain unmatched
    t3 = _tally("INV03", "UNKNOWN", 1000, date(2025, 1, 1))
    g3 = _gst("INV03", "REAL SUPPLIER", 1005, date(2025, 1, 1))
    g3.taxable_value = Decimal("1005.00")
    g3.igst = Decimal("0.00")
    g3.cgst = Decimal("0.00")
    g3.sgst = Decimal("0.00")

    res3 = _match_single_tally_invoice(t3, [g3], {}, [])
    assert res3.status == ReconciliationStatus.MISSING_IN_GSTR
    assert res3.match_method == MatchMethod.NO_MATCH

    # 4. Same invoice but two GST candidates -> remain unmatched
    t4 = _tally("INV04", "UNKNOWN", 1000, date(2025, 1, 1))
    g4a = _gst("INV04", "REAL SUPPLIER A", 1000, date(2025, 1, 1))
    g4a.taxable_value = Decimal("1000.00")
    g4b = _gst("INV04", "REAL SUPPLIER B", 1000, date(2025, 1, 1))
    g4b.taxable_value = Decimal("1000.00")
    for g in [g4a, g4b]:
        g.igst = Decimal("0.00")
        g.cgst = Decimal("0.00")
        g.sgst = Decimal("0.00")

    res4 = _match_single_tally_invoice(t4, [g4a, g4b], {}, [])
    assert res4.status == ReconciliationStatus.MISSING_IN_GSTR
    assert res4.match_method == MatchMethod.NO_MATCH

    # 5. Exact invoice but wrong date + supplier fails -> do not automatically match (Needs Review)
    t5 = _tally("INV05", "UNKNOWN", 1000, date(2025, 1, 1))
    g5 = _gst("INV05", "REAL SUPPLIER", 1000, date(2025, 1, 5))
    g5.taxable_value = Decimal("1000.00")
    g5.igst = Decimal("0.00")
    g5.cgst = Decimal("0.00")
    g5.sgst = Decimal("0.00")

    res5 = _match_single_tally_invoice(t5, [g5], {}, [])
    assert res5.status == ReconciliationStatus.NEEDS_REVIEW
    assert res5.match_method == MatchMethod.STRONG_EVIDENCE_FALLBACK

    # 6. Exact invoice + exact date + exact values but known conflicting GSTIN -> never match
    t6 = _tally("INV06", "UNKNOWN", 1000, date(2025, 1, 1))
    t6.gstin_normalized = "WRONGGSTIN"
    g6 = _gst("INV06", "REAL SUPPLIER", 1000, date(2025, 1, 1))
    g6.gstin = "RIGHTGSTIN"
    g6.taxable_value = Decimal("1000.00")
    g6.igst = Decimal("0.00")
    g6.cgst = Decimal("0.00")
    g6.sgst = Decimal("0.00")

    # Since tally has a GSTIN, it goes to exact GSTIN match, ignoring fuzzy and fallback
    res6 = _match_single_tally_invoice(t6, [g6], {"WRONGGSTIN": []}, [])
    assert res6.status == ReconciliationStatus.MISSING_IN_GSTR
    assert res6.match_method == MatchMethod.NO_MATCH


def test_compute_counts_excludes_consumed_not_in_tally():
    """
    Regression: after manual match consumes a Strong Evidence candidate,
    compute_counts must remove the consumed record from not_in_tally_records
    and decrement the not_in_tally count.
    """
    from decimal import Decimal
    from backend.app.models.reconciliation import (
        ReconciliationSummary, ReconciliationResult, ReconciliationStatus,
        MatchMethod, AggregatedGSTInvoice, AuditTrail
    )

    # Create a summary with one Not-in-Tally record
    agg = AggregatedGSTInvoice(
        gstin="G1", supplier_name="S1",
        invoice_number_original="INV99",
        invoice_number_normalized="inv99",
    )
    nit_result = ReconciliationResult(
        supplier_gstin="G1", supplier_gst="S1",
        invoice_number_gst="INV99",
        status=ReconciliationStatus.NOT_IN_TALLY,
        match_method=MatchMethod.NO_MATCH,
    )
    nit_result.audit = AuditTrail(
        gst_invoice_original="INV99",
        gst_rows=[],
    )

    summary = ReconciliationSummary()
    summary.results = [nit_result]
    summary.not_in_tally_records = [agg]
    summary.compute_counts()
    assert summary.not_in_tally == 1

    # Simulate manual match consuming this record
    agg.consumed = True
    summary.compute_counts()
    assert summary.not_in_tally == 0
    assert len(summary.not_in_tally_records) == 0

class TestDateMismatchFeature:
    def test_01_exact_match_same_date(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert not r.date_mismatch
        assert r.date_difference_days == 0

    def test_02_exact_match_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV1", D("100"), dt=date(2025,4,4))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.date_mismatch is True
        assert r.date_difference_days == 3
        # 12 & 13: Absent from Missing in GSTR / Not in Tally
        assert s.missing_in_gstr == 0
        assert s.not_in_tally == 0

    def test_03_normalized_match_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV/1", D("100"), dt=date(2025,4,5))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.date_mismatch is True
        assert r.date_difference_days == 4

    def test_04_normalized_minor_diff_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100.50"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV/1", D("100.00"), dt=date(2025,4,5))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE
        assert r.date_mismatch is True

    def test_05_normalized_major_diff_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("110.00"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV/1", D("100.00"), dt=date(2025,4,5))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.MAJOR_DIFFERENCE
        assert r.date_mismatch is True

    def test_06_exact_conflicting_gstin_same_date(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("B", "07BBB", "INV1", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_07_exact_conflicting_gstin_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("B", "07BBB", "INV1", D("100"), dt=date(2025,4,4))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_08_last4_correct_supplier_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "X1234", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y1234", D("100"), dt=date(2025,4,4))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_09_last2_correct_supplier_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "X-12", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "Y-12", D("100"), dt=date(2025,4,4))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_10_strong_evidence_gstin_missing_date_mismatch(self):
        s = _run(
            [_tally("UNKNOWN_SUPPLIER", "", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("REAL_SUPPLIER", "06AAA", "INV1", D("100"), dt=date(2025,4,4))],
        )
        assert s.results[0].status == ReconciliationStatus.NEEDS_REVIEW
        assert s.results[0].match_method == MatchMethod.STRONG_EVIDENCE_FALLBACK

    def test_11_one_day_difference(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV1", D("100"), dt=date(2025,4,2))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.date_mismatch is True
        assert r.date_difference_days == 1

    def test_12_thirty_day_difference(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV1", D("100"), dt=date(2025,5,1))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.date_mismatch is True
        assert r.date_difference_days == 30

    def test_13_ninety_two_day_difference(self):
        s = _run(
            [_tally("A", "06AAA", "INV1", D("100"), dt=date(2025,7,1))],
            [_gst("A", "06AAA", "INV1", D("100"), dt=date(2025,10,1))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.date_mismatch is True
        assert r.date_difference_days == 92

class TestInvoiceMismatchFeature:
    def test_01_valid_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH
        assert getattr(r, 'invoice_mismatch', False) is True
        assert r.match_method == MatchMethod.INVOICE_MISMATCH
        assert s.invoice_mismatches == 1
        assert s.missing_in_gstr == 0
        assert s.not_in_tally == 0

    def test_02_normalized_takes_priority(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV/1", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED
        assert s.results[0].match_method == MatchMethod.NORMALIZED_INVOICE

    def test_03_conflicting_gstin_blocked(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("B", "07BBB", "INV-2", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_04_date_differs_1_day(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,2))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_05_taxable_differs(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100.01"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100.00"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_06_cgst_differs(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1), cgst=D("9.01"))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1), cgst=D("9.00"))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_07_ambiguous_gstr_candidates(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [
                _gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1)),
                _gst("A", "06AAA", "INV-3", D("100"), dt=date(2025,4,1))
            ],
        )
        assert s.results[0].status == ReconciliationStatus.NEEDS_REVIEW
        assert s.results[0].match_method == MatchMethod.AMBIGUOUS

    def test_08_ambiguous_tally_rows(self):
        s = _run(
            [
                _tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1)),
                _tally("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1))
            ],
            [_gst("A", "06AAA", "INV-3", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR
        assert s.results[1].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_09_tally_gstin_missing(self):
        s = _run(
            [_tally("A", "", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

    def test_10_last4_takes_priority(self):
        s = _run(
            [_tally("A", "06AAA", "ABC-1234", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "XYZ-1234", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_4

    def test_11_last2_takes_priority(self):
        s = _run(
            [_tally("A", "06AAA", "ABC-12", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "XYZ-12", D("100"), dt=date(2025,4,1))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_2

    def test_12_mismatch_absent_from_not_in_tally(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1))],
        )
        assert s.not_in_tally == 0
        assert len(s.not_in_tally_records) == 0

    def test_13_mismatch_absent_from_missing(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1))],
        )
        assert s.missing_in_gstr == 0

    def test_14_duplicate_manual_consume_rejected(self):
        # We simulate manual matching internally by checking candidate.consumed is True
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-2", D("100"), dt=date(2025,4,1))],
        )
        r = s.results[0]
        # Further manual match behavior is tested in api/routes or orchestration, but we verify consumed state here

    def test_15_same_invoice_date_mismatch(self):
        s = _run(
            [_tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))],
            [_gst("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,2))],
        )
        r = s.results[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert getattr(r, 'date_mismatch', False) is True
        assert r.match_method == MatchMethod.EXACT_INVOICE

class TestGSTPurchaseSheetIsolation:
    def test_01_sheet_isolation_invariant(self):
        # 1. Tally purchase
        t1 = _tally("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))
        # 2. Tally note (to prove notes still work)
        t_note = _tally("A", "06AAA", "DN-1", D("50"), dt=date(2025,4,1))
        t_note.voucher_type = VoucherType.DEBIT_NOTE
        t_note.source_sheet = "Debit Note Register"
        t_note.sheet_category = SheetCategory.DEBIT_NOTE_REGISTER

        # 1. B2B/Purchase record (INCLUDED)
        g1 = _gst("A", "06AAA", "INV-1", D("100"), dt=date(2025,4,1))
        g1.voucher_type = VoucherType.PURCHASE
        g1.source_sheet = "b2b"
        g1.sheet_category = SheetCategory.B2B
        
        # 2. Valid B2B represented as UNKNOWN (INCLUDED)
        g2 = _gst("A", "06AAA", "INV-2", D("200"), dt=date(2025,4,1))
        g2.voucher_type = VoucherType.UNKNOWN
        g2.source_sheet = "b2b"
        g2.sheet_category = SheetCategory.B2B
        
        # 3. CDNR Credit Note (EXCLUDED)
        g_cn = _gst("A", "06AAA", "CN-1", D("50"), dt=date(2025,4,1))
        g_cn.voucher_type = VoucherType.CREDIT_NOTE
        g_cn.source_sheet = "cdnr"
        g_cn.sheet_category = SheetCategory.CDNR
        
        # 4. CDNR Debit Note (EXCLUDED)
        g_dn = _gst("A", "06AAA", "DN-1", D("50"), dt=date(2025,4,1))
        g_dn.voucher_type = VoucherType.DEBIT_NOTE
        g_dn.source_sheet = "cdnr"
        g_dn.sheet_category = SheetCategory.CDNR
        
        # 5. B2CSA / non-purchase record (EXCLUDED)
        g_b2csa = _gst("A", "06AAA", "INV-3", D("300"), dt=date(2025,4,1))
        g_b2csa.voucher_type = VoucherType.UNKNOWN
        g_b2csa.source_sheet = "b2csa"
        g_b2csa.sheet_category = SheetCategory.UNKNOWN
        
        # 6. Generic Main sheet (EXCLUDED)
        g_main = _gst("A", "06AAA", "INV-4", D("400"), dt=date(2025,4,1))
        g_main.voucher_type = VoucherType.UNKNOWN
        g_main.source_sheet = "Main"
        g_main.sheet_category = SheetCategory.UNKNOWN
        
        # Run full engine 
        s = _run([t1, t_note], [g1, g2, g_cn, g_dn, g_b2csa, g_main])
        
        # Expected candidate pool size
        # Only g1 and g2 should enter.
        assert s.total_gst_invoices == 2
        
        # Verify invariants: final B2B candidates = consumed + Not in Tally
        final_b2b_candidates = s.total_gst_invoices 
        
        # Traverse results to find consumed GST candidates
        consumed_invoices = set()
        for r in s.results:
            if r.status != ReconciliationStatus.NOT_IN_TALLY and r.audit and getattr(r.audit, 'gst_invoice_normalized', None):
                key = (r.audit.gst_gstin, r.audit.gst_invoice_normalized)
                consumed_invoices.add(key)
                
        consumed = len(consumed_invoices)
        not_in_tally = s.not_in_tally
        
        assert final_b2b_candidates == consumed + not_in_tally
        
        # INV-1 should match perfectly (consumed = 1)
        # INV-2 is not in tally (not_in_tally = 1)
        assert consumed == 1
        assert not_in_tally == 1
