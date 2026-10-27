import json
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from backend.app.models.records import (
    InvoiceNumberInfo, ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType,
)
from backend.app.models.reconciliation import AccountingSourceSystem, CancelledSourceSide
from backend.app.services.cancellation_detector import (
    build_cancelled_voucher, cancellation_evidence, detect_accounts_source_system,
)
from backend.app.services.excel_exporter import export_to_excel
from backend.app.services.json_exporter import generate_json_report
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary
from backend.app.services.reconciliation_engine import reconcile_purchases
from backend.app.services.session_store import decode_session_value, encode_session_value


def record(source=RecordSource.TALLY, invoice="A1", raw=None, category=None, taxable="100"):
    return ParsedInvoiceRecord(
        source=source, source_sheet="Sheet1", source_row=6,
        sheet_category=category or (SheetCategory.PURCHASE_REGISTER if source == RecordSource.TALLY else SheetCategory.B2B),
        supplier_name="Supplier", gstin_original="06ABCDE1234F1Z5",
        gstin_normalized="06ABCDE1234F1Z5", gstin_valid=True,
        invoice_number=InvoiceNumberInfo(original=invoice, normalized=invoice),
        invoice_date=date(2025, 4, 1), voucher_type=VoucherType.PURCHASE,
        taxable_value=Decimal(taxable), cgst=Decimal("9"), sgst=Decimal("9"),
        raw_data=raw or {},
    )


BUSY_HEADERS = {
    "Account": "Supplier", "Vch/Bill No": "A1", "TIN/GSTIN No.": "06ABCDE1234F1Z5",
    "Taxable Amt.": 100, "Notes": "",
}
TALLY_HEADERS = {
    "Particulars": "Supplier", "Voucher No.": "A1", "GSTIN/UIN": "06ABCDE1234F1Z5",
    "Value": 100,
}


@pytest.mark.parametrize("marker", ["CANCELLED", "(CANCELLED)", "C A N C E L L E D", " ( c a n c e l l e d ) "])
def test_busy_explicit_markers_are_cancelled(marker):
    row = record(raw={**BUSY_HEADERS, "Account": f"{marker} - Supplier"})
    voucher = build_cancelled_voucher(row)
    assert voucher.status == "Cancelled BUSY Voucher"
    assert voucher.source_side == CancelledSourceSide.ACCOUNTS
    assert marker.strip() in voucher.cancellation_evidence


def test_normal_and_zero_value_rows_are_not_inferred_cancelled():
    assert build_cancelled_voucher(record(raw=BUSY_HEADERS)) is None
    assert build_cancelled_voucher(record(raw=BUSY_HEADERS, taxable="0")) is None


def test_source_system_statuses_are_structural():
    tally = build_cancelled_voucher(record(raw={**TALLY_HEADERS, "Particulars": "Cancelled Supplier"}))
    generic = build_cancelled_voucher(record(raw={"Supplier": "Cancelled Supplier"}))
    assert tally.status == "Cancelled Tally Voucher"
    assert tally.source_system == AccountingSourceSystem.TALLY
    assert generic.status == "Cancelled Accounts Voucher"


def test_cancelled_accounts_bypass_matching_and_missing():
    cancelled = record(raw={**BUSY_HEADERS, "Account": "(C A N C E L L E D) - Supplier"})
    matching_gst = record(source=RecordSource.GST, raw={}, category=SheetCategory.B2B)
    summary = reconcile_purchases([cancelled], [matching_gst])
    assert summary.total_tally_invoices == 1
    assert summary.active_tally_invoices == 0
    assert summary.cancelled_accounts_vouchers == 1
    assert summary.missing_in_gstr == summary.needs_review == 0
    assert not any(result.audit.tally_rows for result in summary.results)
    assert summary.not_in_tally == 1


@pytest.mark.parametrize("category", [SheetCategory.B2B, SheetCategory.B2BA])
def test_explicit_gstr_cancellation_is_separate_and_not_a_candidate(category):
    gst = record(
        source=RecordSource.GST, category=category,
        raw={"Cancellation Date": "17-Apr-26", "Reverse Charge": "N"},
    )
    tally = record(raw=BUSY_HEADERS)
    summary = reconcile_purchases([tally], [gst])
    assert summary.cancelled_gstr_vouchers == 1
    assert summary.active_gst_invoices == 0
    assert summary.not_in_tally == 0
    assert summary.missing_in_gstr == 1
    assert summary.cancelled_vouchers[0].status == "Cancelled GSTR Voucher"


def test_red_reverse_charge_metadata_is_not_cancellation_evidence():
    row = record(source=RecordSource.GST, raw={
        "Reverse Charge": "Y", "ITC InEligibility Reason": "Reverse Charge is YES.",
        "_fill_rgb": "FFF08080",
    })
    assert cancellation_evidence(row) is None


def test_cancelled_summary_round_trips_session_serialization():
    summary = reconcile_purchases(
        [record(raw={**BUSY_HEADERS, "Account": "Cancelled Supplier"})], []
    )
    restored = decode_session_value(encode_session_value(summary))
    assert restored.cancelled_accounts_vouchers == 1
    assert restored.cancelled_vouchers[0].status == "Cancelled BUSY Voucher"


def test_json_export_is_additive_for_cancelled_vouchers():
    summary = reconcile_purchases(
        [record(raw={**BUSY_HEADERS, "Account": "Cancelled Supplier"})], []
    )
    report = json.loads(generate_json_report(summary, NotesReconciliationSummary(), "Test"))
    assert report["counts"]["cancelled_vouchers"] == 1
    assert report["counts"]["cancelled_accounts_vouchers"] == 1
    assert report["counts"]["cancelled_gstr_vouchers"] == 0
    assert report["cancelled_vouchers"][0]["status"] == "Cancelled BUSY Voucher"
    empty = json.loads(generate_json_report(
        reconcile_purchases([], []), NotesReconciliationSummary(), "Test"
    ))
    assert empty["counts"]["cancelled_vouchers"] == 0
    assert empty["cancelled_vouchers"] == []


def test_excel_export_has_cancelled_sheet_in_required_order(tmp_path):
    summary = reconcile_purchases(
        [record(raw={**BUSY_HEADERS, "Account": "Cancelled Supplier"})], []
    )
    path = tmp_path / "report.xlsx"
    export_to_excel(str(path), summary, NotesReconciliationSummary(), "Test")
    workbook = openpyxl.load_workbook(path, data_only=False)
    assert workbook.sheetnames == [
        "Reconciliation Summary", "Missing & Differences", "Taxable Value Missing",
        "Date Mismatches", "Invoice Mismatches", "Cancelled Vouchers", "Invoice No Remarks",
        "Not in Tally", "Notes",
    ]
    assert workbook["Cancelled Vouchers"]["L2"].value == "Cancelled BUSY Voucher"


def test_cancelled_population_invariants_and_no_duplicate_consumption():
    active_tally = record(invoice="ACTIVE", raw={**BUSY_HEADERS, "Vch/Bill No": "ACTIVE"})
    cancelled_tally = record(invoice="CANCEL", raw={**BUSY_HEADERS, "Account": "Cancelled Supplier"})
    active_gst = record(source=RecordSource.GST, invoice="ACTIVE", category=SheetCategory.B2B)
    cancelled_gst = record(source=RecordSource.GST, invoice="CANCEL-G", category=SheetCategory.B2B, raw={"Cancellation Date": "1-May-26"})
    summary = reconcile_purchases([active_tally, cancelled_tally], [active_gst, cancelled_gst])
    primary = (summary.perfectly_matched + summary.minor_differences + summary.major_differences
               + summary.invoice_mismatches + summary.missing_in_gstr + summary.needs_review
               + summary.cancelled_accounts_vouchers)
    assert primary == summary.total_tally_invoices
    assert summary.active_gst_invoices - summary.not_in_tally + summary.not_in_tally + summary.cancelled_gstr_vouchers == summary.total_gst_invoices
    consumed_rows = [
        row.source_row for result in summary.results
        for row in result.audit.gst_rows
    ]
    assert len(consumed_rows) == len(set(consumed_rows))
