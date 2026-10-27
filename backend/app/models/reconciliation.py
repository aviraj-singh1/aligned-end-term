"""
Reconciliation data models for the GST Reconciliation Tool.

Defines:
- MatchMethod: How the match was determined (exact, normalized, last-4, etc.)
- ReconciliationStatus: Final status of a reconciled invoice
- TaxComparison: Side-by-side comparison of a single tax head
- AuditTrail: Complete audit record for one reconciliation result
- ReconciliationResult: Per-invoice reconciliation outcome
- ReconciliationSummary: Aggregate counts and overall result set
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from backend.app.models.records import ParsedInvoiceRecord


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MatchMethod(str, Enum):
    """How the invoice match was determined."""
    EXACT_INVOICE = "Exact Invoice"
    NORMALIZED_INVOICE = "Normalized Invoice"
    INVOICE_LAST_4 = "Invoice Last 4"
    INVOICE_LAST_2 = "Invoice Last 2"
    SUPPLIER_NAME_FALLBACK = "Supplier Name Fallback"
    MANUAL_MATCH = "Manual Match"
    NO_MATCH = "No Match"
    AMBIGUOUS = "Ambiguous"
    STRONG_EVIDENCE_FALLBACK = "Strong Evidence Fallback"
    INVOICE_MISMATCH = "Exact GSTIN + Date + Financials"


class ReconciliationStatus(str, Enum):
    """Final status of a reconciled invoice."""
    PERFECTLY_MATCHED = "Perfectly Matched"
    MINOR_ROUNDING_DIFFERENCE = "Minor Rounding Difference"
    MAJOR_DIFFERENCE = "Major Difference"
    MISSING_IN_GSTR = "Missing in GSTR-2B"
    NOT_IN_TALLY = "Not in Tally"
    NEEDS_REVIEW = "Needs Review / Ambiguous"
    POTENTIAL_DUPLICATE = "Potential Duplicate"
    INVOICE_NUMBER_MISMATCH = "Invoice Number Mismatch"
    TAXABLE_VALUE_MISSING = "Taxable Value Missing"


class CancelledSourceSide(str, Enum):
    ACCOUNTS = "ACCOUNTS"
    GSTR = "GSTR"


class AccountingSourceSystem(str, Enum):
    BUSY = "BUSY"
    TALLY = "TALLY"
    ACCOUNTS = "ACCOUNTS"
    GSTR = "GSTR"


@dataclass
class CancelledVoucher:
    id: str = ""
    source_side: CancelledSourceSide = CancelledSourceSide.ACCOUNTS
    source_system: AccountingSourceSystem = AccountingSourceSystem.ACCOUNTS
    supplier: str = ""
    gstin: str = ""
    invoice_number: str = ""
    invoice_date: Optional[date] = None
    taxable_value: Decimal = Decimal("0.00")
    igst: Decimal = Decimal("0.00")
    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    source_sheet: str = ""
    source_row: int = 0
    status: str = ""
    cancellation_reason: str = ""
    cancellation_evidence: str = ""
    source_rows: List[ParsedInvoiceRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tax Comparison
# ---------------------------------------------------------------------------

@dataclass
class TaxComparison:
    """
    Side-by-side comparison of a single tax head (IGST, CGST, or SGST).
    
    Difference is computed as: tally_value - gst_value (signed).
    """
    tax_head: str = ""
    tally_value: Optional[Decimal] = Decimal("0.00")
    gst_value: Optional[Decimal] = Decimal("0.00")
    difference: Optional[Decimal] = Decimal("0.00")    # None when comparison is unavailable
    abs_difference: Optional[Decimal] = Decimal("0.00")

    def __repr__(self) -> str:
        return (
            f"TaxComparison({self.tax_head}: "
            f"tally={self.tally_value}, gst={self.gst_value}, "
            f"diff={self.difference})"
        )


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------

@dataclass
class AuditTrail:
    """
    Complete audit record for one reconciliation result.
    
    Preserves every decision point for traceability.
    """
    # Source records
    tally_rows: List[ParsedInvoiceRecord] = field(default_factory=list)
    gst_rows: List[ParsedInvoiceRecord] = field(default_factory=list)

    # Invoice number decisions
    tally_invoice_original: str = ""
    tally_invoice_normalized: str = ""
    gst_invoice_original: str = ""
    gst_invoice_normalized: str = ""

    # GSTIN decision
    gstin_match: str = ""  # "exact", "missing_in_tally", "missing_in_gst", "mismatch"
    tally_gstin: str = ""
    gst_gstin: str = ""

    # Supplier decision
    supplier_match: str = ""  # "gstin_based", "name_fallback", "no_match"
    tally_supplier: str = ""
    gst_supplier: str = ""

    # Invoice match method
    invoice_match_method: MatchMethod = MatchMethod.NO_MATCH

    # Taxable value comparison
    tally_taxable: Optional[Decimal] = Decimal("0.00")
    gst_taxable: Optional[Decimal] = Decimal("0.00")
    taxable_difference: Optional[Decimal] = Decimal("0.00")

    # Accounts Gross Total compared with the GSTR Invoice Value. ``None``
    # means one side was not mapped/supplied, so no comparison was possible.
    tally_gross_total: Optional[Decimal] = None
    gst_invoice_value: Optional[Decimal] = None
    gross_total_difference: Optional[Decimal] = None

    # Tax comparisons
    igst_comparison: TaxComparison = field(
        default_factory=lambda: TaxComparison(tax_head="IGST")
    )
    cgst_comparison: TaxComparison = field(
        default_factory=lambda: TaxComparison(tax_head="CGST")
    )
    sgst_comparison: TaxComparison = field(
        default_factory=lambda: TaxComparison(tax_head="SGST")
    )

    # Matching details
    candidate_count: int = 0
    candidates: List[Any] = field(default_factory=list) # List[AggregatedGSTInvoice]
    final_status: ReconciliationStatus = ReconciliationStatus.NEEDS_REVIEW
    explanation: str = ""


# ---------------------------------------------------------------------------
# Reconciliation Result
# ---------------------------------------------------------------------------

@dataclass
class ReconciliationResult:
    """
    Per-invoice reconciliation outcome.
    
    Represents the final result of matching one Tally invoice against
    GSTR-2B data, including status, match method, and full audit trail.
    """
    # Identifiers
    supplier_gstin: str = ""
    supplier_tally: str = ""
    supplier_gst: str = ""
    invoice_number_tally: str = ""
    invoice_number_gst: str = ""
    invoice_date: Optional[date] = None

    # Monetary summary
    taxable_value: Decimal = Decimal("0.00")
    taxable_value_missing: bool = False
    cgst_diff: Optional[Decimal] = Decimal("0.00")
    sgst_diff: Optional[Decimal] = Decimal("0.00")
    igst_diff: Optional[Decimal] = Decimal("0.00")
    gross_total_diff: Optional[Decimal] = None

    # Date Mismatch fields
    date_mismatch: bool = False
    date_difference_days: Optional[int] = None
    tally_date: Optional[date] = None
    gstr_date: Optional[date] = None

    # Invoice Mismatch fields
    invoice_mismatch: bool = False

    # Result
    status: ReconciliationStatus = ReconciliationStatus.NEEDS_REVIEW
    match_method: MatchMethod = MatchMethod.NO_MATCH
    remarks: str = ""
    cancelled_gstr_counterpart: bool = False
    cancelled_gstr_source_rows: List[int] = field(default_factory=list)
    cancelled_gstr_source_sheet: str = ""

    # Audit
    audit: AuditTrail = field(default_factory=AuditTrail)
    # Additive domain marker; kept last to preserve legacy positional callers.
    reconciliation_type: str = "PURCHASE"

    def refresh_source_values(self) -> None:
        """Populate reporting values from actual sides, without changing decisions.

        Review candidates are not a matched GST side. Their values stay in the
        candidate list until a user explicitly chooses one.
        """
        a, g = self.audit.tally_rows, self.audit.gst_rows
        if not a and not g:
            # Legacy externally constructed reports may carry only explicit
            # comparisons. Without source evidence, do not overwrite them.
            return
        self.tally_date = (a[0].note_date or a[0].invoice_date) if a else None
        self.gstr_date = (g[0].note_date or g[0].invoice_date) if g else None
        self.audit.tally_taxable = sum((r.taxable_value for r in a), Decimal(0)) if a else None
        self.audit.gst_taxable = sum((r.taxable_value for r in g), Decimal(0)) if g else None
        if not (a and g):
            self.audit.taxable_difference = None
            self.audit.gross_total_difference = None
            self.gross_total_diff = None
        for head in ("igst", "cgst", "sgst"):
            comparison = getattr(self.audit, f"{head}_comparison")
            comparison.tally_value = sum((getattr(r, head) for r in a), Decimal(0)) if a else None
            comparison.gst_value = sum((getattr(r, head) for r in g), Decimal(0)) if g else None
            if not (a and g):
                comparison.difference = comparison.abs_difference = None
                setattr(self, f"{head}_diff", None)

    @property
    def taxable_difference(self) -> Optional[Decimal]:
        if not self.audit.tally_rows or not self.audit.gst_rows or self.taxable_value_missing:
            return None
        return self.audit.taxable_difference

    def __repr__(self) -> str:
        return (
            f"ReconciliationResult("
            f"gstin={self.supplier_gstin!r}, "
            f"tally_inv={self.invoice_number_tally!r}, "
            f"gst_inv={self.invoice_number_gst!r}, "
            f"status={self.status.value!r}, "
            f"method={self.match_method.value!r})"
        )


# ---------------------------------------------------------------------------
# Aggregated GST Invoice
# ---------------------------------------------------------------------------

@dataclass
class AggregatedGSTInvoice:
    """
    A GST invoice that may have been aggregated from multiple rows
    (different tax rates / line items for the same GSTIN + invoice).
    
    Holds the summed monetary values and references to source rows.
    """
    gstin: str = ""
    supplier_name: str = ""
    invoice_number_original: str = ""
    invoice_number_normalized: str = ""
    invoice_number_trailing: str = ""
    invoice_number_last_4: str = ""
    invoice_number_last_2: str = ""
    invoice_date: Optional[date] = None

    taxable_value: Decimal = Decimal("0.00")
    igst: Decimal = Decimal("0.00")
    cgst: Decimal = Decimal("0.00")
    sgst: Decimal = Decimal("0.00")
    cess: Decimal = Decimal("0.00")
    cess_present: bool = False
    total_invoice_value: Decimal = Decimal("0.00")
    total_invoice_value_present: bool = False

    source_rows: List[ParsedInvoiceRecord] = field(default_factory=list)
    financial_parse_issues: List[Any] = field(default_factory=list)
    consumed: bool = False   # One-to-one: marked True once matched

    def total_tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst

    def __repr__(self) -> str:
        return (
            f"AggregatedGSTInvoice("
            f"gstin={self.gstin!r}, "
            f"inv={self.invoice_number_original!r}, "
            f"taxable={self.taxable_value}, "
            f"consumed={self.consumed})"
        )


# ---------------------------------------------------------------------------
# Reconciliation Summary
# ---------------------------------------------------------------------------

@dataclass
class ReconciliationSummary:
    """
    Aggregate counts and the complete result set from a reconciliation run.
    """
    total_tally_invoices: int = 0
    total_gst_invoices: int = 0
    perfectly_matched: int = 0
    minor_differences: int = 0
    major_differences: int = 0
    missing_in_gstr: int = 0
    not_in_tally: int = 0
    needs_review: int = 0
    potential_duplicates: int = 0
    invoice_mismatches: int = 0
    date_mismatches: int = 0
    active_tally_invoices: int = 0
    active_gst_invoices: int = 0
    cancelled_accounts_vouchers: int = 0
    cancelled_gstr_vouchers: int = 0
    taxable_value_missing: int = 0

    results: List[ReconciliationResult] = field(default_factory=list)
    not_in_tally_records: List[AggregatedGSTInvoice] = field(default_factory=list)
    cancelled_vouchers: List[CancelledVoucher] = field(default_factory=list)

    def compute_counts(self) -> None:
        """Recompute summary counts from the results list."""
        self.perfectly_matched = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.PERFECTLY_MATCHED
        )
        self.minor_differences = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE
        )
        self.major_differences = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.MAJOR_DIFFERENCE
        )
        self.taxable_value_missing = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.TAXABLE_VALUE_MISSING
        )
        self.missing_in_gstr = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.MISSING_IN_GSTR
        )
        # Filter not_in_tally_records to exclude candidates that were consumed
        # by manual matching after the initial reconciliation
        self.not_in_tally_records = [
            rec for rec in self.not_in_tally_records if not rec.consumed
        ]
        # Also update any NOT_IN_TALLY result entries whose underlying record
        # was consumed via manual match
        remaining_not_in_tally_invoices = {
            rec.invoice_number_original for rec in self.not_in_tally_records
        }
        for r in self.results:
            if r.status == ReconciliationStatus.NOT_IN_TALLY:
                if r.audit and r.audit.gst_rows:
                    # Check if this NOT_IN_TALLY result references a now-consumed record
                    gst_inv = r.audit.gst_invoice_original
                    if gst_inv and gst_inv not in remaining_not_in_tally_invoices:
                        r.status = ReconciliationStatus.PERFECTLY_MATCHED
                        r.remarks = "Resolved via manual match"
        self.not_in_tally = len(self.not_in_tally_records)
        self.needs_review = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.NEEDS_REVIEW
        )
        self.potential_duplicates = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.POTENTIAL_DUPLICATE
        )
        self.date_mismatches = sum(
            1 for r in self.results
            if getattr(r, 'date_mismatch', False)
        )
        self.invoice_mismatches = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH
        )
        self.cancelled_accounts_vouchers = sum(
            1 for voucher in self.cancelled_vouchers
            if voucher.source_side == CancelledSourceSide.ACCOUNTS
        )
        self.cancelled_gstr_vouchers = sum(
            1 for voucher in self.cancelled_vouchers
            if voucher.source_side == CancelledSourceSide.GSTR
        )

    @property
    def cancelled_voucher_count(self) -> int:
        return len(self.cancelled_vouchers)
