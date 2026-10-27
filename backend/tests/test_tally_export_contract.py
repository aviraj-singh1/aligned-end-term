from datetime import date
from decimal import Decimal

from backend.app.models.records import (
    FinancialParseIssue,
    ParsedInvoiceRecord,
    RecordSource,
    SheetCategory,
    VoucherType,
)
from backend.app.models.tally_export import (
    ReverseChargeStatus,
    TallyExportTransactionType,
    TallyTaxStructure,
)
from backend.app.services.file_parser import _row_to_record
from backend.app.services.notes_reconciliation_engine import aggregate_gst_credit_notes
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.reconciliation_engine import aggregate_gst_invoices
from backend.app.services.session_store import decode_session_value, encode_session_value
from backend.app.services.tally_export_contract import (
    purchase_candidate,
    supplier_credit_note_candidate,
)


D = Decimal
GSTIN = "06DEMOX1234A1Z5"


def _gst_row(
    *,
    number: str = "INV-1",
    row: int = 2,
    category: SheetCategory = SheetCategory.B2B,
    taxable: str = "100",
    igst: str = "0",
    cgst: str = "9",
    sgst: str = "9",
    cess: str = "0",
    rate: str | None = "18",
    place: str = "Haryana",
    reverse_charge: str = "No",
) -> ParsedInvoiceRecord:
    return ParsedInvoiceRecord(
        source=RecordSource.GST,
        source_sheet=category.value,
        source_row=row,
        sheet_category=category,
        supplier_name="Supplier Pvt Ltd",
        gstin_original=GSTIN,
        gstin_normalized=GSTIN,
        gstin_valid=True,
        invoice_number=normalize_invoice_number(number),
        invoice_date=date(2026, 4, 1),
        voucher_type=VoucherType.PURCHASE,
        taxable_value=D(taxable),
        igst=D(igst),
        cgst=D(cgst),
        sgst=D(sgst),
        cess=D(cess),
        cess_present=True,
        gst_rate=D(rate) if rate is not None else None,
        place_of_supply=place,
        reverse_charge=reverse_charge,
    )


def test_parser_preserves_cess_and_note_value_as_canonical_money():
    headers = [
        "GSTIN", "Note/Refund Number", "Note/Refund Date", "Note Type",
        "Taxable Value", "IGST", "CGST", "SGST", "Cess Amount",
        "Note/Refund Value",
    ]
    mapping = {
        "gstin": 0, "note_number": 1, "note_date": 2, "note_type": 3,
        "taxable_value": 4, "igst": 5, "cgst": 6, "sgst": 7,
        "cess": 8, "note_value": 9,
    }
    record = _row_to_record(
        [GSTIN, "CN-1", date(2026, 4, 2), "Credit Note", 100, 0, 9, 9, 2, 120],
        headers,
        mapping,
        SheetCategory.CDNR,
        "CDNR",
        2,
    )

    assert record is not None
    assert record.cess == D("2.00")
    assert record.cess_present is True
    assert record.total_invoice_value == D("120.00")
    assert record.total_invoice_value_present is True
    assert record.financial_parse_issues == []


def test_invalid_cess_is_preserved_as_a_financial_parse_issue():
    record = _row_to_record(
        [GSTIN, "INV-1", "not-a-number"],
        ["GSTIN", "Invoice No.", "Cess"],
        {"gstin": 0, "invoice_number": 1, "cess": 2},
        SheetCategory.B2B,
        "B2B",
        2,
    )

    assert record is not None
    assert record.cess_present is True
    assert record.cess == D("0.00")
    assert [issue.field_name for issue in record.financial_parse_issues] == ["cess"]


def test_purchase_candidate_exposes_validation_ready_source_state():
    first = _gst_row(rate="5", cgst="2.5", sgst="2.5", cess="1")
    second = _gst_row(
        row=3, rate="12", taxable="200", igst="24", cgst="0", sgst="0",
        cess="2", place="", reverse_charge="Yes",
    )
    first.total_invoice_value = D("329.00")
    first.total_invoice_value_present = True
    first.financial_parse_issues.append(FinancialParseIssue("gst_rate", "bad", "invalid"))
    first.raw_data["Status"] = "Cancelled"
    second.sheet_category = SheetCategory.B2BA
    second.amendment_original_number = "INV-OLD"
    second.amendment_original_date = date(2026, 3, 1)

    candidate = purchase_candidate(aggregate_gst_invoices([first, second])[0])

    assert candidate.transaction_type == TallyExportTransactionType.PURCHASE_INVOICE
    assert candidate.supplier_gstin == GSTIN and candidate.supplier_gstin_valid
    assert candidate.document_number == "INV-1"
    assert candidate.taxable_value == D("300")
    assert candidate.cess == D("3") and candidate.cess_present and candidate.has_cess
    assert candidate.invoice_total == D("329.00")
    assert candidate.gst_rates == (D("5"), D("12"))
    assert candidate.has_multiple_gst_rates and not candidate.gst_rate_missing
    assert candidate.tax_structure == TallyTaxStructure.MIXED_OR_INCONSISTENT
    assert candidate.place_of_supply == ""
    assert candidate.place_of_supply_values == ("Haryana",)
    assert candidate.place_of_supply_missing and not candidate.place_of_supply_conflicting
    assert candidate.reverse_charge_status == ReverseChargeStatus.CONFLICTING
    assert not candidate.has_reverse_charge
    assert candidate.is_amendment
    assert candidate.amendment_original_number == "INV-OLD"
    assert candidate.amendment_original_date == date(2026, 3, 1)
    assert candidate.is_cancelled and candidate.cancellation_evidence
    assert [(source.source_sheet, source.source_row) for source in candidate.source_rows] == [
        ("b2b", 2), ("b2b", 3),
    ]
    assert candidate.has_unresolved_financial_issues


def test_purchase_candidate_distinguishes_supported_tax_structures():
    igst = purchase_candidate(aggregate_gst_invoices([
        _gst_row(igst="18", cgst="0", sgst="0"),
    ])[0])
    intrastate = purchase_candidate(aggregate_gst_invoices([_gst_row()])[0])
    unequal = purchase_candidate(aggregate_gst_invoices([
        _gst_row(cgst="9", sgst="8"),
    ])[0])

    assert igst.tax_structure == TallyTaxStructure.IGST_ONLY
    assert intrastate.tax_structure == TallyTaxStructure.CGST_SGST
    assert unequal.tax_structure == TallyTaxStructure.MIXED_OR_INCONSISTENT
    assert igst.reverse_charge_status == ReverseChargeStatus.NO


def test_candidate_exposes_conflicting_place_and_positive_reverse_charge():
    first = _gst_row(place="Haryana", reverse_charge="Yes", rate=None)
    second = _gst_row(row=3, place="Punjab", reverse_charge="Y")

    candidate = purchase_candidate(aggregate_gst_invoices([first, second])[0])

    assert candidate.place_of_supply == ""
    assert candidate.place_of_supply_values == ("Haryana", "Punjab")
    assert candidate.place_of_supply_conflicting
    assert not candidate.place_of_supply_missing
    assert candidate.reverse_charge_status == ReverseChargeStatus.YES
    assert candidate.has_reverse_charge
    assert candidate.gst_rate_missing


def test_supplier_credit_note_candidate_preserves_note_and_source_metadata():
    row = _gst_row(category=SheetCategory.CDNR, cess="4", rate="18")
    row.voucher_type = VoucherType.CREDIT_NOTE
    row.note_type = "Credit Note"
    row.note_number = "CN-1"
    row.note_date = date(2026, 4, 2)
    row.original_invoice_number = "INV-9"
    row.total_invoice_value = D("122")
    row.total_invoice_value_present = True

    aggregate = aggregate_gst_credit_notes([row])[0]
    candidate = supplier_credit_note_candidate(aggregate)

    assert candidate.transaction_type == TallyExportTransactionType.SUPPLIER_CREDIT_NOTE
    assert candidate.document_number == "CN-1"
    assert candidate.document_date == date(2026, 4, 2)
    assert candidate.original_invoice_number == "INV-9"
    assert candidate.cess == D("4") and candidate.has_cess
    assert candidate.invoice_total == D("122")
    assert candidate.source_rows[0].sheet_category == SheetCategory.CDNR


def test_canonical_cess_survives_existing_session_serialization():
    row = _gst_row(cess="7.25")
    restored = decode_session_value(encode_session_value(row))

    assert restored.cess == D("7.25")
    assert restored.cess_present is True


def test_pre_cess_session_payload_uses_backward_compatible_defaults():
    payload = encode_session_value(_gst_row(cess="7.25"))
    payload["fields"].pop("cess")
    payload["fields"].pop("cess_present")

    restored = decode_session_value(payload)

    assert restored.cess == D("0.00")
    assert restored.cess_present is False
