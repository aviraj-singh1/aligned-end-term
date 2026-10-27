from datetime import date
from decimal import Decimal

from backend.app.models.records import ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType
from backend.app.models.reconciliation import MatchMethod, ReconciliationStatus
from backend.app.services.file_parser import _row_to_record
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.reconciliation_engine import reconcile_purchases


D = Decimal
GSTIN = "06DEMOX1234A1Z5"


def _accounts(*, supplier="Alpha Traders", invoice="INV-1", invoice_date=date(2026, 4, 1), gross=None):
    return ParsedInvoiceRecord(
        source=RecordSource.TALLY,
        source_sheet="Purchase Register",
        supplier_name=supplier,
        gstin_original=GSTIN,
        gstin_normalized=GSTIN,
        gstin_valid=True,
        invoice_number=normalize_invoice_number(invoice),
        invoice_date=invoice_date,
        voucher_type=VoucherType.PURCHASE,
        taxable_value=D("100"),
        gross_total=D(str(gross)) if gross is not None else D("0"),
        gross_total_present=gross is not None,
    )


def _gstr(*, supplier="Alpha Traders", invoice="INV-1", invoice_date=date(2026, 4, 1), invoice_value=None):
    return ParsedInvoiceRecord(
        source=RecordSource.GST,
        source_sheet="b2b",
        sheet_category=SheetCategory.B2B,
        supplier_name=supplier,
        gstin_original=GSTIN,
        gstin_normalized=GSTIN,
        gstin_valid=True,
        invoice_number=normalize_invoice_number(invoice),
        invoice_date=invoice_date,
        voucher_type=VoucherType.PURCHASE,
        taxable_value=D("100"),
        total_invoice_value=D(str(invoice_value)) if invoice_value is not None else D("0"),
        total_invoice_value_present=invoice_value is not None,
    )


def test_priority_uses_supplier_before_date_to_disambiguate_same_gstin_and_invoice():
    accounts = _accounts(invoice_date=date(2026, 4, 1))
    supplier_candidate = _gstr(invoice_date=date(2026, 4, 2))
    date_candidate = _gstr(supplier="Different Supplier", invoice_date=date(2026, 4, 1))

    result = reconcile_purchases([accounts], [supplier_candidate, date_candidate]).results[0]

    assert result.match_method == MatchMethod.EXACT_INVOICE
    assert result.supplier_gst == "Alpha Traders"
    assert result.gstr_date == date(2026, 4, 2)


def test_gross_total_is_compared_with_gstr_invoice_value():
    result = reconcile_purchases(
        [_accounts(gross="118.00")],
        [_gstr(invoice_value="120.50")],
    ).results[0]

    assert result.status == ReconciliationStatus.MAJOR_DIFFERENCE
    assert result.audit.tally_gross_total == D("118.00")
    assert result.audit.gst_invoice_value == D("120.50")
    assert result.gross_total_diff == D("-2.50")


def test_unmapped_gross_total_does_not_change_existing_classification():
    result = reconcile_purchases([_accounts()], [_gstr(invoice_value="999")]).results[0]
    assert result.status == ReconciliationStatus.PERFECTLY_MATCHED
    assert result.gross_total_diff is None


def test_parser_keeps_accounts_gross_total_separate_from_gstr_invoice_value():
    accounts = _row_to_record(
        ["INV-1", "118.00"], ["Invoice", "Gross"],
        {"invoice_number": 0, "gross_total": 1},
        "Purchase Register", SheetCategory.PURCHASE_REGISTER, 2,
    )
    gstr = _row_to_record(
        ["INV-1", "118.00"], ["Invoice", "Value"],
        {"invoice_number": 0, "total_invoice_value": 1},
        "b2b", SheetCategory.B2B, 2,
    )

    assert accounts.gross_total_present and accounts.gross_total == D("118.00")
    assert not accounts.total_invoice_value_present
    assert gstr.total_invoice_value_present and gstr.total_invoice_value == D("118.00")
    assert not gstr.gross_total_present
