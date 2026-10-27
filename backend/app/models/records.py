"""
Data models for GST Reconciliation Tool.

Defines the normalized record structures used across all phases:
- ParsedInvoiceRecord: A single normalized invoice from either Tally or GST source.
- InvoiceNumberInfo: Breakdown of an invoice number for multi-level matching.
- SheetDetectionResult: Metadata about a detected sheet and its column mappings.
- FileInspectionResult: Top-level result of inspecting an uploaded file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RecordSource(str, Enum):
    """Where a parsed record originated."""
    TALLY = "tally"
    GST = "gst"


class SheetCategory(str, Enum):
    """Semantic category assigned to a detected sheet."""
    PURCHASE_REGISTER = "purchase_register"
    DEBIT_NOTE_REGISTER = "debit_note_register"
    B2B = "b2b"
    B2BA = "b2ba"
    CDNR = "cdnr"
    CDNRA = "cdnra"
    PARTY = "party"
    UNKNOWN = "unknown"


class VoucherType(str, Enum):
    """The type of voucher / document."""
    PURCHASE = "Purchase"
    DEBIT_NOTE = "Debit Note"
    CREDIT_NOTE = "Credit Note"
    UNKNOWN = "Unknown"


def normalize_note_direction(raw: Any) -> VoucherType:
    """Normalize explicit note direction only; never infer it from a sheet."""
    value = " ".join(str(raw or "").strip().lower().split())
    if value in ("credit note", "credit", "c", "cr"):
        return VoucherType.CREDIT_NOTE
    if value in ("debit note", "debit", "d", "dr"):
        return VoucherType.DEBIT_NOTE
    return VoucherType.UNKNOWN


# ---------------------------------------------------------------------------
# Invoice Number Info
# ---------------------------------------------------------------------------

@dataclass
class InvoiceNumberInfo:
    """
    Breakdown of an invoice number for multi-level matching.
    
    original        – exactly as it appeared in the source
    normalized      – uppercased, trimmed, separators collapsed
    trailing_number – the numeric suffix extracted from the invoice number
    last_4          – last 4 characters of the normalized form
    last_2          – last 2 characters of the normalized form
    """
    original: str = ""
    normalized: str = ""
    trailing_number: str = ""
    last_4: str = ""
    last_2: str = ""

    def __repr__(self) -> str:
        return (
            f"InvoiceNumberInfo(original={self.original!r}, "
            f"normalized={self.normalized!r}, "
            f"trailing={self.trailing_number!r}, "
            f"last4={self.last_4!r}, last2={self.last_2!r})"
        )


@dataclass
class FinancialParseIssue:
    """A non-blank monetary source value that could not be parsed."""
    field_name: str = ""
    raw_value: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Parsed Invoice Record
# ---------------------------------------------------------------------------

@dataclass
class ParsedInvoiceRecord:
    """
    A single normalized invoice/note record from any source.

    All monetary values use Decimal for exact arithmetic.
    Both original and normalized GSTIN/invoice values are preserved
    for auditability.
    """
    # Source tracking
    source: RecordSource = RecordSource.TALLY
    source_sheet: str = ""
    source_row: int = 0
    sheet_category: SheetCategory = SheetCategory.UNKNOWN
    accounting_source_system: str = ""

    # Supplier
    supplier_name: str = ""
    gstin_original: str = ""
    gstin_normalized: str = ""
    gstin_valid: bool = False

    # Invoice / Note identification
    invoice_number: InvoiceNumberInfo = field(default_factory=InvoiceNumberInfo)
    invoice_date: Optional[date] = None
    invoice_date_original: str = ""

    # Canonical document type for Accounts and GST.
    voucher_type: VoucherType = VoucherType.UNKNOWN
    voucher_type_original: str = ""

    # Monetary values (all Decimal for exact arithmetic)
    taxable_value: Decimal = Decimal("0.00")
    # Preserve whether the Accounts source supplied a taxable-value cell.
    # The Decimal remains non-null so existing arithmetic stays exact.
    taxable_value_present: bool = True
    igst: Decimal = Decimal("0.00")
    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    # Cess is not part of the current reconciliation calculation, but it must
    # remain canonical evidence so later accounting exports can reject
    # unsupported cess-bearing documents instead of silently dropping it.
    cess: Decimal = Decimal("0.00")
    cess_present: bool = False
    total_invoice_value: Decimal = Decimal("0.00")
    total_invoice_value_present: bool = False

    # Additional Tally fields (preserved for reporting)
    additional_cost: Decimal = Decimal("0.00")
    gross_total: Decimal = Decimal("0.00")
    gross_total_present: bool = False
    round_off: Decimal = Decimal("0.00")

    # GST-specific fields
    gst_rate: Optional[Decimal] = None
    place_of_supply: str = ""
    reverse_charge: str = ""
    note_type: str = ""           # Credit / Debit for cdnr
    note_number: str = ""         # For cdnr note number
    note_date: Optional[date] = None
    original_invoice_number: str = ""  # cdnr links back to original invoice

    # Amendment lineage (B2BA / CDNRA).  The ordinary invoice/note fields above
    # always contain the revised, authoritative document identity.
    amendment_original_number: str = ""
    amendment_original_date: Optional[date] = None
    amendment_original_date_original: str = ""
    amendment_period: str = ""
    amendment_date: Optional[date] = None

    # Non-blank invalid amounts are retained as evidence. Decimal fields stay
    # arithmetic-safe, but must not be used for automatic classification when
    # this collection is non-empty.
    financial_parse_issues: List[FinancialParseIssue] = field(default_factory=list)

    # Raw row data for auditability
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def note_direction(self) -> VoucherType:
        # Explicit source metadata wins even over legacy forced CREDIT_NOTE.
        if self.source == RecordSource.GST:
            if self.note_type.strip():
                return normalize_note_direction(self.note_type)
            if self.sheet_category in (SheetCategory.CDNR, SheetCategory.CDNRA):
                return normalize_note_direction(self.voucher_type_original)
        return self.voucher_type if self.voucher_type in (
            VoucherType.CREDIT_NOTE, VoucherType.DEBIT_NOTE,
        ) else VoucherType.UNKNOWN

    def total_tax(self) -> Decimal:
        """Sum of IGST + CGST + SGST."""
        return self.igst + self.cgst + self.sgst

    def __repr__(self) -> str:
        inv = self.invoice_number.original or self.note_number
        return (
            f"ParsedInvoiceRecord("
            f"source={self.source.value}, "
            f"supplier={self.supplier_name!r}, "
            f"gstin={self.gstin_normalized!r}, "
            f"invoice={inv!r}, "
            f"taxable={self.taxable_value}, "
            f"igst={self.igst}, cgst={self.cgst}, sgst={self.sgst})"
        )


# ---------------------------------------------------------------------------
# Column Mapping
# ---------------------------------------------------------------------------

@dataclass
class ColumnMapping:
    """
    Maps a semantic field to the actual column header found in a sheet.
    
    semantic_name – our internal field name (e.g. 'supplier_name')
    detected_header – the actual column header text found
    confidence – how confident the detection is (0.0 - 1.0)
    """
    semantic_name: str = ""
    detected_header: str = ""
    confidence: float = 0.0

    def __repr__(self) -> str:
        return (
            f"ColumnMapping({self.semantic_name!r} → "
            f"{self.detected_header!r} [{self.confidence:.0%}])"
        )


# ---------------------------------------------------------------------------
# Sheet Detection Result
# ---------------------------------------------------------------------------

@dataclass
class SheetDetectionResult:
    """Result of detecting and classifying a single sheet."""
    sheet_name: str = ""
    category: SheetCategory = SheetCategory.UNKNOWN
    header_row: int = 0           # 1-indexed row where headers were found
    data_start_row: int = 0       # 1-indexed row where data begins
    total_data_rows: int = 0
    headers: List[str] = field(default_factory=list)
    column_mappings: List[ColumnMapping] = field(default_factory=list)
    confidence: float = 0.0
    candidate_header_rows: List[int] = field(default_factory=list)

    def get_mapping(self, semantic_name: str) -> Optional[ColumnMapping]:
        """Get the column mapping for a given semantic field name."""
        for m in self.column_mappings:
            if m.semantic_name == semantic_name:
                return m
        return None

    def get_header_for(self, semantic_name: str) -> Optional[str]:
        """Return the detected header text for a semantic field, or None."""
        mapping = self.get_mapping(semantic_name)
        return mapping.detected_header if mapping else None


@dataclass
class PersistedSheetRows:
    """Canonical, file-independent source rows retained after inspection."""
    sheet_name: str = ""
    category: SheetCategory = SheetCategory.UNKNOWN
    headers: List[Any] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    row_numbers: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# File Inspection Result
# ---------------------------------------------------------------------------

@dataclass
class FileInspectionResult:
    """Top-level result of inspecting an uploaded file."""
    file_name: str = ""
    file_type: str = ""           # 'xlsx', 'xls', 'json'
    file_size_bytes: int = 0
    sheets: List[SheetDetectionResult] = field(default_factory=list)
    records: List[ParsedInvoiceRecord] = field(default_factory=list)
    persisted_sheets: List[PersistedSheetRows] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def total_records(self) -> int:
        return len(self.records)

    def records_by_source(self, source: RecordSource) -> List[ParsedInvoiceRecord]:
        return [r for r in self.records if r.source == source]

    def records_by_category(self, category: SheetCategory) -> List[ParsedInvoiceRecord]:
        return [r for r in self.records if r.sheet_category == category]
