"""
Tests for header_detector, column_detector, and sheet_detector modules.

Uses representative header rows from the actual sample files to verify
correct detection and classification.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.app.models.records import SheetCategory
from backend.app.services.header_detector import detect_header_row, score_row_as_header
from backend.app.services.column_detector import detect_columns, build_column_index_map
from backend.app.services.sheet_detector import detect_sheet
from backend.app.services.file_parser import _guess_json_category


# ===================================================================
# Header Detector
# ===================================================================

@pytest.mark.parametrize("sheet_name", ["cdnr", "CDNR", "Cdnr", "notes", "Notes", "NOTES", "note", " Note "])
def test_gstr_note_sheet_aliases_are_canonical_cdnr(sheet_name):
    rows = [
        ["GSTIN of supplier", "Note Number", "Note Type", "Note Date", "Taxable Value", "IGST", "CGST", "SGST"],
        ["06DEMOX1234A1Z5", "CN-1", "Credit Note", "10-05-2025", 100, 0, 9, 9],
    ]
    result = detect_sheet(sheet_name, rows)
    assert result.category == SheetCategory.CDNR
    assert result.sheet_name == sheet_name


@pytest.mark.parametrize("key", ["cdnr", "CDNR", "notes", "Notes", "note", " Note "])
def test_json_note_keys_are_canonical_cdnr(key):
    assert _guess_json_category(key) == SheetCategory.CDNR


class TestHeaderDetector:
    """Tests for automatic header row detection."""

    def test_purchase_register_headers(self):
        """Headers from the actual Debit Notes.xlsx Purchase Register sheet."""
        headers = [
            "Date", "Particulars", "Voucher Type", "Voucher No.",
            "GSTIN/UIN", "Value", "Addl. Cost", "Gross Total",
            "Accessery 18 %", "SGST", "CGST", "ROUND OFF",
            "Purchase 18%", "Igst", "PROSSESING FEE (IGST)", "PROSSESING FEE",
        ]
        score, matched = score_row_as_header(headers)
        assert score >= 6  # Should match many known fields
        assert "supplier_name" in matched  # Particulars
        assert "gstin" in matched          # GSTIN/UIN
        assert "invoice_number" in matched  # Voucher No.
        assert "invoice_date" in matched    # Date
        assert "sgst" in matched            # SGST
        assert "cgst" in matched            # CGST
        assert "gross_total" in matched     # Gross Total

    def test_gst_b2b_headers(self):
        """Headers from the actual 2A sample b2b sheet."""
        headers = [
            "Invoice Type", "GSTIN/UIN", "Party Name", "Invoice No.",
            "Invoice Date", "Place Of Supply", "Reverse Charge",
            "Total Invoice Value", "Taxable Value", "Rate",
            "IGST Amount", "CGST Amount", "SGST Amount", "CESS Amount",
            "UploadedOn", "Return Filed", "GSTR-3B filed",
            "GSTR-1 Filing Date", "GSTR-1 Filing Period",
            "Amendment, if any", "Amendment Period", "Cancellation Date",
            None, "If MSME, Payment due on",
        ]
        score, matched = score_row_as_header(headers)
        assert score >= 8
        assert "gstin" in matched
        assert "supplier_name" in matched
        assert "invoice_number" in matched
        assert "taxable_value" in matched
        assert "igst" in matched
        assert "cgst" in matched
        assert "sgst" in matched
        assert "total_invoice_value" in matched

    def test_cdnr_headers(self):
        """Headers from the actual 2A sample cdnr sheet."""
        headers = [
            "GSTIN/UIN", "Party Name", "Invoice Number", "Invoice date",
            "Note Type", "Note/Refund Number", "Note/Refund date",
            "Reason For Issuing document", "Place Of Supply", "Pre GST",
            "Note/Refund Value", "Taxable Value", "Rate",
            "IGST", "CGST", "SGST", "Cess Amount",
            "Uploaded On", "Return Filed",
            "Amendment, if any", "Amendment Period",
        ]
        score, matched = score_row_as_header(headers)
        assert score >= 6
        assert "gstin" in matched
        assert "note_number" in matched or "note_type" in matched
        assert "taxable_value" in matched

    def test_header_not_on_row_1(self):
        """When headers are not on row 1, detect the correct row."""
        rows = [
            ["Company Name"],
            ["Report Title"],
            [""],
            ["Date", "Particulars", "Voucher No.", "GSTIN/UIN", "Value"],
            ["2025-04-02", "Kumar Traders", "KT0039", "06DEMOX1234A1Z5", 83366.11],
        ]
        best_idx, best_values, confidence, candidates = detect_header_row(rows)
        assert best_idx == 3  # 0-indexed → row 4
        assert "Date" in best_values
        assert "Particulars" in best_values

    def test_empty_sheet(self):
        best_idx, best_values, confidence, candidates = detect_header_row([])
        assert confidence == 0.0

    def test_all_blank_rows(self):
        rows = [[None, None, None]] * 10
        best_idx, best_values, confidence, candidates = detect_header_row(rows)
        assert confidence == 0.0


# ===================================================================
# Column Detector
# ===================================================================

class TestColumnDetector:
    """Tests for column mapping detection."""

    def test_tally_column_mapping(self):
        headers = [
            "Date", "Particulars", "Voucher Type", "Voucher No.",
            "GSTIN/UIN", "Value", "Addl. Cost", "Gross Total",
            "SGST", "CGST", "ROUND OFF", "Igst",
        ]
        mappings = detect_columns(headers)
        semantic_names = {m.semantic_name for m in mappings}

        assert "invoice_date" in semantic_names
        assert "supplier_name" in semantic_names
        assert "voucher_type" in semantic_names
        assert "invoice_number" in semantic_names
        assert "gstin" in semantic_names
        assert "taxable_value" in semantic_names
        assert "sgst" in semantic_names
        assert "cgst" in semantic_names
        assert "igst" in semantic_names

    def test_gst_b2b_column_mapping(self):
        headers = [
            "GSTIN/UIN", "Party Name", "Invoice No.",
            "Invoice Date", "Taxable Value", "IGST Amount",
            "CGST Amount", "SGST Amount",
        ]
        mappings = detect_columns(headers)
        semantic_names = {m.semantic_name for m in mappings}

        assert "gstin" in semantic_names
        assert "supplier_name" in semantic_names
        assert "invoice_number" in semantic_names
        assert "invoice_date" in semantic_names
        assert "taxable_value" in semantic_names
        assert "igst" in semantic_names

    def test_tax_column_aliases(self):
        """Verify robust tax-column alias mapping and canonical normalization."""
        # 1-14: Test various delimiters, whitespace, and cases
        test_headers = [
            "Igst", "I-gst", "I GST", "I_GST",  # IGST variations
            "Cgst", "C-gst", "C GST", "C_GST",  # CGST variations
            "Sgst", "S-gst", "S GST", "S_GST",  # SGST variations
            "   i-GsT  ", " c_GsT\t", "\nS GsT "  # Whitespace and case variations
        ]
        
        for h in test_headers:
            mappings = detect_columns([h])
            assert len(mappings) == 1, f"Failed to map header: {h!r}"
            m = mappings[0]
            # 15: Original physical column name is preserved
            assert m.detected_header == h.strip()
            
            # Check semantic name mapping
            h_lower = h.lower()
            if "i" in h_lower:
                assert m.semantic_name == "igst"
            elif "c" in h_lower:
                assert m.semantic_name == "cgst"
            elif "s" in h_lower:
                assert m.semantic_name == "sgst"

    def test_existing_formats_remain_correct(self):
        """Verify existing GSTR and Tally standard headers still work."""
        # 16: Existing GSTR headers
        gstr_headers = ["IGST Amount", "CGST Amount", "SGST Amount"]
        mappings = detect_columns(gstr_headers)
        semantic_names = {m.semantic_name for m in mappings}
        assert "igst" in semantic_names
        assert "cgst" in semantic_names
        assert "sgst" in semantic_names
        
        # 17: Existing old Tally format
        tally_headers = ["Igst", "CGST", "SGST"]
        mappings = detect_columns(tally_headers)
        semantic_names = {m.semantic_name for m in mappings}
        assert "igst" in semantic_names
        assert "cgst" in semantic_names
        assert "sgst" in semantic_names

    def test_tax_column_ambiguity(self):
        """Verify ambiguity handling (e.g., both CGST and C-gst exist)."""
        # 18: If both exist and tie, do not silently choose arbitrarily; leave unmapped
        headers = ["CGST", "C-gst"]
        mappings = detect_columns(headers)
        semantic_names = {m.semantic_name for m in mappings}
        assert "cgst" not in semantic_names

    def test_column_index_map(self):
        headers = ["Date", "Particulars", "Voucher No.", "Value"]
        mappings = detect_columns(headers)
        col_map = build_column_index_map(headers, mappings)

        assert col_map.get("invoice_date") == 0
        assert col_map.get("supplier_name") == 1
        assert col_map.get("invoice_number") == 2
        assert col_map.get("taxable_value") == 3

    def test_no_match_columns(self):
        headers = ["Column A", "Column B", "Column C"]
        mappings = detect_columns(headers)
        assert len(mappings) == 0

    def test_account_maps_to_supplier_without_hiding_ambiguity(self):
        mappings = detect_columns(["Account"])
        assert [(m.semantic_name, m.detected_header) for m in mappings] == [
            ("supplier_name", "Account")
        ]

        ambiguous = detect_columns(["Account", "Account"])
        assert "supplier_name" not in {m.semantic_name for m in ambiguous}


# ===================================================================
# Sheet Detector
# ===================================================================

class TestSheetDetector:
    """Tests for sheet classification."""

    def test_purchase_register_detection(self):
        """Detect Purchase Register from actual headers."""
        rows = [
            ["Date", "Particulars", "Voucher Type", "Voucher No.",
             "GSTIN/UIN", "Value", "SGST", "CGST", "Igst"],
            ["2025-04-02", "Kumar Traders", "Purchase", "KT0039",
             "06DEMOX1234A1Z5", 83366.11, 7502.95, 7502.95, None],
        ]
        result = detect_sheet("Purchase Register", rows)
        assert result.category == SheetCategory.PURCHASE_REGISTER
        assert result.header_row == 1
        assert result.total_data_rows == 1

    def test_b2b_detection(self):
        """Detect B2B sheet from actual headers."""
        rows = [
            ["Invoice Type", "GSTIN/UIN", "Party Name", "Invoice No.",
             "Invoice Date", "Taxable Value", "IGST Amount", "CGST Amount",
             "SGST Amount"],
        ]
        result = detect_sheet("b2b", rows)
        assert result.category == SheetCategory.B2B

    def test_cdnr_detection(self):
        """Detect CDNR sheet from actual headers."""
        rows = [
            ["GSTIN/UIN", "Party Name", "Invoice Number", "Invoice date",
             "Note Type", "Note/Refund Number", "Note/Refund date",
             "Taxable Value", "IGST", "CGST", "SGST"],
        ]
        result = detect_sheet("cdnr", rows)
        assert result.category == SheetCategory.CDNR

    def test_debit_note_register(self):
        """Detect Debit Note Register from actual headers."""
        rows = [
            ["Date", "Particulars", "Voucher Type", "Voucher No.",
             "GSTIN/UIN", "Value", "SGST", "CGST", "ROUND OFF"],
        ]
        result = detect_sheet("Debit Note Register", rows)
        assert result.category == SheetCategory.DEBIT_NOTE_REGISTER

    def test_unknown_sheet(self):
        rows = [
            ["Column A", "Column B", "Column C"],
            ["val1", "val2", "val3"],
        ]
        result = detect_sheet("Random Sheet", rows)
        assert result.category == SheetCategory.UNKNOWN

    def test_empty_sheet(self):
        result = detect_sheet("Empty", [])
        assert result.category == SheetCategory.UNKNOWN
        assert result.total_data_rows == 0

    def test_headers_below_row_1(self):
        """Headers not on row 1 — should still detect correctly."""
        rows = [
            ["", "", ""],
            ["Report Title", "", ""],
            ["", "", ""],
            ["Date", "Particulars", "Voucher No.", "GSTIN/UIN", "Value",
             "Voucher Type", "SGST", "CGST"],
            ["2025-04-02", "Kumar Traders", "KT0039", "06DEMOX1234A1Z5",
             83366.11, "Purchase", 7502.95, 7502.95],
        ]
        result = detect_sheet("Purchase Register", rows)
        assert result.header_row == 4  # 1-indexed
        assert result.total_data_rows == 1

    def test_large_purchase_register_shape_on_generic_sheet(self):
        headers = [
            "Date", "Vch/Bill No", "Account", "TIN/GSTIN No.", "Type",
            "Total Amount", "Purc. Amount", "Taxable Amt.", "IGST", "CGST",
            "SGST", "Other Amount.", "Notes",
        ]
        rows = [
            ["SYNTHETIC COMPANY"], ["Purchase Register"], ["FY 2025-26"], ["Report"],
            headers,
            ["01-04-2025", "QA-1", "Alpha Traders", "06ABCDE1234F1Z5",
             "Purchase", 11800, 10000, 10000, 0, 900, 900, 0, ""],
        ]

        result = detect_sheet("Sheet1", rows)
        mappings = {m.semantic_name: m.detected_header for m in result.column_mappings}

        assert result.category == SheetCategory.PURCHASE_REGISTER
        assert result.header_row == 5
        assert result.headers == headers
        assert mappings["supplier_name"] == "Account"
        assert mappings["gstin"] == "TIN/GSTIN No."
        assert mappings["invoice_number"] == "Vch/Bill No"
        assert mappings["taxable_value"] == "Taxable Amt."

    @pytest.mark.parametrize("headers", [
        ["Account", "Date"],
        ["Account", "TIN/GSTIN No."],
        ["Account", "Vch/Bill No", "Type"],
    ])
    def test_partial_large_register_headers_are_not_purchase_register(self, headers):
        result = detect_sheet("Random Sheet", [headers, ["value"] * len(headers)])
        assert result.category != SheetCategory.PURCHASE_REGISTER


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
