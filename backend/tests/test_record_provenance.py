import pytest

from backend.app.models.records import (
    FileInspectionResult,
    ParsedInvoiceRecord,
    SheetCategory,
)
from backend.app.services.file_parser import _row_to_record


FILTER_CATEGORIES = (
    SheetCategory.B2B,
    SheetCategory.B2BA,
    SheetCategory.CDNR,
    SheetCategory.CDNRA,
    SheetCategory.PARTY,
    SheetCategory.UNKNOWN,
)


@pytest.fixture
def categorized_inspection():
    records = [
        ParsedInvoiceRecord(source_sheet=category.value, sheet_category=category)
        for category in FILTER_CATEGORIES
    ]
    return FileInspectionResult(records=records), records


@pytest.mark.parametrize("category", FILTER_CATEGORIES)
def test_records_by_category_returns_only_requested_category(categorized_inspection, category):
    inspection, records = categorized_inspection
    expected = next(record for record in records if record.sheet_category == category)
    assert inspection.records_by_category(category) == [expected]


def test_records_by_category_mixed_categories_do_not_leak(categorized_inspection):
    inspection, _ = categorized_inspection
    for category in FILTER_CATEGORIES:
        grouped = inspection.records_by_category(category)
        assert grouped
        assert all(record.sheet_category == category for record in grouped)


def test_records_by_category_returns_empty_list_when_absent():
    inspection = FileInspectionResult(
        records=[ParsedInvoiceRecord(sheet_category=SheetCategory.B2B)]
    )
    assert inspection.records_by_category(SheetCategory.CDNR) == []


def test_records_by_category_uses_only_authoritative_sheet_category(categorized_inspection):
    inspection, records = categorized_inspection
    assert all(not hasattr(record, "source_sheet_category") for record in records)
    assert inspection.records_by_category(SheetCategory.B2B) == [records[0]]


def test_sheet_category_survives_normal_parser_record_construction():
    record = _row_to_record(
        row_values=["06AAAAA0000A1Z5", "INV-1", "01-04-2025", 100],
        headers=["GSTIN/UIN", "Invoice No.", "Invoice Date", "Taxable Value"],
        col_map={"gstin": 0, "invoice_number": 1, "invoice_date": 2, "taxable_value": 3},
        category=SheetCategory.B2BA,
        sheet_name="b2ba",
        row_number=2,
    )
    assert record is not None
    assert record.sheet_category == SheetCategory.B2BA
    assert record.source_sheet == "b2ba"
