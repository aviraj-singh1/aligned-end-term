"""
Tests for the normalizer module.

Covers:
- GSTIN normalization and validation
- Invoice number normalization with multi-level breakdown
- Monetary value parsing (Decimal)
- Date parsing
- Supplier name normalization
"""

import sys
import os
from datetime import date
from decimal import Decimal

import pytest

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.app.services.normalizer import (
    normalize_gstin,
    normalize_invoice_number,
    normalize_supplier_name,
    parse_date,
    parse_money,
    safe_parse_money,
)


# ===================================================================
# GSTIN Normalization
# ===================================================================

class TestNormalizeGSTIN:
    """Tests for GSTIN normalization and validation."""

    def test_valid_gstin(self):
        orig, norm, valid = normalize_gstin("06DEMOX1234A1Z5")
        assert orig == "06DEMOX1234A1Z5"
        assert norm == "06DEMOX1234A1Z5"
        assert valid is True

    def test_valid_gstin_lowercase(self):
        orig, norm, valid = normalize_gstin("06demox1234a1z5")
        assert norm == "06DEMOX1234A1Z5"
        assert valid is True

    def test_gstin_with_spaces(self):
        orig, norm, valid = normalize_gstin("06 DEMOX 1234A1Z5")
        assert norm == "06DEMOX1234A1Z5"
        assert valid is True

    def test_gstin_with_leading_trailing_spaces(self):
        orig, norm, valid = normalize_gstin("  06DEMOX1234A1Z5  ")
        assert orig == "06DEMOX1234A1Z5"
        assert norm == "06DEMOX1234A1Z5"
        assert valid is True

    def test_none_gstin(self):
        orig, norm, valid = normalize_gstin(None)
        assert orig == ""
        assert norm == ""
        assert valid is False

    def test_empty_gstin(self):
        orig, norm, valid = normalize_gstin("")
        assert valid is False

    def test_invalid_gstin_too_short(self):
        orig, norm, valid = normalize_gstin("06AAAFK")
        assert valid is False

    def test_invalid_gstin_bad_format(self):
        orig, norm, valid = normalize_gstin("XXXXXXXXXXXXXXX")
        assert valid is False

    def test_gstin_from_sample_file(self):
        """Using actual GSTIN from the sample Purchase Register."""
        orig, norm, valid = normalize_gstin("06TESTX1234A1Z5")
        assert norm == "06TESTX1234A1Z5"
        assert valid is True

    def test_gstin_preserves_original(self):
        orig, norm, valid = normalize_gstin("06 demox1234a1z5")
        assert orig == "06 demox1234a1z5"
        assert norm == "06DEMOX1234A1Z5"


# ===================================================================
# Invoice Number Normalization
# ===================================================================

class TestNormalizeInvoiceNumber:
    """Tests for invoice number normalization."""

    def test_standard_invoice(self):
        info = normalize_invoice_number("SBT/2025-26/4587")
        assert info.original == "SBT/2025-26/4587"
        assert info.trailing_number == "4587"
        assert info.last_4 == "4587"
        assert info.last_2 == "87"

    def test_simple_invoice(self):
        info = normalize_invoice_number("KT0039")
        assert info.original == "KT0039"
        assert info.normalized == "KT0039"
        assert info.trailing_number == "0039"
        assert info.last_4 == "0039"
        assert info.last_2 == "39"

    def test_invoice_with_dashes(self):
        info = normalize_invoice_number("KBS25-00016")
        assert info.original == "KBS25-00016"
        assert info.trailing_number == "00016"
        assert info.last_4 == "0016"
        assert info.last_2 == "16"

    def test_numeric_invoice(self):
        """Excel may store invoice numbers as integers."""
        info = normalize_invoice_number(12345)
        assert info.original == "12345"
        assert info.normalized == "12345"
        assert info.trailing_number == "12345"

    def test_float_invoice(self):
        """Excel may store as float with .0."""
        info = normalize_invoice_number(12345.0)
        assert info.original == "12345"
        assert info.trailing_number == "12345"

    def test_none_invoice(self):
        info = normalize_invoice_number(None)
        assert info.original == ""
        assert info.normalized == ""

    def test_whitespace_invoice(self):
        info = normalize_invoice_number("  KT0039  ")
        assert info.original == "KT0039"
        assert info.normalized == "KT0039"

    def test_lowercase_normalized(self):
        info = normalize_invoice_number("inv/2025/001")
        assert info.normalized == "INV2025001"

    def test_multiple_separators_collapsed(self):
        info = normalize_invoice_number("INV--2025//001")
        assert info.normalized == "INV2025001"

    def test_leading_zeros_preserved(self):
        info = normalize_invoice_number("00123")
        assert info.original == "00123"
        assert info.trailing_number == "00123"


# ===================================================================
# Money Parsing
# ===================================================================

class TestParseMoney:
    """Tests for monetary value parsing."""

    def test_integer(self):
        assert parse_money(50000) == Decimal("50000.00")

    def test_float(self):
        assert parse_money(83366.11) == Decimal("83366.11")

    def test_string_with_commas(self):
        assert parse_money("50,000") == Decimal("50000.00")

    def test_string_with_rupee_symbol(self):
        assert parse_money("₹50,000.00") == Decimal("50000.00")

    def test_string_plain(self):
        assert parse_money("50000") == Decimal("50000.00")

    def test_none(self):
        assert parse_money(None) == Decimal("0.00")

    def test_blank_string(self):
        assert parse_money("") == Decimal("0.00")

    def test_dash(self):
        assert parse_money("-") == Decimal("0.00")

    def test_decimal_passthrough(self):
        assert parse_money(Decimal("123.456")) == Decimal("123.46")

    def test_negative_value(self):
        assert parse_money(-500.50) == Decimal("-500.50")

    def test_nan_string(self):
        with pytest.raises(ValueError):
            parse_money("nan")

    def test_two_decimal_quantization(self):
        result = parse_money(0.005)
        assert result == Decimal("0.01") or result == Decimal("0.00")  # Rounding

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_money("not a number")

    def test_safe_parse_money_invalid(self):
        with pytest.raises(ValueError):
            safe_parse_money("not a number")

    def test_from_sample_value(self):
        """Test with actual value from sample: 83366.11"""
        assert parse_money(83366.11) == Decimal("83366.11")

    def test_from_sample_sgst(self):
        """Test with actual SGST from sample: 7502.95"""
        assert parse_money(7502.95) == Decimal("7502.95")

    def test_round_off_value(self):
        """Test with actual round-off from sample: 0.01"""
        assert parse_money(0.01) == Decimal("0.01")


# ===================================================================
# Date Parsing
# ===================================================================

class TestParseDate:
    """Tests for date parsing."""

    def test_datetime_object(self):
        from datetime import datetime
        dt = datetime(2025, 4, 2)
        parsed, orig = parse_date(dt)
        assert parsed == date(2025, 4, 2)
        assert orig == "02-04-2025"

    def test_date_object(self):
        d = date(2025, 4, 2)
        parsed, orig = parse_date(d)
        assert parsed == date(2025, 4, 2)

    def test_dd_mm_yyyy_dash(self):
        parsed, orig = parse_date("02-04-2025")
        assert parsed == date(2025, 4, 2)

    def test_dd_mm_yyyy_slash(self):
        parsed, orig = parse_date("02/04/2025")
        assert parsed == date(2025, 4, 2)

    def test_dd_mon_yyyy(self):
        parsed, orig = parse_date("02-Apr-2025")
        assert parsed == date(2025, 4, 2)

    def test_iso_format(self):
        parsed, orig = parse_date("2025-04-02")
        assert parsed == date(2025, 4, 2)

    def test_none(self):
        parsed, orig = parse_date(None)
        assert parsed is None
        assert orig == ""

    def test_empty_string(self):
        parsed, orig = parse_date("")
        assert parsed is None

    def test_preserves_original(self):
        parsed, orig = parse_date("02-04-2025")
        assert orig == "02-04-2025"

    def test_unparseable(self):
        parsed, orig = parse_date("not a date")
        assert parsed is None
        assert orig == "not a date"


# ===================================================================
# Supplier Name Normalization
# ===================================================================

class TestNormalizeSupplierName:
    """Tests for supplier name normalization."""

    def test_basic(self):
        result = normalize_supplier_name("Kumar Traders (Vivo)")
        assert "KUMAR" in result
        assert "TRADERS" in result

    def test_pvt_ltd_expansion(self):
        result = normalize_supplier_name("Bluetex Pvt Ltd")
        assert "PRIVATE" in result
        assert "LIMITED" in result

    def test_uppercase(self):
        result = normalize_supplier_name("kumar traders")
        assert result == result.upper()

    def test_none(self):
        result = normalize_supplier_name(None)
        assert result == ""

    def test_empty(self):
        result = normalize_supplier_name("")
        assert result == ""

    def test_extra_spaces(self):
        result = normalize_supplier_name("  Kumar   Traders  ")
        assert "  " not in result

    def test_punctuation_removed(self):
        result = normalize_supplier_name("Kumar (Traders) [Vivo]")
        # Punctuation should be replaced with spaces
        assert "(" not in result
        assert "[" not in result

    def test_from_sample_supplier(self):
        """Test with actual supplier from sample data."""
        result = normalize_supplier_name("Kanodia Brothers SIRSA")
        assert "KANODIA" in result
        assert "BROTHERS" in result
        assert "SIRSA" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
