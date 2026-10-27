from datetime import date

from openpyxl import Workbook

from backend.app.models.records import SheetCategory
from backend.app.services.file_parser import parse_excel_file
from backend.app.services.sheet_detector import detect_sheet


DAYBOOK_HEADERS = [
    "Date", None, None, "Supplier Invoice No.", "GSTIN/UIN", "Value",
    "Addl. Cost", "Gross Total", "Purchase 18%", "Pur 5%", "CGST",
    "SGST", "Round Off", "Number Palat  18%", "Tractors Pur 5%",
    "I-GST", "Frieght 18% GST Paid", "Air Conditioner", "SERVICE INCOME 18%",
]


def daybook_rows():
    return [
        ["Company"], ["Address"], ["Purchase Register"], ["Period"], DAYBOOK_HEADERS,
        [date(2026, 4, 1), "Alpha Supplier", "Purchase", "A1", "06ABCDE1234F1Z5",
         1000, None, 1180, 1000, None, 90, 90, 0, None, None, None, None, None, None],
        [date(2026, 4, 2), "Beta Supplier", "Purchase", "A2", "07ABCDE1234F1Z3",
         None, None, 590, None, None, 45, 45, 0, None, None, None, 500, None, None],
        [None, "Grand Total", "", "", "", 1000, None, 1770],
        ["Footer"],
    ]


def gstr_b2b_rows():
    return [
        ["Goods and Services Tax - GSTR-2B"], [], [], ["Taxable inward supplies"],
        ["GSTIN of supplier", "Trade/Legal name", "Invoice Details", None, None, None,
         "Place of supply", "Supply Attract Reverse Charge", "Taxable Value (₹)",
         "Tax Amount", None, None, None],
        [None, None, "Invoice number", "Invoice type", "Invoice Date", "Invoice Value(₹)",
         None, None, None, "Integrated Tax(₹)", "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)"],
        ["06ABCDE1234F1Z5", "Alpha Supplier", "A1", "Regular", "01/04/2026", 1180,
         "Haryana", "No", 1000, 0, 90, 90, 0],
    ]


def test_daybook_blank_columns_are_stable_and_roles_are_unique():
    result = detect_sheet("Purchase Register", daybook_rows())
    mappings = {mapping.semantic_name: mapping.detected_header for mapping in result.column_mappings}
    assert result.category == SheetCategory.PURCHASE_REGISTER
    assert result.header_row == 5 and result.data_start_row == 6
    assert result.headers[1:3] == ["Column B (blank header)", "Column C (blank header)"]
    assert mappings["supplier_name"] == "Column B (blank header)"
    assert mappings["voucher_type"] == "Column C (blank header)"
    assert mappings["invoice_number"] == "Supplier Invoice No."
    assert mappings["supplier_name"] != mappings["invoice_number"]


def test_daybook_all_financial_and_identity_mappings():
    result = detect_sheet("Purchase Register", daybook_rows())
    mappings = {mapping.semantic_name: mapping.detected_header for mapping in result.column_mappings}
    assert mappings | {
        "gstin": "GSTIN/UIN", "invoice_date": "Date", "taxable_value": "Value",
        "igst": "I-GST", "cgst": "CGST", "sgst": "SGST",
    } == mappings


def test_daybook_parser_excludes_footers_and_recovers_ledger_taxable(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Purchase Register"
    for row in daybook_rows():
        sheet.append(row)
    path = tmp_path / "daybook.xlsx"
    workbook.save(path)
    parsed = parse_excel_file(str(path))
    assert len(parsed.records) == 2
    assert [record.supplier_name for record in parsed.records] == ["Alpha Supplier", "Beta Supplier"]
    assert [record.taxable_value for record in parsed.records] == [1000, 500]
    persisted = parsed.persisted_sheets[0]
    assert persisted.headers[1] == "Column B (blank header)"
    assert persisted.row_numbers == [6, 7, 8, 9]


def test_unrelated_or_ambiguous_blank_headers_are_not_guessed():
    unrelated = detect_sheet("Random", [["Date", None, "Value"], [date.today(), "Text", 1]])
    assert unrelated.category != SheetCategory.PURCHASE_REGISTER
    ambiguous_rows = daybook_rows()
    ambiguous_rows[6][2] = "Second Supplier"
    result = detect_sheet("Purchase Register", ambiguous_rows)
    semantics = {mapping.semantic_name for mapping in result.column_mappings}
    assert "supplier_name" not in semantics


def test_two_row_b2b_header_preserves_leaf_identity_and_mappings():
    result = detect_sheet("B2B", gstr_b2b_rows())
    mappings = {mapping.semantic_name: mapping.detected_header for mapping in result.column_mappings}
    assert result.category == SheetCategory.B2B
    assert result.header_row == 5 and result.data_start_row == 7
    assert result.headers[:13] == [
        "GSTIN of supplier", "Trade/Legal name", "Invoice number", "Invoice type",
        "Invoice Date", "Invoice Value(₹)", "Place of supply",
        "Supply Attract Reverse Charge", "Taxable Value (₹)", "Integrated Tax(₹)",
        "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)",
    ]
    assert mappings["gstin"] == "GSTIN of supplier"
    assert mappings["supplier_name"] == "Trade/Legal name"
    assert mappings["invoice_number"] == "Invoice number"
    assert mappings["taxable_value"] == "Taxable Value (₹)"
    assert mappings["total_invoice_value"] == "Invoice Value(₹)"
    assert [mappings[key] for key in ("igst", "cgst", "sgst")] == [
        "Integrated Tax(₹)", "Central Tax(₹)", "State/UT Tax(₹)",
    ]


def test_multirow_header_rows_are_not_parsed_as_invoices(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "B2B"
    for row in gstr_b2b_rows():
        sheet.append(row)
    path = tmp_path / "gstr.xlsx"
    workbook.save(path)
    parsed = parse_excel_file(str(path))
    b2b = parsed.records_by_category(SheetCategory.B2B)
    assert len(b2b) == 1
    assert b2b[0].source_row == 7
    assert b2b[0].taxable_value == 1000


def test_three_row_b2ba_prefers_revised_invoice_and_tax_fields():
    rows = [
        ["Title"], [], [], ["Amendments"],
        ["Original Details", None, "Revised Details"],
        ["Invoice number", "Invoice Date", "GSTIN of supplier", "Trade/Legal name",
         "Invoice Details", None, None, None, "Place of supply", "Supply Attract Reverse Charge",
         "Taxable Value (₹)", "Tax Amount", None, None, None],
        [None, None, None, None, "Invoice number", "Invoice type", "Invoice Date", "Invoice Value(₹)",
         None, None, None, "Integrated Tax(₹)", "Central Tax(₹)", "State/UT Tax(₹)", "Cess(₹)"],
    ]
    result = detect_sheet("B2BA", rows)
    mappings = {mapping.semantic_name: mapping.detected_header for mapping in result.column_mappings}
    assert result.category == SheetCategory.B2BA
    assert result.header_row == 5 and result.data_start_row == 8
    assert mappings["invoice_number"].startswith("Revised Details")
    assert mappings["invoice_date"].startswith("Revised Details")
    assert mappings["igst"].endswith("Integrated Tax(₹)")


def test_cdnr_multirow_sheet_remains_notes_provenance():
    rows = gstr_b2b_rows()
    rows[4][2] = "Credit note/Debit note details"
    rows[5][2] = "Note number"
    rows[5][3] = "Note type"
    result = detect_sheet("B2B-CDNR", rows)
    assert result.category == SheetCategory.CDNR
    assert result.category not in (SheetCategory.B2B, SheetCategory.B2BA)
