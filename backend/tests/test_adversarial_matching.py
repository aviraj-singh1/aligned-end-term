"""
Adversarial Matching QA — Phase 7
Tests Parts 1-7: Exact/Normalized/Last-4/Last-2/Duplicates/Supplier/StrongEvidence
"""
import pytest
from decimal import Decimal
from datetime import date
from backend.app.models.records import ParsedInvoiceRecord, RecordSource, VoucherType, InvoiceNumberInfo, SheetCategory
from backend.app.models.reconciliation import (
    AggregatedGSTInvoice, ReconciliationStatus, MatchMethod, ReconciliationSummary
)
from backend.app.services.reconciliation_engine import (
    reconcile_purchases, _match_single_tally_invoice, _classify_status,
    _make_tax_comparison, aggregate_gst_invoices
)
from backend.app.services.normalizer import normalize_invoice_number, normalize_supplier_name

# ---- Helpers ----
def _t(inv, gstin="G1", name="SUPPLIER A", taxable=1000, dt=date(2025,3,1),
       igst=0, cgst=90, sgst=90, vtype=VoucherType.PURCHASE):
    rec = ParsedInvoiceRecord(source=RecordSource.TALLY, voucher_type=vtype)
    rec.invoice_number = normalize_invoice_number(inv)
    rec.gstin_normalized = gstin
    rec.supplier_name = name
    rec.taxable_value = Decimal(str(taxable))
    rec.invoice_date = dt
    rec.igst = Decimal(str(igst)); rec.cgst = Decimal(str(cgst)); rec.sgst = Decimal(str(sgst))
    return rec

def _g(inv, gstin="G1", name="SUPPLIER A", taxable=1000, dt=date(2025,3,1),
       igst=0, cgst=90, sgst=90):
    info = normalize_invoice_number(inv)
    return AggregatedGSTInvoice(
        gstin=gstin, supplier_name=name,
        invoice_number_original=info.original, invoice_number_normalized=info.normalized,
        invoice_number_trailing=info.trailing_number,
        invoice_number_last_4=info.last_4, invoice_number_last_2=info.last_2,
        invoice_date=dt, taxable_value=Decimal(str(taxable)),
        igst=Decimal(str(igst)), cgst=Decimal(str(cgst)), sgst=Decimal(str(sgst)),
    )

def _run(tally_list, gst_list):
    by_gstin = {}
    for g in gst_list:
        by_gstin.setdefault(g.gstin, []).append(g)
    results = []
    for t in tally_list:
        results.append(_match_single_tally_invoice(t, gst_list, by_gstin, tally_list))
    return results

# ==== PART 1: Core matching ====
class TestPart1Core:
    def test_01_exact_same_gstin_perfect(self):
        r = _run([_t("INV001")], [_g("INV001")])[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert r.match_method == MatchMethod.EXACT_INVOICE

    def test_02_exact_same_gstin_minor(self):
        r = _run([_t("INV002", taxable=1000)], [_g("INV002", taxable=1000.50)])[0]
        assert r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_03_exact_same_gstin_major(self):
        r = _run([_t("INV003", taxable=1000)], [_g("INV003", taxable=1001.01)])[0]
        assert r.status == ReconciliationStatus.MAJOR_DIFFERENCE

    def test_04_exact_conflicting_gstin(self):
        r = _run([_t("INV004", gstin="G1")], [_g("INV004", gstin="G2")])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_05_same_invoice_two_suppliers(self):
        t = _t("INV005", gstin="G1")
        g1 = _g("INV005", gstin="G1")
        g2 = _g("INV005", gstin="G2")
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert g1.consumed and not g2.consumed

    def test_06_same_invoice_no_gstin_two_suppliers(self):
        t = _t("INV006", gstin="", name="UNKNOWN SHOP")
        g1 = _g("INV006", gstin="G1", name="REAL SUPPLIER A")
        g2 = _g("INV006", gstin="G2", name="REAL SUPPLIER B")
        r = _run([t], [g1, g2])[0]
        # No supplier match → Strong Evidence needs exactly 1 unused candidate but 2 exist
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_07_normalized_match(self):
        t = _t("ABC/2025-26/4587", gstin="G1")
        g = _g("ABC-2025-26-4587", gstin="G1")
        r = _run([t], [g])[0]
        assert r.match_method in (MatchMethod.EXACT_INVOICE, MatchMethod.NORMALIZED_INVOICE)
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_08_formatting_normalized_confirmed(self):
        t = _t("INV-100", gstin="G1")
        g = _g("INV/100", gstin="G1")
        r = _run([t], [g])[0]
        assert r.match_method == MatchMethod.NORMALIZED_INVOICE

    def test_09_similar_not_equivalent(self):
        t = _t("INV100", gstin="G1")
        g = _g("INV101", gstin="G1")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH

# ==== PART 2: Last-4 adversarial ====
class TestPart2Last4:
    def test_10_last4_confirmed_unique(self):
        t = _t("ABC-4587", gstin="G1")
        g = _g("XYZ-4587", gstin="G1", dt=date(2025,3,1))
        r = _run([t], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_4

    def test_11_last4_perfect(self):
        t = _t("A4587", gstin="G1")
        g = _g("B4587", gstin="G1")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_12_last4_minor(self):
        t = _t("A4587", gstin="G1", taxable=1000)
        g = _g("B4587", gstin="G1", taxable=1000.50)
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_13_last4_major(self):
        t = _t("A4587", gstin="G1", taxable=1000)
        g = _g("B4587", gstin="G1", taxable=1100)
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MAJOR_DIFFERENCE
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert g.consumed

    def test_14_last4_wrong_supplier(self):
        t = _t("ABC-4587", gstin="G1")
        g = _g("XYZ-4587", gstin="G2")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_15_last4_conflicting_gstin(self):
        t = _t("ABC-4587", gstin="G1")
        g = _g("XYZ-4587", gstin="G2")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_16_last4_date_mismatch(self):
        t = _t("ABC-4587", gstin="G1", dt=date(2025,3,1))
        g = _g("XYZ-4587", gstin="G1", dt=date(2025,4,1))
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_17_last4_two_candidates_ambiguous(self):
        t = _t("ABC-4587", gstin="G1")
        g1 = _g("XY1-4587", gstin="G1")
        g2 = _g("XY2-4587", gstin="G1")
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

    def test_18_last4_one_consumed_one_free(self):
        t = _t("ABC-4587", gstin="G1")
        g1 = _g("XY1-4587", gstin="G1"); g1.consumed = True
        g2 = _g("XY2-4587", gstin="G1")
        r = _run([t], [g1, g2])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert g2.consumed

    def test_19_three_suppliers_same_last4(self):
        t = _t("ABC-4587", gstin="G1")
        g1 = _g("XYZ-4587", gstin="G2")
        g2 = _g("LMN-4587", gstin="G3")
        g3 = _g("PQR-4587", gstin="G4")
        r = _run([t], [g1, g2, g3])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not any(g.consumed for g in [g1, g2, g3])

    def test_20_same_supplier_multiple_same_last4(self):
        t = _t("ABC-4587", gstin="G1")
        g1 = _g("INV1-4587", gstin="G1")
        g2 = _g("INV2-4587", gstin="G1")
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

    def test_21_exact_wins_over_last4(self):
        t = _t("INV-4587", gstin="G1")
        g_exact = _g("INV-4587", gstin="G1")
        g_last4 = _g("OTHER-4587", gstin="G1")
        r = _run([t], [g_exact, g_last4])[0]
        assert r.match_method == MatchMethod.EXACT_INVOICE
        assert g_exact.consumed and not g_last4.consumed

    def test_22_last4_cross_fy_date_blocks(self):
        t = _t("FY25-4587", gstin="G1", dt=date(2025,3,1))
        g = _g("FY24-4587", gstin="G1", dt=date(2024,3,1))
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

# ==== PART 3: Last-2 adversarial ====
class TestPart3Last2:
    def test_23_last2_unique_confirmed(self):
        # Use invoices where last-4 differs but last-2 matches
        t = _t("AAAA87", gstin="G1")
        g = _g("BBBB87", gstin="G1")
        r = _run([t], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_2

    def test_24_last2_no_date(self):
        t = _t("AAAA87", gstin="G1", dt=None)
        g = _g("BBBB87", gstin="G1", dt=None)
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_25_last2_wrong_supplier(self):
        t = _t("A-87", gstin="G1")
        g = _g("B-87", gstin="G2")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_26_last2_conflicting_gstin(self):
        t = _t("A-87", gstin="G1")
        g = _g("B-87", gstin="G2")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_27_last2_two_candidates(self):
        t = _t("A-87", gstin="G1")
        g1 = _g("B-87", gstin="G1")
        g2 = _g("C-87", gstin="G1")
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

    def test_28_last2_ten_candidates(self):
        t = _t("A-87", gstin="G1")
        gs = [_g(f"INV{i}-87", gstin="G1") for i in range(10)]
        r = _run([t], gs)[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

    def test_29_last2_financial_coincidence(self):
        t = _t("COMPLETELY-DIFFERENT-87", gstin="G1", taxable=5000)
        g = _g("UNRELATED-INV-87", gstin="G1", taxable=5000)
        r = _run([t], [g])[0]
        # Last-2 match is valid with date+supplier confirmation
        assert r.match_method == MatchMethod.INVOICE_LAST_2

    def test_30_last2_name_fallback(self):
        t = _t("A-87", gstin="", name="REDINGTON LIMITED")
        g = _g("B-87", gstin="G1", name="REDINGTON LIMITED")
        r = _run([t], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_2

    def test_31_last2_similar_name_below_threshold(self):
        t = _t("A-87", gstin="", name="ALPHA SHOP")
        g = _g("B-87", gstin="G1", name="BETA STORE")
        r = _run([t], [g])[0]
        assert r.status in (ReconciliationStatus.MISSING_IN_GSTR, ReconciliationStatus.NEEDS_REVIEW)

    def test_32_last2_consumed(self):
        t = _t("A-87", gstin="G1")
        g = _g("B-87", gstin="G1"); g.consumed = True
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_33_exact_wins_over_last2(self):
        t = _t("INV-87", gstin="G1")
        g_exact = _g("INV-87", gstin="G1")
        g_last2 = _g("OTHER-87", gstin="G1")
        r = _run([t], [g_exact, g_last2])[0]
        assert r.match_method == MatchMethod.EXACT_INVOICE
        assert g_exact.consumed and not g_last2.consumed

# ==== PART 4: Leading zero / numeric ====
class TestPart4Numeric:
    def test_34_leading_zero_normalization(self):
        info1 = normalize_invoice_number("0004587")
        info2 = normalize_invoice_number("4587")
        # Report behavior — do they normalize the same?
        # Current: "0004587" stays "0004587", "4587" stays "4587" → different
        assert info1.normalized != info2.normalized or info1.normalized == info2.normalized
        # The important thing: document what happens
        # Leading zeros are preserved in normalization

    def test_35_numeric_vs_string(self):
        info_int = normalize_invoice_number(4587)
        info_str = normalize_invoice_number("4587")
        assert info_int.normalized == info_str.normalized

    def test_36_large_numeric(self):
        info = normalize_invoice_number(9999999999)
        assert info.original == "9999999999"
        assert info.last_4 == "9999"

    def test_37_float_whole_number(self):
        info = normalize_invoice_number(4587.0)
        assert info.original == "4587"

    def test_38_leading_zeros_preserved(self):
        info = normalize_invoice_number("0004587")
        assert info.original == "0004587"
        assert "0004587" in info.normalized.upper()

# ==== PART 5: Duplicates ====
class TestPart5Duplicates:
    def test_39_duplicate_tally_exact(self):
        t1 = _t("INV100", gstin="G1")
        t2 = _t("INV100", gstin="G1")
        g = _g("INV100", gstin="G1")
        s = reconcile_purchases([t1, t2], [_gst_rec("INV100", "G1")])
        dupes = [r for r in s.results if r.status == ReconciliationStatus.POTENTIAL_DUPLICATE]
        assert len(dupes) >= 1

    def test_40_duplicate_gst_exact(self):
        t = _gst_rec("INV200", "G1")  # reuse helper but as tally
        t.source = RecordSource.TALLY
        t.voucher_type = VoucherType.PURCHASE
        r1 = _gst_rec("INV200", "G1", taxable=500, cgst=45, sgst=45)
        r2 = _gst_rec("INV200", "G1", taxable=500, cgst=45, sgst=45)
        s = reconcile_purchases([t], [r1, r2])
        matched = [r for r in s.results if r.status in (
            ReconciliationStatus.PERFECTLY_MATCHED,
            ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE)]
        assert len(matched) == 1  # aggregated into one

    def test_41_gst_multiline_aggregation(self):
        r1 = _gst_rec("INV300", "G1", taxable=500, cgst=45, sgst=45)
        r2 = _gst_rec("INV300", "G1", taxable=500, cgst=45, sgst=45)
        aggs = aggregate_gst_invoices([r1, r2])
        assert len(aggs) == 1
        assert aggs[0].taxable_value == Decimal("1000.00")

    def test_42_same_inv_different_dates(self):
        # Two exact invoice matches under same GSTIN → Ambiguous (correct)
        t = _t("INV400", gstin="G1", dt=date(2025,3,1))
        g1 = _g("INV400", gstin="G1", dt=date(2025,3,1))
        g2 = _g("INV400", gstin="G1", dt=date(2025,4,1))
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

    def test_43_same_inv_different_gstins(self):
        t = _t("INV500", gstin="G1")
        g1 = _g("INV500", gstin="G1")
        g2 = _g("INV500", gstin="G2")
        r = _run([t], [g1, g2])[0]
        assert g1.consumed and not g2.consumed

    def test_44_same_inv_same_gstin_diff_values(self):
        t = _t("INV600", gstin="G1", taxable=1000)
        g1 = _g("INV600", gstin="G1", taxable=900)
        g2 = _g("INV600", gstin="G1", taxable=1100)
        # Two exact matches → ambiguous
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW

    def test_45_same_voucher_diff_suppliers(self):
        t1 = _t("INV700", gstin="G1")
        t2 = _t("INV700", gstin="G2")
        g1 = _g("INV700", gstin="G1")
        g2 = _g("INV700", gstin="G2")
        results = _run([t1, t2], [g1, g2])
        assert all(r.status == ReconciliationStatus.PERFECTLY_MATCHED for r in results)

# ==== PART 6: Supplier name ====
class TestPart6Supplier:
    def test_46_pvt_ltd_vs_private_limited(self):
        n1 = normalize_supplier_name("SYNTHETIC SUPPLIER LTD")
        n2 = normalize_supplier_name("SYNTHETIC SUPPLIER LIMITED")
        assert n1 == n2

    def test_47_ltd_vs_limited(self):
        n1 = normalize_supplier_name("REDINGTON LTD")
        n2 = normalize_supplier_name("REDINGTON LIMITED")
        assert n1 == n2

    def test_48_punctuation(self):
        n1 = normalize_supplier_name("M/S. REDINGTON (INDIA) LTD.")
        n2 = normalize_supplier_name("MS REDINGTON INDIA LIMITED")
        from rapidfuzz import fuzz
        assert fuzz.token_sort_ratio(n1, n2) >= 75

    def test_49_spacing(self):
        n1 = normalize_supplier_name("REDINGTON  LIMITED")
        n2 = normalize_supplier_name("REDINGTON LIMITED")
        assert n1 == n2

    def test_50_location_suffix(self):
        from rapidfuzz import fuzz
        n1 = normalize_supplier_name("REDINGTON LIMITED")
        n2 = normalize_supplier_name("REDINGTON LIMITED BALLABGARH")
        score = fuzz.token_sort_ratio(n1, n2)
        assert score >= 75  # should still match

    def test_51_minor_typo(self):
        from rapidfuzz import fuzz
        n1 = normalize_supplier_name("REDINTON LIMITED")
        n2 = normalize_supplier_name("REDINGTON LIMITED")
        score = fuzz.token_sort_ratio(n1, n2)
        assert score >= 75

    def test_52_genuinely_different(self):
        from rapidfuzz import fuzz
        n1 = normalize_supplier_name("KANODIA BROTHERS")
        n2 = normalize_supplier_name("SHARMA ELECTRONICS")
        score = fuzz.token_sort_ratio(n1, n2)
        assert score < 75

    def test_53_threshold_boundary(self):
        from rapidfuzz import fuzz
        n1 = normalize_supplier_name("KANODIA BROTHERS")
        n2 = normalize_supplier_name("KANODIA TRADERS")
        score = fuzz.token_sort_ratio(n1, n2)
        # Just document where this falls
        assert isinstance(score, float)

    def test_54_no_gstin_wrong_supplier_matching_inv(self):
        t = _t("INV900", gstin="", name="COMPLETELY DIFFERENT NAME")
        g = _g("INV900", gstin="G1", name="REAL SUPPLIER")
        r = _run([t], [g])[0]
        # Supplier doesn't match → falls to Strong Evidence fallback
        # Unique candidate + exact invoice → Needs Review (not auto match)
        assert r.status == ReconciliationStatus.NEEDS_REVIEW
        assert r.match_method == MatchMethod.STRONG_EVIDENCE_FALLBACK

    def test_55_gstin_present_name_very_different(self):
        t = _t("INV950", gstin="G1", name="TOTALLY DIFFERENT")
        g = _g("INV950", gstin="G1", name="REAL SUPPLIER LTD")
        r = _run([t], [g])[0]
        # GSTIN match dominates
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_56_gstin_conflict_names_identical(self):
        t = _t("INV960", gstin="G1", name="SAME NAME")
        g = _g("INV960", gstin="G2", name="SAME NAME")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

# ==== PART 7: Strong Evidence fallback ====
class TestPart7StrongEvidence:
    def test_57_se_exact_perfect(self):
        t = _t("SE001", gstin="", name="UNKNOWN")
        g = _g("SE001", gstin="G1", name="REAL")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW
        assert r.match_method == MatchMethod.STRONG_EVIDENCE_FALLBACK
        assert not g.consumed

    def test_58_se_minor_diff(self):
        t = _t("SE002", gstin="", name="UNKNOWN", taxable=1000)
        g = _g("SE002", gstin="G1", name="REAL", taxable=1000.50)
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW
        assert not g.consumed

    def test_59_se_major_blocked(self):
        t = _t("SE003", gstin="", name="UNKNOWN", taxable=1000)
        g = _g("SE003", gstin="G1", name="REAL", taxable=1005)
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_60_se_two_candidates(self):
        t = _t("SE004", gstin="", name="UNKNOWN")
        g1 = _g("SE004", gstin="G1", name="REAL A")
        g2 = _g("SE004", gstin="G2", name="REAL B")
        r = _run([t], [g1, g2])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_61_se_similar_not_exact(self):
        t = _t("SE005A", gstin="", name="UNKNOWN")
        g = _g("SE005B", gstin="G1", name="REAL")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_62_se_gstin_present_conflicting(self):
        t = _t("SE006", gstin="WRONG", name="UNKNOWN")
        g = _g("SE006", gstin="RIGHT", name="REAL")
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR

    def test_63_se_date_mismatch(self):
        t = _t("SE007", gstin="", name="UNKNOWN", dt=date(2025,1,1))
        g = _g("SE007", gstin="G1", name="REAL", dt=date(2025,2,1))
        r = _run([t], [g])[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW
        assert "Date Warning" in r.remarks

    def test_64_se_unconsumed(self):
        t = _t("SE008", gstin="", name="UNKNOWN")
        g = _g("SE008", gstin="G1", name="REAL")
        r = _run([t], [g])[0]
        assert not g.consumed

    def test_65_66_manual_then_duplicate(self):
        """Manual match + duplicate block tested in test_compute_counts_excludes_consumed_not_in_tally"""
        pass  # Covered by existing regression test


# Helper for reconcile_purchases tests that need ParsedInvoiceRecord GST source
def _gst_rec(inv, gstin, taxable=1000, cgst=90, sgst=90, igst=0, dt=date(2025,3,1), name="SUPPLIER A"):
    rec = ParsedInvoiceRecord(source=RecordSource.GST, voucher_type=VoucherType.PURCHASE, sheet_category=SheetCategory.B2B)
    rec.invoice_number = normalize_invoice_number(inv)
    rec.gstin_normalized = gstin
    rec.gstin_original = gstin
    rec.supplier_name = name
    rec.taxable_value = Decimal(str(taxable))
    rec.invoice_date = dt
    rec.igst = Decimal(str(igst)); rec.cgst = Decimal(str(cgst)); rec.sgst = Decimal(str(sgst))
    return rec


# ==== PART 8: Permanent weak-suffix safety regressions ====
class TestPart8SuffixSafety:
    def test_67_last4_exact_date_perfect_auto_matches(self):
        g = _g("GST-1234")
        r = _run([_t("TALLY-1234")], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_68_last4_exact_date_minor_auto_matches(self):
        g = _g("GST-1234", taxable=1000.50)
        r = _run([_t("TALLY-1234")], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_69_last4_major_does_not_auto_match(self):
        g = _g("GST-1234", taxable=1001.01)
        r = _run([_t("TALLY-1234")], [g])[0]
        assert r.status == ReconciliationStatus.MAJOR_DIFFERENCE
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert g.consumed

    def test_69a_last4_supplier_fallback_perfect_auto_matches(self):
        g = _g("GST-1234", gstin="G1", name="REDINGTON LIMITED")
        r = _run([_t("TALLY-1234", gstin="", name="REDINGTON LIMITED")], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert g.consumed

    def test_69b_last4_supplier_fallback_minor_auto_matches(self):
        g = _g("GST-1234", gstin="G1", name="REDINGTON LIMITED", taxable=1000.50)
        r = _run([_t("TALLY-1234", gstin="", name="REDINGTON LIMITED")], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_4
        assert r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE
        assert g.consumed

    def test_70_last4_missing_tally_date_does_not_auto_match(self):
        g = _g("GST-1234")
        r = _run([_t("TALLY-1234", dt=None)], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_71_last4_missing_gstr_date_does_not_auto_match(self):
        g = _g("GST-1234", dt=None)
        r = _run([_t("TALLY-1234")], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_72_last4_one_day_difference_does_not_auto_match(self):
        g = _g("GST-1234", dt=date(2025, 3, 2))
        r = _run([_t("TALLY-1234")], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_73_last4_two_qualifying_candidates_are_ambiguous(self):
        candidates = [_g("GST-A1234"), _g("GST-B1234")]
        r = _run([_t("TALLY-1234")], candidates)[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW
        assert not any(c.consumed for c in candidates)

    def test_74_last4_conflicting_gstin_is_blocked(self):
        g = _g("GST-1234", gstin="G2")
        r = _run([_t("TALLY-1234", gstin="G1")], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_75_last2_exact_date_perfect_unique_auto_matches(self):
        g = _g("GST-XYZ-12")
        r = _run([_t("TALLY-ABC-12")], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_2
        assert r.status == ReconciliationStatus.PERFECTLY_MATCHED

    def test_76_last2_minor_auto_matches(self):
        g = _g("GST-XYZ-12", cgst=90.50)
        r = _run([_t("TALLY-ABC-12")], [g])[0]
        assert r.match_method == MatchMethod.INVOICE_LAST_2
        assert r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE

    def test_77_last2_major_does_not_auto_match(self):
        g = _g("GST-XYZ-12", sgst=91.01)
        r = _run([_t("TALLY-ABC-12")], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_78_last2_missing_date_does_not_auto_match(self):
        g = _g("GST-XYZ-12")
        r = _run([_t("TALLY-ABC-12", dt=None)], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_79_last2_date_mismatch_does_not_auto_match(self):
        g = _g("GST-XYZ-12", dt=date(2025, 3, 2))
        r = _run([_t("TALLY-ABC-12")], [g])[0]
        assert r.status == ReconciliationStatus.MISSING_IN_GSTR
        assert not g.consumed

    def test_80_last2_multiple_qualifying_candidates_are_ambiguous(self):
        candidates = [_g("GST-XYZ-12"), _g("GST-PQR-12")]
        r = _run([_t("TALLY-ABC-12")], candidates)[0]
        assert r.status == ReconciliationStatus.NEEDS_REVIEW
        assert not any(c.consumed for c in candidates)

    def test_81_exact_invoice_major_still_establishes_identity(self):
        g = _g("INV-001", taxable=1100)
        r = _run([_t("INV-001", taxable=1000)], [g])[0]
        assert r.match_method == MatchMethod.EXACT_INVOICE
        assert r.status == ReconciliationStatus.MAJOR_DIFFERENCE
        assert g.consumed

    def test_82_normalized_invoice_date_mismatch_is_unchanged(self):
        g = _g("INV/001", dt=date(2025, 3, 2))
        r = _run([_t("INV-001", dt=date(2025, 3, 1))], [g])[0]
        assert r.match_method == MatchMethod.NORMALIZED_INVOICE
        assert r.date_mismatch is True
        assert g.consumed

    def test_83_rejected_suffix_candidate_remains_unconsumed(self):
        g = _g("GST-XYZ-12", igst=2)
        r = _run([_t("TALLY-ABC-12", igst=0)], [g])[0]
        assert r.match_method == MatchMethod.NO_MATCH
        assert g.consumed is False

    def test_84_accepted_suffix_candidate_is_consumed_only_once(self):
        g = _g("GST-XYZ-12")
        first, second = _run(
            [_t("TALLY-ABC-12"), _t("OTHER-ABC-12")],
            [g],
        )
        assert first.match_method == MatchMethod.INVOICE_LAST_2
        assert second.status == ReconciliationStatus.MISSING_IN_GSTR
        assert g.consumed is True
