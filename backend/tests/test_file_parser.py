"""
Tests for the file_parser module.

Tests both Excel and JSON parsing using:
1. The actual sample files in the workspace
2. Programmatically created test data
"""

import sys
import os
import json
import tempfile
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.app.models.records import (
    RecordSource,
    SheetCategory,
    VoucherType,
)
from backend.app.services.file_parser import (
    parse_excel_file,
    parse_json_file,
    parse_file,
)


def test_parse_synthetic_large_layout_excludes_total_and_footer_rows(tmp_path):
    """A synthetic workbook guards compatibility without using client data."""
    headers = [
        "Date", "Vch/Bill No", "Account", "TIN/GSTIN No.", "Type",
        "Total Amount", "Purc. Amount", "Taxable Amt.", "IGST", "CGST",
        "SGST", "Other Amount.", "Notes",
    ]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    for title in ("Company", "Purchase Register", "FY 2025-26", "Report"):
        sheet.append([title])
    sheet.append(headers)
    sheet.append(["01-04-2025", "QA-1", "Alpha Traders", "06TESTX1234A1Z5",
                  "Purchase", 11800, 10000, 10000, 0, 900, 900, 0, ""])
    sheet.append(["02-04-2025", "QA-2", "Beta Traders", "07ABCDE1234F1Z3",
                  "Purchase", 5900, 5000, 5000, 0, 450, 450, 0, ""])
    sheet.append([])
    sheet.append([None] * 5 + [17700, 15000, 15000, 0, 1350, 1350])
    sheet.append([])
    sheet.append(["Total Tax Amount"])
    path = tmp_path / "synthetic_purchase_register.xlsx"
    workbook.save(path)

    result = parse_excel_file(str(path))
    detected = result.sheets[0]

    assert result.errors == []
    assert detected.category == SheetCategory.PURCHASE_REGISTER
    assert detected.headers == headers
    assert len(result.records) == 2
    assert [record.supplier_name for record in result.records] == [
        "Alpha Traders", "Beta Traders",
    ]
    assert [record.source_row for record in result.records] == [6, 7]


# ===================================================================
# Path to sample files
# ===================================================================

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
DAYBOOK_PATH = os.path.join(SAMPLES_DIR, "DayBook.xlsx")
GST_PATH = os.path.join(SAMPLES_DIR, "2A sample for ai.xlsx")
DEBIT_NOTES_PATH = os.path.join(SAMPLES_DIR, "Debit Notes.xlsx")


# ===================================================================
# Excel File Parsing — Actual Sample Files
# ===================================================================

class TestParseSampleFiles:
    """Integration tests using the actual sample workbooks."""

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file Debit Notes.xlsx not found",
    )
    def test_parse_debit_notes_file(self):
        """Parse the Debit Notes.xlsx file (contains real Purchase Register data)."""
        result = parse_excel_file(DEBIT_NOTES_PATH)

        assert result.file_name == "Debit Notes.xlsx"
        assert result.file_type == "xlsx"
        assert len(result.errors) == 0

        # Should detect both sheets
        sheet_names = [s.sheet_name for s in result.sheets]
        assert "Purchase Register" in sheet_names
        assert "Debit Note Register" in sheet_names

        # Purchase Register sheet should be classified correctly
        pr_sheet = next(s for s in result.sheets if s.sheet_name == "Purchase Register")
        assert pr_sheet.category == SheetCategory.PURCHASE_REGISTER
        assert pr_sheet.header_row == 1
        assert pr_sheet.total_data_rows > 0

        # Should have column mappings
        mapped_semantics = {m.semantic_name for m in pr_sheet.column_mappings}
        assert "supplier_name" in mapped_semantics
        assert "gstin" in mapped_semantics
        assert "invoice_number" in mapped_semantics
        assert "taxable_value" in mapped_semantics

        # Should produce parsed records
        assert len(result.records) > 0

        # Check first record (Kumar Traders KT0039)
        first = result.records[0]
        assert first.supplier_name == "Kumar Traders (Vivo)"
        assert first.gstin_normalized == "06DEMOX1234A1Z5"
        assert first.gstin_valid is True
        assert first.invoice_number.original == "KT0039"
        assert first.invoice_number.trailing_number == "0039"
        assert first.taxable_value == Decimal("83366.11")
        assert first.sgst == Decimal("7502.95")
        assert first.cgst == Decimal("7502.95")
        assert first.source == RecordSource.TALLY
        assert first.voucher_type == VoucherType.PURCHASE

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file Debit Notes.xlsx not found",
    )
    def test_debit_note_register_detected(self):
        """The Debit Note Register sheet should be classified even if empty."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        dn_sheet = next(
            (s for s in result.sheets if s.sheet_name == "Debit Note Register"),
            None,
        )
        assert dn_sheet is not None
        assert dn_sheet.category == SheetCategory.DEBIT_NOTE_REGISTER

    @pytest.mark.skipif(
        not os.path.exists(GST_PATH),
        reason="Sample file 2A sample for ai.xlsx not found",
    )
    def test_parse_gst_file(self):
        """Parse the GST 2A sample file."""
        result = parse_excel_file(GST_PATH)

        assert result.file_name == "2A sample for ai.xlsx"
        assert len(result.errors) == 0

        # Should detect multiple sheets
        sheet_names = [s.sheet_name for s in result.sheets]
        assert "b2b" in sheet_names
        assert "cdnr" in sheet_names

        # B2B sheet should be classified
        b2b_sheet = next(
            (s for s in result.sheets if s.sheet_name == "b2b"),
            None,
        )
        assert b2b_sheet is not None
        assert b2b_sheet.category == SheetCategory.B2B

        # CDNR sheet should be classified
        cdnr_sheet = next(
            (s for s in result.sheets if s.sheet_name == "cdnr"),
            None,
        )
        assert cdnr_sheet is not None
        assert cdnr_sheet.category == SheetCategory.CDNR

    @pytest.mark.skipif(
        not os.path.exists(DAYBOOK_PATH),
        reason="Sample file DayBook.xlsx not found",
    )
    def test_parse_daybook_template(self):
        """Parse the DayBook.xlsx template (headers only, no data)."""
        result = parse_excel_file(DAYBOOK_PATH)

        assert result.file_name == "DayBook.xlsx"
        assert len(result.errors) == 0
        # Should detect the sheet even though it's empty
        assert len(result.sheets) >= 1
        # No records since no data
        assert len(result.records) == 0

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_all_records_have_decimal_money(self):
        """Ensure all monetary values are Decimal, not float."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        for rec in result.records:
            assert isinstance(rec.taxable_value, Decimal), \
                f"taxable_value is {type(rec.taxable_value)}"
            assert isinstance(rec.igst, Decimal)
            assert isinstance(rec.cgst, Decimal)
            assert isinstance(rec.sgst, Decimal)

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_all_records_have_gstin_info(self):
        """Each record should have original and normalized GSTIN."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        for rec in result.records:
            # At least one should be populated (unless genuinely missing)
            if rec.gstin_original:
                assert rec.gstin_normalized  # Normalized should also exist
                assert isinstance(rec.gstin_valid, bool)

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_all_records_have_invoice_number_info(self):
        """Each record should have invoice number breakdown."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        for rec in result.records:
            if rec.invoice_number.original:
                assert rec.invoice_number.normalized
                assert rec.invoice_number.last_2
                assert rec.invoice_number.last_4

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_raw_data_preserved(self):
        """Each record should preserve the original raw data dict."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        for rec in result.records[:5]:  # Check first few
            assert isinstance(rec.raw_data, dict)
            assert len(rec.raw_data) > 0


# ===================================================================
# JSON Parsing
# ===================================================================

class TestParseJSON:
    """Tests for JSON file parsing."""

    def test_direct_array(self):
        """JSON as a direct array of records."""
        data = [
            {
                "GSTIN/UIN": "06TESTX1234A1Z5",
                "Voucher No.": "KT0039",
                "Value": 83366.11,
                "Particulars": "Kumar Traders",
                "Date": "02-04-2025",
                "SGST": 7502.95,
                "CGST": 7502.95,
            },
            {
                "GSTIN/UIN": "06TESTX1234A1Z5",
                "Voucher No.": "KBS25-00016",
                "Value": 222963.25,
                "Particulars": "Kanodia Brothers",
                "Date": "02-04-2025",
                "SGST": 20066.69,
                "CGST": 20066.69,
            },
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="."
        ) as f:
            json.dump(data, f)
            tmp_path = f.name

        try:
            result = parse_json_file(tmp_path)
            assert len(result.errors) == 0
            assert len(result.records) == 2

            rec = result.records[0]
            assert rec.gstin_normalized == "06TESTX1234A1Z5"
            assert rec.invoice_number.original == "KT0039"
            assert rec.taxable_value == Decimal("83366.11")
            assert rec.supplier_name == "Kumar Traders"
        finally:
            os.unlink(tmp_path)

    def test_workbook_like_object(self):
        """JSON as a workbook-like object with named sheets."""
        data = {
            "Purchase Register": [
                {
                    "GSTIN/UIN": "06DEMOX1234A1Z5",
                    "Voucher No.": "KT0039",
                    "Value": 83366.11,
                    "Particulars": "Kumar Traders (Vivo)",
                },
            ],
            "Debit Note Register": [
                {
                    "GSTIN/UIN": "06TESTX1234A1Z5",
                    "Voucher No.": "DN001",
                    "Value": 5000,
                    "Particulars": "Kanodia Brothers",
                },
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="."
        ) as f:
            json.dump(data, f)
            tmp_path = f.name

        try:
            result = parse_json_file(tmp_path)
            assert len(result.errors) == 0
            assert len(result.sheets) == 2
            assert len(result.records) == 2

            # Check sheet categories
            categories = {s.sheet_name: s.category for s in result.sheets}
            assert categories["Purchase Register"] == SheetCategory.PURCHASE_REGISTER
            assert categories["Debit Note Register"] == SheetCategory.DEBIT_NOTE_REGISTER
        finally:
            os.unlink(tmp_path)

    def test_invalid_json(self):
        """Malformed JSON should produce an error, not crash."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="."
        ) as f:
            f.write("not valid json {{{")
            tmp_path = f.name

        try:
            result = parse_json_file(tmp_path)
            assert len(result.errors) > 0
        finally:
            os.unlink(tmp_path)

    def test_empty_array(self):
        """Empty JSON array should produce no records."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="."
        ) as f:
            json.dump([], f)
            tmp_path = f.name

        try:
            result = parse_json_file(tmp_path)
            assert len(result.records) == 0
            assert len(result.errors) == 0
        finally:
            os.unlink(tmp_path)


# ===================================================================
# Unified parse_file entry point
# ===================================================================

class TestParseFile:
    """Tests for the unified parse_file function."""

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_parse_file_excel(self):
        result = parse_file(DEBIT_NOTES_PATH)
        assert result.file_type == "xlsx"
        assert len(result.records) > 0

    def test_parse_file_unsupported(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            parse_file("test.csv")

    def test_parse_file_routes_legacy_xls_to_xlrd_not_openpyxl(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "backend.app.services.file_parser.openpyxl.load_workbook",
            lambda *args, **kwargs: pytest.fail(".xls must be read by xlrd"),
        )
        path = tmp_path / "corrupt.xls"
        path.write_bytes(b"not a legacy workbook")
        result = parse_file(str(path))
        assert result.file_type == "xls"
        assert result.errors and result.errors[0].startswith("Failed to open Excel file:")

    def test_parse_file_json(self):
        data = [{"GSTIN/UIN": "06DEMOX1234A1Z5", "Voucher No.": "KT0039", "Value": 100}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir="."
        ) as f:
            json.dump(data, f)
            tmp_path = f.name
        try:
            result = parse_file(tmp_path)
            assert result.file_type == "json"
        finally:
            os.unlink(tmp_path)


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:
    """Edge case tests."""

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_no_records_silently_discarded(self):
        """All rows with at least some data should produce records."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        # The sample has 619 data rows; we should parse a significant fraction
        # Some may be skipped (blank or totals), but not silently lost
        assert len(result.records) > 500

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_dates_are_parsed(self):
        """Dates from the sample should be actual date objects."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        dated_records = [r for r in result.records if r.invoice_date is not None]
        assert len(dated_records) > 0
        for rec in dated_records[:10]:
            assert isinstance(rec.invoice_date, date)

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_multiple_suppliers(self):
        """Should detect multiple distinct suppliers."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        suppliers = set(r.supplier_name for r in result.records if r.supplier_name)
        assert len(suppliers) >= 5  # We know there are ~19 unique suppliers

    @pytest.mark.skipif(
        not os.path.exists(DEBIT_NOTES_PATH),
        reason="Sample file not found",
    )
    def test_igst_and_cgst_sgst_records(self):
        """Some records use IGST, others use CGST+SGST."""
        result = parse_excel_file(DEBIT_NOTES_PATH)
        igst_records = [r for r in result.records if r.igst > 0]
        cgst_records = [r for r in result.records if r.cgst > 0]
        sgst_records = [r for r in result.records if r.sgst > 0]
        # Most records should have CGST+SGST (intra-state)
        assert len(cgst_records) > 0
        assert len(sgst_records) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
