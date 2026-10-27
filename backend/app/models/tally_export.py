"""Canonical, validation-ready inputs for a future Tally XML exporter.

These DTOs do not contain ledger choices or Tally-version-specific XML fields.
They preserve only authoritative reconciliation evidence needed by the later
validation phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

from backend.app.models.records import (
    FinancialParseIssue,
    RecordSource,
    SheetCategory,
)


class TallyExportTransactionType(str, Enum):
    PURCHASE_INVOICE = "purchase_invoice"
    SUPPLIER_CREDIT_NOTE = "supplier_credit_note"


class TallyTaxStructure(str, Enum):
    NO_TAX = "no_tax"
    IGST_ONLY = "igst_only"
    CGST_SGST = "cgst_sgst"
    MIXED_OR_INCONSISTENT = "mixed_or_inconsistent"


class ReverseChargeStatus(str, Enum):
    YES = "yes"
    NO = "no"
    MISSING = "missing"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class TallyExportSourceIdentity:
    source: RecordSource
    source_sheet: str
    source_row: int
    sheet_category: SheetCategory


@dataclass(frozen=True)
class TallyRateAllocation:
    """Exact source totals for one GST rate within a candidate."""

    gst_rate: Decimal
    taxable_value: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal


@dataclass(frozen=True)
class TallyExportCandidate:
    transaction_type: TallyExportTransactionType
    supplier_gstin: str
    supplier_gstin_valid: bool
    supplier_name: str
    document_number: str
    document_date: Optional[date]
    original_invoice_number: str

    taxable_value: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal
    cess_present: bool
    invoice_total: Optional[Decimal]

    gst_rates: Tuple[Decimal, ...]
    rate_allocations: Tuple[TallyRateAllocation, ...]
    gst_rate_missing: bool
    place_of_supply: str
    place_of_supply_values: Tuple[str, ...]
    place_of_supply_missing: bool
    place_of_supply_conflicting: bool
    reverse_charge_status: ReverseChargeStatus
    reverse_charge_values: Tuple[str, ...]

    is_amendment: bool
    amendment_original_number: str
    amendment_original_date: Optional[date]
    is_cancelled: bool
    cancellation_evidence: Tuple[str, ...]

    source_rows: Tuple[TallyExportSourceIdentity, ...]
    financial_parse_issues: Tuple[FinancialParseIssue, ...]

    @property
    def tax_structure(self) -> TallyTaxStructure:
        has_igst = self.igst != 0
        has_cgst = self.cgst != 0
        has_sgst = self.sgst != 0
        if not (has_igst or has_cgst or has_sgst):
            return TallyTaxStructure.NO_TAX
        if has_igst and not (has_cgst or has_sgst):
            return TallyTaxStructure.IGST_ONLY
        if not has_igst and has_cgst and has_sgst and self.cgst == self.sgst:
            return TallyTaxStructure.CGST_SGST
        return TallyTaxStructure.MIXED_OR_INCONSISTENT

    @property
    def has_multiple_gst_rates(self) -> bool:
        return len(self.gst_rates) > 1

    @property
    def has_reverse_charge(self) -> bool:
        return self.reverse_charge_status == ReverseChargeStatus.YES

    @property
    def has_cess(self) -> bool:
        return self.cess != 0

    @property
    def has_unresolved_financial_issues(self) -> bool:
        return bool(self.financial_parse_issues)
