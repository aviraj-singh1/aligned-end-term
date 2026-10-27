import json
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from backend.app.models.records import RecordSource, SheetCategory, VoucherType
from backend.app.models.reconciliation import ReconciliationStatus
from backend.app.services.excel_exporter import export_to_excel
from backend.app.services.file_parser import _row_to_record, parse_file, reparse_persisted_file
from backend.app.services.normalizer import parse_rate, parse_money
from backend.app.services.session_store import encode_session_value, decode_session_value
from backend.app.models.reconciliation import MatchMethod
from backend.app.services.json_exporter import generate_json_report
from backend.app.services.notes_reconciliation_engine import reconcile_notes
from backend.app.services.reconciliation_engine import reconcile_purchases


GSTIN = "29ABCDE1234F1Z5"
HEADERS = ["GSTIN", "Invoice", "Date", "Taxable", "IGST", "CGST", "SGST", "Total"]
MAP = {"gstin": 0, "invoice_number": 1, "invoice_date": 2, "taxable_value": 3,
       "igst": 4, "cgst": 5, "sgst": 6, "total_invoice_value": 7}


def parsed(source, number="INV-1", taxable=100, igst="", cgst=9, sgst=9,
           total=118, *, category=None, raw_extra=None, note=False):
    category = category or (SheetCategory.PURCHASE_REGISTER if source == RecordSource.TALLY else SheetCategory.B2B)
    row = [GSTIN, number, date(2026, 4, 1), taxable, igst, cgst, sgst, total]
    rec = _row_to_record(row, HEADERS, MAP, category, category.value, 2)
    rec.source = source
    rec.voucher_type = (
        VoucherType.CREDIT_NOTE if note and source == RecordSource.GST
        else VoucherType.DEBIT_NOTE if note
        else VoucherType.PURCHASE
    )
    if note:
        rec.note_type = "Credit Note" if source == RecordSource.GST else "Debit Note"
        rec.note_number = number
        rec.note_date = date(2026, 4, 1)
    if raw_extra:
        rec.raw_data.update(raw_extra)
    return rec


def test_blank_taxable_and_explicit_zero_keep_frozen_semantics():
    blank = parsed(RecordSource.TALLY, taxable="   ")
    zero = parsed(RecordSource.TALLY, number="INV-0", taxable="₹0")
    assert not blank.taxable_value_present
    assert blank.financial_parse_issues == []
    assert reconcile_purchases([blank], []).results[0].status == ReconciliationStatus.TAXABLE_VALUE_MISSING
    assert zero.taxable_value_present and zero.taxable_value == Decimal("0.00")
    assert zero.financial_parse_issues == []


def test_blank_optional_tax_heads_remain_valid_zero_for_both_tax_patterns():
    interstate = parsed(RecordSource.TALLY, igst=18, cgst="", sgst="")
    intrastate = parsed(RecordSource.TALLY, number="INV-2", igst="", cgst=9, sgst=9)
    assert (interstate.cgst, interstate.sgst) == (Decimal("0.00"), Decimal("0.00"))
    assert intrastate.igst == Decimal("0.00")
    assert interstate.financial_parse_issues == intrastate.financial_parse_issues == []


def test_malformed_accounts_taxable_is_review_not_zero_classification_and_does_not_consume():
    tally = parsed(RecordSource.TALLY, taxable="abc", cgst=0, sgst=0, total=0)
    gst = parsed(RecordSource.GST, taxable=0, cgst=0, sgst=0, total=0)
    summary = reconcile_purchases([tally], [gst])
    result = summary.results[0]
    assert result.status == ReconciliationStatus.NEEDS_REVIEW
    assert "taxable_value" in result.remarks and "'abc'" in result.remarks
    assert summary.not_in_tally == 1
    assert not summary.not_in_tally_records[0].consumed


def test_malformed_accounts_tax_head_cannot_produce_perfect_minor_or_major():
    tally = parsed(RecordSource.TALLY, cgst="9OO")
    gst = parsed(RecordSource.GST)
    summary = reconcile_purchases([tally], [gst])
    assert summary.results[0].status == ReconciliationStatus.NEEDS_REVIEW
    assert "cgst" in summary.results[0].remarks and "9OO" in summary.results[0].remarks
    assert summary.not_in_tally == 1


def test_malformed_gstr_taxable_and_tax_head_are_unmatchable_and_unconsumed():
    for field, kwargs in (("taxable_value", {"taxable": "10O0"}), ("igst", {"igst": "₹12,3x0"})):
        tally = parsed(RecordSource.TALLY)
        gst = parsed(RecordSource.GST, **kwargs)
        summary = reconcile_purchases([tally], [gst])
        assert summary.results[0].status == ReconciliationStatus.NEEDS_REVIEW
        assert field in summary.results[0].remarks
        assert summary.not_in_tally == 1 and not summary.not_in_tally_records[0].consumed


def test_cancelled_accounts_precedence_remains_higher_than_invalid_money():
    tally = parsed(RecordSource.TALLY, taxable="abc")
    tally.raw_data["Remarks"] = "Cancelled"
    summary = reconcile_purchases([tally], [])
    assert summary.cancelled_accounts_vouchers == 1
    assert summary.needs_review == 0


def test_notes_receive_equivalent_invalid_money_protection():
    tally = parsed(RecordSource.TALLY, number="CN-1", cgst="nine hundred", note=True,
                   category=SheetCategory.DEBIT_NOTE_REGISTER)
    gst = parsed(RecordSource.GST, number="CN-1", note=True, category=SheetCategory.CDNR)
    summary = reconcile_notes([tally], [gst])
    assert summary.results[0].status == ReconciliationStatus.NEEDS_REVIEW
    assert summary.not_in_tally == 1
    assert not summary.not_in_tally_notes[0].consumed


def test_gstr_note_invalid_money_is_reviewable_and_unconsumed():
    tally = parsed(RecordSource.TALLY, number="CN-1", note=True,
                   category=SheetCategory.DEBIT_NOTE_REGISTER)
    gst = parsed(RecordSource.GST, number="CN-1", sgst="bad", note=True,
                 category=SheetCategory.CDNR)
    summary = reconcile_notes([tally], [gst])
    assert summary.results[0].status == ReconciliationStatus.NEEDS_REVIEW
    assert "sgst" in summary.results[0].remarks
    assert summary.not_in_tally == 1


def test_json_and_excel_preserve_invalid_raw_value_without_fabricated_zero(tmp_path):
    tally = parsed(RecordSource.TALLY, cgst="9OO")
    gst = parsed(RecordSource.GST)
    purchases = reconcile_purchases([tally], [gst])
    notes = reconcile_notes([], [])

    report = json.loads(generate_json_report(purchases, notes, "Apr-26"))
    row = report["reconciliation_summary"][0]
    assert row["tally_cgst"] is None
    assert row["financial_parse_issues"][0]["raw_value"] == "9OO"

    target = tmp_path / "report.xlsx"
    export_to_excel(str(target), purchases, notes, "Apr-26")
    wb = openpyxl.load_workbook(target, data_only=False)
    values = list(wb["Reconciliation Summary"].values)
    assert values[1][8] == "Invalid: 9OO"
    assert "9OO" in values[1][13]


def test_accounts_and_gstr_accounting_invariants_with_invalid_values():
    tally = parsed(RecordSource.TALLY, cgst="bad")
    gst = parsed(RecordSource.GST)
    summary = reconcile_purchases([tally], [gst])
    accounted_accounts = (
        summary.perfectly_matched + summary.minor_differences + summary.major_differences
        + summary.invoice_mismatches + summary.taxable_value_missing + summary.missing_in_gstr
        + summary.needs_review + summary.potential_duplicates + summary.cancelled_accounts_vouchers
    )
    assert accounted_accounts == summary.total_tally_invoices == 1
    assert summary.not_in_tally + summary.cancelled_gstr_vouchers == summary.total_gst_invoices == 1


@pytest.mark.parametrize("raw,expected", [
    ("100%", "100"), ("18%", "18"), ("5%", "5"), ("18", "18"),
    ("5", "5"), (18, "18"), (5, "5"), ("12.0%", "12.0"),
    (" 18.125 % ", "18.125"), (Decimal("0.125"), "0.125"),
])
def test_rate_metadata_is_decimal_percentage_points_not_money(raw, expected):
    assert parse_rate(raw) == Decimal(expected)
    record = _row_to_record(
        [GSTIN, "INV-1", date(2026, 4, 1), 100, 0, 9, 9, 118, raw],
        HEADERS + ["Applicable % of Tax Rate"], MAP | {"gst_rate": 8},
        SheetCategory.B2B, "B2B", 2,
    )
    assert record.gst_rate == Decimal(expected)
    assert record.financial_parse_issues == []


@pytest.mark.parametrize("raw", [None, "", "  ", "-"])
def test_blank_rate_metadata_is_absent(raw):
    assert parse_rate(raw) is None


@pytest.mark.parametrize("raw", ["abc", "18%%", "NaN", "Infinity", float("nan"), True])
def test_malformed_rate_is_not_silently_accepted(raw):
    with pytest.raises(ValueError):
        parse_rate(raw)
    record = _row_to_record(
        [GSTIN, "INV-1", date(2026, 4, 1), 100, 0, 9, 9, 118, raw],
        HEADERS + ["Rate"], MAP | {"gst_rate": 8}, SheetCategory.B2B, "B2B", 2,
    )
    assert record.gst_rate is None
    assert [issue.field_name for issue in record.financial_parse_issues] == ["gst_rate"]


@pytest.mark.parametrize("kind", ["xlsx", "json"])
@pytest.mark.parametrize("number,gst_number,method", [
    ("150554", "T06/27/150554", MatchMethod.INVOICE_LAST_4),
    ("170549", "T49/27/107549", MatchMethod.INVOICE_LAST_2),
])
@pytest.mark.parametrize("bad_field", [None, "Taxable Value", "IGST", "CGST", "SGST"])
def test_percentage_metadata_preserves_suffix_rules_and_money_guards(
    tmp_path, kind, number, gst_number, method, bad_field,
):
    from backend.tests.test_mapping_persistence import workbook_bytes
    row = {"GSTIN": GSTIN, "Invoice No.": gst_number, "Invoice Date": "01-04-2026",
           "Taxable Value": 100, "IGST": 0, "CGST": 9, "SGST": 9,
           "Applicable % of Tax Rate": "100%"}
    if bad_field:
        row[bad_field] = "not available"
    path = tmp_path / f"gstr.{kind}"
    if kind == "xlsx":
        path.write_bytes(workbook_bytes({"B2B": [list(row), list(row.values())]}))
    else:
        path.write_text(json.dumps({"b2b": [row]}), encoding="utf-8")
    inspected = parse_file(str(path), RecordSource.GST)
    assert not inspected.errors
    assert inspected.records[0].gst_rate == 100
    # Exercise the durable inspect -> mapping -> reconcile representation too.
    inspected.records = []
    restored = decode_session_value(encode_session_value(inspected))
    path.unlink()
    records = reparse_persisted_file(restored, RecordSource.GST).records
    tally = parsed(RecordSource.TALLY, number=number)
    summary = reconcile_purchases([tally], records)
    result = next(r for r in summary.results if r.audit.tally_rows)
    if bad_field:
        semantic = {"Taxable Value": "taxable_value", "IGST": "igst", "CGST": "cgst", "SGST": "sgst"}[bad_field]
        assert [issue.field_name for issue in records[0].financial_parse_issues] == [semantic]
        assert result.status not in (ReconciliationStatus.PERFECTLY_MATCHED,
                                     ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE,
                                     ReconciliationStatus.MAJOR_DIFFERENCE)
        assert summary.not_in_tally == 1
        assert not summary.not_in_tally_records[0].consumed
    else:
        assert records[0].financial_parse_issues == []
        assert result.match_method == method
        assert result.status == ReconciliationStatus.PERFECTLY_MATCHED
        assert summary.not_in_tally == 0


@pytest.mark.parametrize("raw", ["100%", "18%", "abc", "not available"])
def test_money_parser_still_rejects_percentages_and_malformed_text(raw):
    with pytest.raises(ValueError):
        parse_money(raw)
