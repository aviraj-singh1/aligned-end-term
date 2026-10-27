from datetime import date
from decimal import Decimal
import json

import pytest

from backend.app.models.records import ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType
from backend.app.services.amendment_resolver import resolve_amendments
from backend.app.services.file_parser import _apply_amendment_column_map, _row_to_record
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.notes_reconciliation_engine import aggregate_gst_credit_notes, reconcile_notes
from backend.app.services.reconciliation_engine import aggregate_gst_invoices, reconcile_purchases
from backend.app.services.json_exporter import generate_json_report


GSTIN = "29ABCDE1234F1Z5"


def record(category, number, when, taxable="100", *, original="", original_date=None, note=False, row=2):
    rec = ParsedInvoiceRecord(
        source=RecordSource.GST,
        source_sheet=category.value,
        source_row=row,
        sheet_category=category,
        gstin_original=GSTIN,
        gstin_normalized=GSTIN,
        gstin_valid=True,
        invoice_number=normalize_invoice_number(number),
        invoice_date=when,
        taxable_value=Decimal(taxable),
        cgst=Decimal("9"),
        sgst=Decimal("9"),
        amendment_original_number=original,
        amendment_original_date=original_date,
    )
    if note:
        rec.note_number = number
        rec.note_date = when
        rec.voucher_type = VoucherType.CREDIT_NOTE
        rec.note_type = "C"
    return rec


def tally(number, when, taxable="100", *, debit=False):
    rec = record(SheetCategory.PURCHASE_REGISTER, number, when, taxable)
    rec.source = RecordSource.TALLY
    rec.voucher_type = VoucherType.DEBIT_NOTE if debit else VoucherType.PURCHASE
    return rec


def test_b2ba_parser_maps_original_and_revised_portal_fields():
    headers = [
        "GSTIN/UIN", "Original Invoice No.", "Original Invoice Date",
        "Revised Invoice No.", "Revised Invoice Date", "Taxable Value",
        "IGST Amount", "CGST Amount", "SGST Amount", "Total Invoice Value",
        "Place Of Supply", "Amendment Period",
    ]
    col_map = {"gstin": 0, "taxable_value": 5, "igst": 6, "cgst": 7,
               "sgst": 8, "total_invoice_value": 9, "place_of_supply": 10}
    _apply_amendment_column_map(headers, col_map, SheetCategory.B2BA)
    rec = _row_to_record(
        [GSTIN, "INV-100", date(2026, 4, 10), "INV-100A", date(2026, 4, 11),
         12000, 2160, 0, 0, 14160, "Karnataka", "May-26"],
        headers, col_map, SheetCategory.B2BA, "b2ba", 2,
    )
    assert rec.amendment_original_number == "INV-100"
    assert rec.amendment_original_date == date(2026, 4, 10)
    assert rec.invoice_number.original == "INV-100A"
    assert rec.invoice_date == date(2026, 4, 11)
    assert (rec.taxable_value, rec.igst, rec.total_invoice_value) == (
        Decimal("12000.00"), Decimal("2160.00"), Decimal("14160.00"))
    assert rec.place_of_supply == "Karnataka"
    assert rec.amendment_period == "May-26"


def test_cdnra_parser_maps_original_and_revised_portal_fields():
    headers = ["GSTIN/UIN", "Original Note No", "Original Note Date",
               "Revised Note Number", "Revised Note Date", "Note Type", "Taxable Value"]
    col_map = {"gstin": 0, "note_type": 5, "taxable_value": 6}
    _apply_amendment_column_map(headers, col_map, SheetCategory.CDNRA)
    rec = _row_to_record(
        [GSTIN, "CN-1", date(2026, 4, 1), "CN-1A", date(2026, 4, 2), "C", 120],
        headers, col_map, SheetCategory.CDNRA, "cdnra", 2,
    )
    assert rec.amendment_original_number == "CN-1"
    assert rec.amendment_original_date == date(2026, 4, 1)
    assert rec.note_number == "CN-1A"
    assert rec.note_date == date(2026, 4, 2)
    assert rec.invoice_number.original == "CN-1A"


def test_b2ba_replaces_original_and_revised_values_reconcile_once():
    old = record(SheetCategory.B2B, "INV-100", date(2026, 4, 10), "10000")
    amended = record(SheetCategory.B2BA, "INV-100A", date(2026, 4, 11), "12000",
                     original="INV-100", original_date=date(2026, 4, 10))
    authoritative, stats = resolve_amendments(
        [old, amended], SheetCategory.B2B, SheetCategory.B2BA)
    assert authoritative == [amended]
    assert stats.superseded_regular_documents == 1
    aggregated = aggregate_gst_invoices(authoritative)
    assert len(aggregated) == 1
    assert aggregated[0].invoice_number_original == "INV-100A"
    assert aggregated[0].taxable_value == Decimal("12000")
    summary = reconcile_purchases([tally("INV-100A", date(2026, 4, 11), "12000")], authoritative)
    assert summary.total_gst_invoices == 1
    assert summary.not_in_tally == 0


def test_orphan_b2ba_is_authoritative_and_unrelated_b2b_remains():
    regular = record(SheetCategory.B2B, "OTHER", date(2026, 4, 1))
    orphan = record(SheetCategory.B2BA, "NEW", date(2026, 5, 2), original="OLD",
                    original_date=date(2026, 3, 2))
    authoritative, stats = resolve_amendments(
        [regular, orphan], SheetCategory.B2B, SheetCategory.B2BA)
    assert authoritative == [regular, orphan]
    assert stats.superseded_regular_documents == 0


def test_multiline_b2ba_aggregates_without_original_rows():
    old = record(SheetCategory.B2B, "OLD", date(2026, 4, 1), "100")
    lines = [
        record(SheetCategory.B2BA, "NEW", date(2026, 4, 2), value,
               original="OLD", original_date=date(2026, 4, 1), row=row)
        for row, value in ((2, "60"), (3, "40"))
    ]
    authoritative, _ = resolve_amendments(
        [old, *lines], SheetCategory.B2B, SheetCategory.B2BA)
    aggregated = aggregate_gst_invoices(authoritative)
    assert len(aggregated) == 1
    assert aggregated[0].taxable_value == Decimal("100")
    assert aggregated[0].source_rows == lines


def test_multiple_distinct_revisions_are_rejected_without_row_order_guess():
    rows = [
        record(SheetCategory.B2BA, revised, date(2026, 4, day), original="OLD",
               original_date=date(2026, 3, 1), row=day)
        for revised, day in (("REV-1", 2), ("REV-2", 3))
    ]
    with pytest.raises(ValueError, match="no deterministic latest-version evidence"):
        resolve_amendments(rows, SheetCategory.B2B, SheetCategory.B2BA)


def test_incomplete_amendment_linkage_is_rejected():
    incomplete = record(SheetCategory.B2BA, "NEW", date(2026, 4, 2), original="OLD")
    with pytest.raises(ValueError, match="lacks a complete"):
        resolve_amendments([incomplete], SheetCategory.B2B, SheetCategory.B2BA)


def test_cdnra_replaces_cdnr_and_is_export_candidate_once():
    old = record(SheetCategory.CDNR, "CN-1", date(2026, 4, 1), note=True)
    amended = record(SheetCategory.CDNRA, "CN-1A", date(2026, 4, 2), "120",
                     original="CN-1", original_date=date(2026, 4, 1), note=True)
    authoritative, stats = resolve_amendments(
        [old, amended], SheetCategory.CDNR, SheetCategory.CDNRA)
    aggregated = aggregate_gst_credit_notes(authoritative)
    assert authoritative == [amended]
    assert stats.superseded_regular_documents == 1
    assert len(aggregated) == 1
    assert aggregated[0].note_number_original == "CN-1A"
    assert aggregated[0].source_rows == [amended]
    summary = reconcile_notes([tally("CN-1A", date(2026, 4, 2), "120", debit=True)], authoritative)
    assert summary.total_gst_credit_notes == 1
    assert summary.not_in_tally == 0


def test_orphan_cdnra_remains_authoritative():
    orphan = record(SheetCategory.CDNRA, "CN-2A", date(2026, 4, 2),
                    original="CN-2", original_date=date(2026, 4, 1), note=True)
    authoritative, _ = resolve_amendments(
        [orphan], SheetCategory.CDNR, SheetCategory.CDNRA)
    assert authoritative == [orphan]


def test_candidate_consumption_and_json_export_contain_amendment_once():
    amended = record(SheetCategory.B2BA, "INV-A", date(2026, 4, 2), original="INV",
                     original_date=date(2026, 4, 1))
    purchase_summary = reconcile_purchases(
        [tally("INV-A", date(2026, 4, 2)), tally("INV-A", date(2026, 4, 2))],
        [amended],
    )
    assert purchase_summary.total_gst_invoices == 1
    assert sum(bool(result.audit.gst_rows) for result in purchase_summary.results) == 1

    amended_note = record(SheetCategory.CDNRA, "CN-A", date(2026, 4, 2), original="CN",
                          original_date=date(2026, 4, 1), note=True)
    notes_summary = reconcile_notes(
        [tally("CN-A", date(2026, 4, 2), debit=True)], [amended_note])
    report = json.loads(generate_json_report(purchase_summary, notes_summary, "Apr-26"))
    purchase_rows = [row for row in report["reconciliation_summary"]
                     if row["gstr_invoice_number"] == "INV-A"]
    note_rows = [row for row in report["notes"] if row["invoice_number_gst"] == "CN-A"]
    assert len(purchase_rows) == 1
    assert len(note_rows) == 1
    assert purchase_rows[0]["gstr_source_category"] == "b2ba"
    assert note_rows[0]["gstr_source_category"] == "cdnra"


@pytest.mark.parametrize("regular,amended", [
    (SheetCategory.B2B, SheetCategory.B2BA),
    (SheetCategory.CDNR, SheetCategory.CDNRA),
])
def test_regular_only_input_is_unchanged(regular, amended):
    row = record(regular, "DOC-1", date(2026, 4, 1), note=regular == SheetCategory.CDNR)
    authoritative, stats = resolve_amendments([row], regular, amended)
    assert authoritative == [row]
    assert stats.raw_amendment_rows == 0
