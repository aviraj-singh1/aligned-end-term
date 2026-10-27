"""
Tests for Credit Note ↔ Debit Note Reconciliation Engine.
30 test cases covering all note matching scenarios.
"""
import sys, os
from datetime import date
from decimal import Decimal
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.app.models.records import (
    ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType,
)
from backend.app.models.reconciliation import (
    MatchMethod, ReconciliationStatus,
)
from backend.app.services.notes_reconciliation_engine import (
    aggregate_gst_credit_notes, reconcile_notes,
)
from backend.app.services.normalizer import normalize_invoice_number

D = Decimal

def _dn(supplier, gstin, note_no, taxable, cgst=D("0"), sgst=D("0"),
         igst=D("0"), dt=date(2025,5,10), row=1):
    """Create a Tally Debit Note record."""
    info = normalize_invoice_number(note_no)
    return ParsedInvoiceRecord(
        source=RecordSource.TALLY, source_sheet="Debit Note Register",
        source_row=row, supplier_name=supplier,
        gstin_original=gstin, gstin_normalized=gstin.upper().replace(" ","") if gstin else "",
        gstin_valid=bool(gstin), invoice_number=info,
        invoice_date=dt, voucher_type=VoucherType.DEBIT_NOTE,
        taxable_value=taxable, igst=igst, cgst=cgst, sgst=sgst,
    )

def _cn(supplier, gstin, note_no, taxable, cgst=D("0"), sgst=D("0"),
         igst=D("0"), dt=date(2025,5,10), row=1, note_type="Credit"):
    """Create a GST Credit Note record (CDNR)."""
    info = normalize_invoice_number(note_no)
    return ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="cdnr", source_row=row,
        supplier_name=supplier, gstin_original=gstin,
        gstin_normalized=gstin.upper().replace(" ","") if gstin else "",
        gstin_valid=bool(gstin), invoice_number=info,
        invoice_date=dt, voucher_type=VoucherType.CREDIT_NOTE,
        taxable_value=taxable, igst=igst, cgst=cgst, sgst=sgst,
        note_number=note_no, note_date=dt, note_type=note_type,
    )

def _run(dn_list, cn_list):
    return reconcile_notes(dn_list, cn_list)


class Test01ExactNoteMatch:
    def test_exact(self):
        s = _run(
            [_dn("ABC", "06DEMOX1234A1Z5", "DN001", D("5000"), D("450"), D("450"))],
            [_cn("ABC Corp", "06DEMOX1234A1Z5", "DN001", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED
        assert s.results[0].match_method == MatchMethod.EXACT_INVOICE

class Test02SameGSTINNameDiffers:
    def test_name_differs(self):
        s = _run(
            [_dn("ABC Traders", "06DEMOX1234A1Z5", "DN001", D("1000"), D("90"), D("90"))],
            [_cn("ABC TRADERS PVT LTD", "06DEMOX1234A1Z5", "DN001", D("1000"), D("90"), D("90"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test03NormalizedNoteMatch:
    def test_normalized(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN-2025-001", D("5000"), D("450"), D("450"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN/2025/001", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].match_method == MatchMethod.NORMALIZED_INVOICE

class Test04PrefixDifference:
    def test_prefix(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "ABC-DN-100", D("5000"), D("450"), D("450"))],
            [_cn("A", "06DEMOX1234A1Z5", "ABC/DN/100", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test05FYPrefixDifference:
    def test_fy(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN/2025-26/4587", D("10000"), D("900"), D("900"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN-2025-26-4587", D("10000"), D("900"), D("900"))],
        )
        assert s.results[0].match_method == MatchMethod.NORMALIZED_INVOICE

class Test06Last4UniqueNote:
    def test_last4(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN4587", D("10000"), D("900"), D("900"))],
            [_cn("A", "06DEMOX1234A1Z5", "VENDOR/CN/4587", D("10000"), D("900"), D("900"))],
        )
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_4

class Test07Last4AmbiguousNote:
    def test_last4_ambig(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN4587", D("10000"), D("900"), D("900"))],
            [_cn("A", "06DEMOX1234A1Z5", "X/4587", D("10000"), D("900"), D("900")),
             _cn("A", "06DEMOX1234A1Z5", "Y/4587", D("10000"), D("900"), D("900"))],
        )
        r = [x for x in s.results if x.invoice_number_tally == "DN4587"][0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

class Test08Last2UniqueNote:
    def test_last2(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "N99", D("5000"), D("450"), D("450"))],
            [_cn("A", "06DEMOX1234A1Z5", "LONG-NOTE-99", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].match_method == MatchMethod.INVOICE_LAST_2

class Test09Last2AmbiguousNote:
    def test_last2_ambig(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "N99", D("5000"), D("450"), D("450"))],
            [_cn("A", "06DEMOX1234A1Z5", "A-99", D("5000"), D("450"), D("450")),
             _cn("A", "06DEMOX1234A1Z5", "B-99", D("5000"), D("450"), D("450"))],
        )
        r = [x for x in s.results if x.invoice_number_tally == "N99"][0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

class Test10ConflictingGSTINNeverMatch:
    def test_gstin_conflict(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN001", D("5000"), D("450"), D("450"))],
            [_cn("A", "07BBBBB1234B1Z1", "DN001", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

class Test11GSTINMissingSupplierFallback:
    def test_fallback(self):
        s = _run(
            [_dn("Kumar Traders", "", "DN001", D("1000"), D("90"), D("90"))],
            [_cn("Kumar Traders", "06DEMOX1234A1Z5", "DN001", D("1000"), D("90"), D("90"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED
        assert s.results[0].audit.supplier_match == "name_fallback"

class Test12FuzzyNameAloneNotEnough:
    def test_fuzzy_alone(self):
        s = _run(
            [_dn("Kumar Traders", "", "TOTALLY_DIFF", D("999"), D("1"), D("1"))],
            [_cn("Kumar Traders Pvt", "06DEMOX1234A1Z5", "OTHER_CN", D("5000"), D("450"), D("450"))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

class Test13ExactTaxValues:
    def test_exact_tax(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test14OnePaisaDiff:
    def test_paisa(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9.01"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9.00"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

class Test15OneRupeeDiff:
    def test_one_rupee(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("10"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

class Test16OverOneRupeeDiff:
    def test_over_one(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("11"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.MAJOR_DIFFERENCE

class Test17TallyDNMissingInGST:
    def test_missing(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN_ONLY", D("100"), D("9"), D("9"))],
            [],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR
        assert s.missing_in_gst == 1

class Test18GSTCNNotInTally:
    def test_not_in_tally(self):
        s = _run(
            [],
            [_cn("A", "06DEMOX1234A1Z5", "CN_ONLY", D("100"), D("9"), D("9"))],
        )
        assert s.not_in_tally == 1

class Test19IGSTNote:
    def test_igst(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), igst=D("18"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), igst=D("18"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test20CGSTSGSTNote:
    def test_cgst_sgst(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].igst_diff == D("0")

class Test21DuplicateTallyDN:
    def test_dup(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"), row=1),
             _dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"), row=2)],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        dupes = [r for r in s.results if r.status == ReconciliationStatus.POTENTIAL_DUPLICATE]
        assert len(dupes) == 2

class Test22DuplicateGSTCN:
    def test_gst_dup_consumed(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"), row=1),
             _dn("A", "06DEMOX1234A1Z5", "DN2", D("200"), D("18"), D("18"), row=2)],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        matched = [r for r in s.results if r.status == ReconciliationStatus.PERFECTLY_MATCHED]
        missing = [r for r in s.results if r.status == ReconciliationStatus.MISSING_IN_GSTR]
        assert len(matched) == 1
        assert len(missing) == 1

class Test23ConsumedCannotReuse:
    def test_consumed(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"), row=1),
             _dn("A", "06DEMOX1234A1Z5", "DN-X", D("100"), D("9"), D("9"), row=2)],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9"))],
        )
        matched = [r for r in s.results if r.status == ReconciliationStatus.PERFECTLY_MATCHED]
        assert len(matched) == 1

class Test24NumericNoteNumber:
    def test_numeric(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "12345", D("100"), D("9"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "12345", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test25LeadingZeroNote:
    def test_leading_zero(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "00123", D("100"), D("9"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "00123", D("100"), D("9"), D("9"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test26BlankTaxes:
    def test_blank(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"))],
        )
        assert s.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED

class Test27SameSuffixDiffTaxable:
    def test_diff_taxable(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "X-100", D("5000"), D("450"), D("450"))],
            [_cn("A", "06DEMOX1234A1Z5", "Y-100", D("99999"), D("9000"), D("9000"))],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR

class Test28SameSuffixMultipleCandidatesAmbiguous:
    def test_ambig(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "X-100", D("5000"), D("450"), D("450"))],
            [_cn("A", "06DEMOX1234A1Z5", "Y-100", D("5000"), D("450"), D("450")),
             _cn("A", "06DEMOX1234A1Z5", "Z-100", D("5000"), D("450"), D("450"))],
        )
        r = [x for x in s.results if x.invoice_number_tally == "X-100"][0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

class Test29CDNRDetection:
    def test_cdnr_structural(self):
        """Credit notes must come from CDNR-like source, not B2B."""
        cn = _cn("A", "06DEMOX1234A1Z5", "CN01", D("100"), D("9"), D("9"))
        cn.source_sheet = "cdnr"
        agg = aggregate_gst_credit_notes([cn])
        assert len(agg) == 1
        assert agg[0].note_number_original == "CN01"

class Test30B2BNotTreatedAsCreditNote:
    def test_b2b_excluded(self):
        """Normal B2B purchase invoices must NOT be treated as credit notes."""
        b2b = ParsedInvoiceRecord(
            source=RecordSource.GST, source_sheet="b2b",
            supplier_name="A", gstin_normalized="06DEMOX1234A1Z5",
            invoice_number=normalize_invoice_number("INV001"),
            voucher_type=VoucherType.PURCHASE,
            taxable_value=D("1000"), cgst=D("90"), sgst=D("90"),
        )
        agg = aggregate_gst_credit_notes([b2b])
        assert len(agg) == 0  # B2B should not appear as credit note

        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "INV001", D("1000"), D("90"), D("90"))],
            [b2b],
        )
        assert s.results[0].status == ReconciliationStatus.MISSING_IN_GSTR


def test_opposite_note_signs_match_by_magnitude_and_preserve_raw_values():
    summary = _run(
        [_dn("A", "06DEMOX1234A1Z5", "DN-SIGN", D("-100"), D("-9"), D("-9"))],
        [_cn("A", "06DEMOX1234A1Z5", "DN-SIGN", D("100"), D("9"), D("9"))],
    )
    result = summary.results[0]
    assert result.reconciliation_type == "NOTE"
    assert result.status == ReconciliationStatus.PERFECTLY_MATCHED
    assert result.audit.tally_taxable == D("-100")
    assert result.audit.gst_taxable == D("100")
    assert result.audit.taxable_difference == D("0")
    assert result.cgst_diff == result.sgst_diff == D("0")


def test_opposite_note_signs_are_used_for_suffix_evidence():
    summary = _run(
        [_dn("A", "06DEMOX1234A1Z5", "DN4587", D("-100"), D("-9"), D("-9"))],
        [_cn("A", "06DEMOX1234A1Z5", "VENDOR/CN/4587", D("100"), D("9"), D("9"))],
    )
    assert summary.results[0].match_method == MatchMethod.INVOICE_LAST_4
    assert summary.results[0].status == ReconciliationStatus.PERFECTLY_MATCHED


class TestNoteAuditTrail:
    def test_audit(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN01", D("100"), D("9"), D("9"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN01", D("100"), D("9"), D("9"))],
        )
        a = s.results[0].audit
        assert len(a.tally_rows) == 1
        assert len(a.gst_rows) == 1
        assert a.tally_invoice_original == "DN01"
        assert a.gst_invoice_original == "DN01"
        assert a.gstin_match == "exact"
        assert a.invoice_match_method == MatchMethod.EXACT_INVOICE
        assert a.final_status == ReconciliationStatus.PERFECTLY_MATCHED

class TestNoteSummaryCounts:
    def test_counts(self):
        s = _run(
            [_dn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9")),
             _dn("A", "06DEMOX1234A1Z5", "DN2", D("200"), D("18"), D("18")),
             _dn("A", "06DEMOX1234A1Z5", "DN3", D("300"), D("27"), D("27"))],
            [_cn("A", "06DEMOX1234A1Z5", "DN1", D("100"), D("9"), D("9")),
             _cn("A", "06DEMOX1234A1Z5", "DN2", D("200"), D("18.50"), D("18")),
             _cn("A", "06DEMOX1234A1Z5", "DN4", D("400"), D("36"), D("36"))],
        )
        assert s.total_tally_debit_notes == 3
        assert s.total_gst_credit_notes == 3
        assert s.perfectly_matched == 1
        assert s.minor_differences == 1
        assert s.missing_in_gst == 1
        assert s.not_in_tally == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


@pytest.mark.parametrize("category", [SheetCategory.CDNR, SheetCategory.CDNRA])
@pytest.mark.parametrize("raw,expected", [
    ("Credit Note", VoucherType.CREDIT_NOTE), ("C", VoucherType.CREDIT_NOTE),
    ("CR", VoucherType.CREDIT_NOTE), (" credit ", VoucherType.CREDIT_NOTE),
    ("Debit Note", VoucherType.DEBIT_NOTE), ("D", VoucherType.DEBIT_NOTE),
    ("DR", VoucherType.DEBIT_NOTE), ("debit", VoucherType.DEBIT_NOTE),
    ("", VoucherType.UNKNOWN), ("other", VoucherType.UNKNOWN),
    ("Credit or Debit", VoucherType.UNKNOWN),
])
def test_cdnr_direction_is_explicit_and_survives_persistence(category, raw, expected):
    from backend.app.services.file_parser import _row_to_record
    from backend.app.services.session_store import encode_session_value, decode_session_value
    headers = ["GSTIN", "Note Number", "Note Type", "Taxable Value"]
    record = _row_to_record(
        ["06DEMOX1234A1Z5", "N1", raw, 100], headers,
        {"gstin": 0, "note_number": 1, "revised_note_number": 1, "note_type": 2, "taxable_value": 3},
        category, category.value, 2,
    )
    assert record.voucher_type == record.note_direction == expected
    assert record.note_type == raw.strip()
    restored = decode_session_value(encode_session_value(record))
    assert restored.note_direction == expected
    assert len(aggregate_gst_credit_notes([restored])) == (expected == VoucherType.CREDIT_NOTE)


@pytest.mark.parametrize("raw", ["Debit Note", "D", "DR", "other", "Credit or Debit", ""])
def test_legacy_forced_credit_voucher_cannot_override_cdnr_direction(raw):
    record = _cn("A", "06DEMOX1234A1Z5", "N1", D("100"), note_type=raw)
    record.sheet_category = SheetCategory.CDNR
    summary = reconcile_notes([], [record])
    assert summary.total_gst_credit_notes == 0
    assert summary.results == summary.not_in_tally_notes == []


def test_positive_direction_metadata_api_and_excel(tmp_path):
    from openpyxl import load_workbook
    from backend.app.services.file_parser import _row_to_record
    from backend.app.services.result_pagination import serialize_result
    from backend.app.services.excel_exporter import export_to_excel
    from backend.app.services.reconciliation_engine import reconcile_purchases
    from backend.app.api.schemas import ResultModel
    gst = _row_to_record(
        ["06DEMOX1234A1Z5", "N1", "Credit Note", "10-05-2025", 100, 9, 9],
        ["GSTIN", "Note Number", "Note Type", "Note Date", "Taxable Value", "CGST", "SGST"],
        {"gstin": 0, "note_number": 1, "note_type": 2, "note_date": 3,
         "taxable_value": 4, "cgst": 5, "sgst": 6}, SheetCategory.CDNR, "CDNR", 2,
    )
    summary = reconcile_notes([_dn("A", "06DEMOX1234A1Z5", "N1", D("100"), D("9"), D("9"))], [gst])
    result = summary.results[0]
    assert result.status == ReconciliationStatus.PERFECTLY_MATCHED
    assert result.match_method == MatchMethod.EXACT_INVOICE
    payload = ResultModel(**serialize_result(result, note=True))
    assert payload.gst_note_direction == "Credit Note"
    assert payload.accounts_note_direction == "Debit Note"
    target = tmp_path / "notes.xlsx"
    export_to_excel(str(target), reconcile_purchases([], []), summary, "test")
    wb = load_workbook(target)
    assert wb["Notes"].cell(1, 6).value == "Credit Note No. (GST)"
    assert wb["Notes"].cell(2, 6).value == "N1"
    wb.close()
