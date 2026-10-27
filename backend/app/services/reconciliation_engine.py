"""
Purchase Invoice Reconciliation Engine for GST Reconciliation Tool.

Implements the complete matching pipeline:
1. Aggregate multi-line GST invoices
2. Match Tally invoices against GST invoices using priority chain
3. Classify results by status
4. Preserve full audit trail

Matching priority:
  GSTIN exact → Supplier name fallback (if GSTIN missing)
  → Exact invoice → Normalized invoice → Last-4 → Last-2
  → Taxable value confirmation → Tax confirmation
  → Unique candidate validation

One-to-one: Once a GST invoice is consumed, it cannot be reused.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Set, Tuple

from rapidfuzz import fuzz

from backend.app.models.records import (
    InvoiceNumberInfo,
    ParsedInvoiceRecord,
    RecordSource,
    SheetCategory,
    VoucherType,
)
from backend.app.models.reconciliation import (
    AggregatedGSTInvoice,
    AuditTrail,
    MatchMethod,
    ReconciliationResult,
    ReconciliationStatus,
    ReconciliationSummary,
    TaxComparison,
)
from backend.app.services.normalizer import normalize_supplier_name
from backend.app.services.cancellation_detector import build_cancelled_voucher


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ZERO = Decimal("0.00")
_ONE_RUPEE = Decimal("1.00")
_MINOR_THRESHOLD = _ONE_RUPEE  # ₹0.01–₹1.00 = minor rounding
_SUPPLIER_FUZZY_THRESHOLD = 75  # RapidFuzz score threshold


# ---------------------------------------------------------------------------
# GST Invoice Aggregation
# ---------------------------------------------------------------------------

def aggregate_gst_invoices(
    gst_records: List[ParsedInvoiceRecord],
) -> List[AggregatedGSTInvoice]:
    """
    Aggregate multiple GST rows into invoice-level records.

    Groups by (GSTIN_normalized, invoice_number_normalized, invoice_date).
    Sums taxable_value, IGST, CGST, SGST.
    """
    groups: Dict[Tuple[str, str, Optional], AggregatedGSTInvoice] = {}

    for rec in gst_records:
        key = (
            rec.gstin_normalized,
            rec.invoice_number.normalized,
            rec.invoice_date,
        )

        if key not in groups:
            agg = AggregatedGSTInvoice(
                gstin=rec.gstin_normalized,
                supplier_name=rec.supplier_name,
                invoice_number_original=rec.invoice_number.original,
                invoice_number_normalized=rec.invoice_number.normalized,
                invoice_number_trailing=rec.invoice_number.trailing_number,
                invoice_number_last_4=rec.invoice_number.last_4,
                invoice_number_last_2=rec.invoice_number.last_2,
                invoice_date=rec.invoice_date,
                total_invoice_value=rec.total_invoice_value,
                total_invoice_value_present=rec.total_invoice_value_present,
            )
            groups[key] = agg

        agg = groups[key]
        if rec.total_invoice_value_present and not agg.total_invoice_value_present:
            agg.total_invoice_value = rec.total_invoice_value
            agg.total_invoice_value_present = True
        agg.taxable_value += rec.taxable_value
        agg.igst += rec.igst
        agg.cgst += rec.cgst
        agg.sgst += rec.sgst
        agg.cess += rec.cess
        agg.cess_present = agg.cess_present or rec.cess_present
        agg.source_rows.append(rec)
        agg.financial_parse_issues.extend(rec.financial_parse_issues)

    return list(groups.values())


# ---------------------------------------------------------------------------
# Tax Comparison Helpers
# ---------------------------------------------------------------------------

def _make_tax_comparison(
    head: str, tally_val: Decimal, gst_val: Decimal,
) -> TaxComparison:
    diff = tally_val - gst_val
    return TaxComparison(
        tax_head=head,
        tally_value=tally_val,
        gst_value=gst_val,
        difference=diff,
        abs_difference=abs(diff),
    )


def _classify_status(
    taxable_diff: Decimal,
    igst_cmp: TaxComparison,
    cgst_cmp: TaxComparison,
    sgst_cmp: TaxComparison,
    gross_total_diff: Optional[Decimal] = None,
) -> ReconciliationStatus:
    """Classify match status from tax comparisons."""
    differences = [
        igst_cmp.abs_difference,
        cgst_cmp.abs_difference,
        sgst_cmp.abs_difference,
        abs(taxable_diff),
    ]
    if gross_total_diff is not None:
        differences.append(abs(gross_total_diff))
    max_abs = max(differences)
    if max_abs == _ZERO:
        return ReconciliationStatus.PERFECTLY_MATCHED
    if max_abs <= _MINOR_THRESHOLD:
        return ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE
    return ReconciliationStatus.MAJOR_DIFFERENCE


# ---------------------------------------------------------------------------
# Supplier Matching
# ---------------------------------------------------------------------------

def _gstin_groups(
    invoices: List[AggregatedGSTInvoice],
) -> Dict[str, List[AggregatedGSTInvoice]]:
    """Group GST invoices by normalized GSTIN."""
    groups: Dict[str, List[AggregatedGSTInvoice]] = {}
    for inv in invoices:
        groups.setdefault(inv.gstin, []).append(inv)
    return groups


def _fuzzy_supplier_match(
    tally_name: str,
    gst_invoices: List[AggregatedGSTInvoice],
) -> List[AggregatedGSTInvoice]:
    """
    Find GST invoices whose supplier name fuzzy-matches the Tally name.
    Returns candidates above the threshold. Does NOT finalize a match.
    """
    tally_norm = normalize_supplier_name(tally_name)
    if not tally_norm:
        return []

    candidates = []
    for inv in gst_invoices:
        gst_norm = normalize_supplier_name(inv.supplier_name)
        if not gst_norm:
            continue
        score = fuzz.token_sort_ratio(tally_norm, gst_norm)
        if score >= _SUPPLIER_FUZZY_THRESHOLD:
            candidates.append(inv)
    return candidates


def _prioritize_supplier_then_date(
    tally: ParsedInvoiceRecord,
    candidates: List[AggregatedGSTInvoice],
) -> List[AggregatedGSTInvoice]:
    """Resolve otherwise-equal candidates using the requested identity order.

    GSTIN has already selected the candidate pool. Supplier is the next
    discriminator, followed by invoice date. A missing or differently formatted
    supplier never discards a sole, otherwise-safe invoice match.
    """
    if len(candidates) <= 1:
        return candidates
    supplier_matches = _fuzzy_supplier_match(tally.supplier_name, candidates)
    narrowed = supplier_matches or candidates
    if len(narrowed) > 1 and tally.invoice_date:
        date_matches = [candidate for candidate in narrowed if candidate.invoice_date == tally.invoice_date]
        if date_matches:
            narrowed = date_matches
    return narrowed


def _find_strong_evidence_fallback(
    tally: ParsedInvoiceRecord,
    all_gst: List[AggregatedGSTInvoice],
) -> List[AggregatedGSTInvoice]:
    """
    Finds a Strong Document Evidence fallback candidate when supplier identity fails.
    Requires: EXACT normalized invoice number, exactly ONE unused candidate globally,
    and financial values matching within Minor Difference tolerance.
    """
    tally_norm_inv = tally.invoice_number.normalized
    if not tally_norm_inv:
        return []

    # Find unused GST invoices with this exact normalized invoice number
    unused_matches = [g for g in all_gst if not g.consumed and g.invoice_number_normalized == tally_norm_inv]
    
    if len(unused_matches) != 1:
        return []
    
    candidate = unused_matches[0]

    # Verify financial values (Must be Perfect or Minor, no Major differences allowed)
    taxable_diff = abs(tally.taxable_value - candidate.taxable_value)
    igst_cmp = _make_tax_comparison("igst", tally.igst, candidate.igst)
    cgst_cmp = _make_tax_comparison("cgst", tally.cgst, candidate.cgst)
    sgst_cmp = _make_tax_comparison("sgst", tally.sgst, candidate.sgst)

    gross_diff = (
        tally.gross_total - candidate.total_invoice_value
        if tally.gross_total_present and candidate.total_invoice_value_present else None
    )
    status = _classify_status(taxable_diff, igst_cmp, cgst_cmp, sgst_cmp, gross_diff)
    if status == ReconciliationStatus.MAJOR_DIFFERENCE:
        return []

    # Return as candidate for Needs Review
    return [candidate]


# ---------------------------------------------------------------------------
# Invoice Matching within a supplier group
# ---------------------------------------------------------------------------

def _find_exact_match(
    tally_inv: str, candidates: List[AggregatedGSTInvoice],
) -> List[AggregatedGSTInvoice]:
    """Exact original invoice number match."""
    return [c for c in candidates if not c.consumed
            and c.invoice_number_original.strip() == tally_inv.strip()
            and tally_inv.strip()]


def _find_normalized_match(
    tally_norm: str, candidates: List[AggregatedGSTInvoice],
) -> List[AggregatedGSTInvoice]:
    """Normalized invoice number match."""
    return [c for c in candidates if not c.consumed
            and c.invoice_number_normalized == tally_norm
            and tally_norm]


def _find_last_n_match(
    tally: ParsedInvoiceRecord,
    tally_last_n: str,
    candidates: List[AggregatedGSTInvoice],
    n: int,
) -> List[AggregatedGSTInvoice]:
    """
    Last-N matching with mandatory date and financial confirmation.
    
    Only returns candidates where:
    - last N chars match
    - both invoice dates exist and match exactly
    - financial confirmation for Last-2 and supplier-fallback Last-4
    - invoice is not consumed
    """
    if not tally_last_n or len(tally_last_n) < n:
        return []

    # Weak suffix identity is never sufficient when the Tally date is absent.
    if not tally.invoice_date:
        return []

    matches = []
    for c in candidates:
        if c.consumed:
            continue
        gst_last_n = c.invoice_number_last_4 if n == 4 else c.invoice_number_last_2
        if gst_last_n != tally_last_n:
            continue
        
        # Both dates are mandatory and there is zero tolerance.
        if not c.invoice_date or tally.invoice_date != c.invoice_date:
            continue

        # Exact GSTIN + Last-4 + exact date is sufficient document identity;
        # financial differences then classify the match, including Major.
        # Supplier-fallback Last-4 and every Last-2 match remain weak enough
        # to require Perfect/Minor financial confirmation before selection.
        requires_financial_confirmation = n == 2 or not tally.gstin_normalized
        if requires_financial_confirmation:
            taxable_diff = tally.taxable_value - c.taxable_value
            igst_cmp = _make_tax_comparison("IGST", tally.igst, c.igst)
            cgst_cmp = _make_tax_comparison("CGST", tally.cgst, c.cgst)
            sgst_cmp = _make_tax_comparison("SGST", tally.sgst, c.sgst)
            gross_diff = (
                tally.gross_total - c.total_invoice_value
                if tally.gross_total_present and c.total_invoice_value_present else None
            )
            if _classify_status(taxable_diff, igst_cmp, cgst_cmp, sgst_cmp, gross_diff) == ReconciliationStatus.MAJOR_DIFFERENCE:
                continue

        matches.append(c)
    return matches


# ---------------------------------------------------------------------------
# Invoice Number Mismatch Detection
# ---------------------------------------------------------------------------

def _find_invoice_mismatch_candidates(
    tally: ParsedInvoiceRecord,
    candidates: List[AggregatedGSTInvoice],
    tally_purchases: List[ParsedInvoiceRecord],
) -> List[AggregatedGSTInvoice]:
    """
    Find Invoice Number Mismatch candidates.
    Requires:
    - Tally GSTIN is present
    - Exactly matching GSTIN
    - Exactly matching Date
    - Exactly matching financials (Taxable, IGST, CGST, SGST)
    - Tally signature is unique in Tally (reciprocal uniqueness)
    """
    if not tally.gstin_normalized or not tally.invoice_date:
        return []

    # Reciprocal uniqueness: Must be only 1 Tally row with this signature
    tally_sig_count = sum(
        1 for t in tally_purchases
        if t.gstin_normalized == tally.gstin_normalized
        and t.invoice_date == tally.invoice_date
        and t.taxable_value == tally.taxable_value
        and t.igst == tally.igst
        and t.cgst == tally.cgst
        and t.sgst == tally.sgst
    )
    if tally_sig_count != 1:
        return []

    matches = []
    for c in candidates:
        if c.consumed:
            continue
        if not c.invoice_date or c.invoice_date != tally.invoice_date:
            continue
        if c.taxable_value != tally.taxable_value:
            continue
        if c.igst != tally.igst:
            continue
        if c.cgst != tally.cgst:
            continue
        if c.sgst != tally.sgst:
            continue
        matches.append(c)
    return matches


# ---------------------------------------------------------------------------
# Duplicate Detection
# ---------------------------------------------------------------------------

def _detect_tally_duplicates(
    tally_records: List[ParsedInvoiceRecord],
) -> Dict[str, List[ParsedInvoiceRecord]]:
    """
    Group Tally records by (GSTIN + normalized invoice number).
    Returns only groups with more than one record (potential duplicates).
    """
    groups: Dict[str, List[ParsedInvoiceRecord]] = {}
    for rec in tally_records:
        key = f"{rec.gstin_normalized}|{rec.invoice_number.normalized}"
        groups.setdefault(key, []).append(rec)
    return {k: v for k, v in groups.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Build Result Helper
# ---------------------------------------------------------------------------

def _build_result(
    tally: ParsedInvoiceRecord,
    gst: Optional[AggregatedGSTInvoice],
    method: MatchMethod,
    status: ReconciliationStatus,
    explanation: str,
    supplier_match_type: str = "gstin_based",
    candidate_count: int = 0,
    candidates: Optional[List[Any]] = None,
) -> ReconciliationResult:
    """Build a ReconciliationResult with full audit trail."""

    result = ReconciliationResult()
    result.supplier_gstin = tally.gstin_normalized
    result.supplier_tally = tally.supplier_name
    result.invoice_number_tally = tally.invoice_number.original
    result.invoice_date = tally.invoice_date
    result.match_method = method
    result.taxable_value_missing = not tally.taxable_value_present

    audit = AuditTrail()
    audit.tally_rows = [tally]
    audit.tally_invoice_original = tally.invoice_number.original
    audit.tally_invoice_normalized = tally.invoice_number.normalized
    audit.tally_gstin = tally.gstin_normalized
    audit.tally_supplier = tally.supplier_name
    audit.tally_taxable = tally.taxable_value
    audit.invoice_match_method = method
    audit.candidate_count = candidate_count
    audit.candidates = candidates or []
    audit.supplier_match = supplier_match_type

    if gst is not None:
        result.supplier_gst = gst.supplier_name
        result.invoice_number_gst = gst.invoice_number_original
        result.supplier_gstin = gst.gstin or tally.gstin_normalized
        result.taxable_value = tally.taxable_value
        
        result.tally_date = tally.invoice_date
        result.gstr_date = gst.invoice_date
        if tally.invoice_date and gst.invoice_date and tally.invoice_date != gst.invoice_date:
            result.date_mismatch = True
            result.date_difference_days = abs((tally.invoice_date - gst.invoice_date).days)
        else:
            result.date_mismatch = False
            result.date_difference_days = 0 if (tally.invoice_date and gst.invoice_date) else None

        # Tax comparisons
        igst_cmp = _make_tax_comparison("IGST", tally.igst, gst.igst)
        cgst_cmp = _make_tax_comparison("CGST", tally.cgst, gst.cgst)
        sgst_cmp = _make_tax_comparison("SGST", tally.sgst, gst.sgst)
        taxable_diff = tally.taxable_value - gst.taxable_value
        gross_total_diff = (
            tally.gross_total - gst.total_invoice_value
            if tally.gross_total_present and gst.total_invoice_value_present else None
        )

        result.igst_diff = igst_cmp.difference
        result.cgst_diff = cgst_cmp.difference
        result.sgst_diff = sgst_cmp.difference
        result.gross_total_diff = gross_total_diff

        audit.gst_rows = gst.source_rows
        audit.gst_invoice_original = gst.invoice_number_original
        audit.gst_invoice_normalized = gst.invoice_number_normalized
        audit.gst_gstin = gst.gstin
        audit.gst_supplier = gst.supplier_name
        audit.gst_taxable = gst.taxable_value
        audit.taxable_difference = taxable_diff
        audit.tally_gross_total = tally.gross_total if tally.gross_total_present else None
        audit.gst_invoice_value = gst.total_invoice_value if gst.total_invoice_value_present else None
        audit.gross_total_difference = gross_total_diff
        audit.igst_comparison = igst_cmp
        audit.cgst_comparison = cgst_cmp
        audit.sgst_comparison = sgst_cmp

        if method != MatchMethod.AMBIGUOUS and status != ReconciliationStatus.INVOICE_NUMBER_MISMATCH:
            status = _classify_status(taxable_diff, igst_cmp, cgst_cmp, sgst_cmp, gross_total_diff)

        audit.gstin_match = (
            "exact" if tally.gstin_normalized and tally.gstin_normalized == gst.gstin
            else "missing_in_tally" if not tally.gstin_normalized
            else "mismatch"
        )
    else:
        result.taxable_value = tally.taxable_value
        audit.gstin_match = "no_gst_match"
        audit.gst_taxable = _ZERO
        audit.tally_gross_total = tally.gross_total if tally.gross_total_present else None

    # Preserve the decision evidence independently of primary-status warnings.
    # Reporting can compose both without reconstructing matching rules.
    audit.explanation = explanation

    # Cancellation is removed before this helper. For every other Accounts
    # invoice, source absence takes precedence over matching/financial status
    # while any safely identified GST candidate remains consumed.
    if not tally.taxable_value_present:
        status = ReconciliationStatus.TAXABLE_VALUE_MISSING
        explanation = "Taxable value is blank in the Accounts source."

    result.status = status
    if status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH:
        result.invoice_mismatch = True
    result.remarks = explanation
    audit.final_status = status
    result.audit = audit
    result.refresh_source_values()
    return result


def _financial_issue_explanation(record: ParsedInvoiceRecord, side: str) -> str:
    issue = record.financial_parse_issues[0]
    return (
        f"{issue.field_name} contains an invalid monetary value in the {side} source: "
        f"{issue.raw_value!r}. Review the source data before reconciliation."
    )


def _aggregated_issue_explanation(candidate: AggregatedGSTInvoice) -> str:
    issue = candidate.financial_parse_issues[0]
    return (
        f"{issue.field_name} contains an invalid monetary value in the GSTR source: "
        f"{issue.raw_value!r}. Review the source data before reconciliation."
    )


# ---------------------------------------------------------------------------
# Main Reconciliation Engine
# ---------------------------------------------------------------------------

def reconcile_purchases(
    tally_records: List[ParsedInvoiceRecord],
    gst_records: List[ParsedInvoiceRecord],
) -> ReconciliationSummary:
    """
    Reconcile Tally purchase invoices against GST B2B invoices.

    Steps:
    1. Filter to Purchase vouchers from Tally, B2B from GST
    2. Aggregate multi-line GST invoices
    3. Detect Tally duplicates
    4. For each Tally invoice, find matching GST invoice using priority chain
    5. Mark unmatched GST invoices as "Not in Tally"
    6. Build summary

    Returns ReconciliationSummary with all results and counts.
    """
    # Filter purchase populations, then remove explicitly cancelled vouchers
    # before any identity, suffix, strong-evidence or mismatch matching.
    all_tally_purchases = [
        r for r in tally_records
        if r.source == RecordSource.TALLY
        and r.voucher_type in (VoucherType.PURCHASE, VoucherType.UNKNOWN)
        and r.invoice_number.original  # Must have an invoice number
    ]
    cancelled_accounts = [v for r in all_tally_purchases if (v := build_cancelled_voucher(r))]
    cancelled_account_rows = {id(v.source_rows[0]) for v in cancelled_accounts}
    tally_purchases = [r for r in all_tally_purchases if id(r) not in cancelled_account_rows]

    # Filter: only GST source records (B2B) - Exclude notes and non-B2B sheets
    # A candidate is allowed ONLY if it comes from a semantic B2B sheet
    all_gst_b2b = [
        r for r in gst_records 
        if r.source == RecordSource.GST
        and r.voucher_type in (VoucherType.PURCHASE, VoucherType.UNKNOWN)
        and r.sheet_category in (SheetCategory.B2B, SheetCategory.B2BA)
    ]

    # Cancellation applies to the complete GST aggregation identity when any
    # constituent row carries explicit cancellation metadata.
    def _gst_key(record):
        return (record.gstin_normalized, record.invoice_number.normalized, record.invoice_date)

    cancelled_keys = {
        _gst_key(record) for record in all_gst_b2b
        if build_cancelled_voucher(record) is not None
    }
    cancelled_gst_rows = [record for record in all_gst_b2b if _gst_key(record) in cancelled_keys]
    gst_b2b = [record for record in all_gst_b2b if _gst_key(record) not in cancelled_keys]

    # Step 1: Aggregate GST invoices
    aggregated = aggregate_gst_invoices(gst_b2b)
    cancelled_gst_aggregated = aggregate_gst_invoices(cancelled_gst_rows)
    cancelled_gstr = []
    for agg in cancelled_gst_aggregated:
        voucher = build_cancelled_voucher(next(
            row for row in agg.source_rows if build_cancelled_voucher(row) is not None
        ))
        voucher.supplier = agg.supplier_name
        voucher.gstin = agg.gstin
        voucher.invoice_number = agg.invoice_number_original
        voucher.invoice_date = agg.invoice_date
        voucher.taxable_value = agg.taxable_value
        voucher.igst, voucher.cgst, voucher.sgst = agg.igst, agg.cgst, agg.sgst
        voucher.source_rows = agg.source_rows
        cancelled_gstr.append(voucher)

    # Step 2: Group GST by GSTIN
    gst_by_gstin = _gstin_groups(aggregated)

    # Step 3: Detect Tally duplicates
    tally_dupes = _detect_tally_duplicates(tally_purchases)

    summary = ReconciliationSummary()
    summary.total_tally_invoices = len(all_tally_purchases)
    summary.active_tally_invoices = len(tally_purchases)
    summary.total_gst_invoices = len(aggregated) + len(cancelled_gst_aggregated)
    summary.active_gst_invoices = len(aggregated)
    summary.cancelled_vouchers = cancelled_accounts + cancelled_gstr

    # Step 4: Match each Tally invoice
    for tally in tally_purchases:
        # Cancellation has already taken precedence above. Invalid non-blank
        # money cannot participate in identity or financial matching and must
        # not consume a GST candidate.
        if tally.financial_parse_issues and tally.taxable_value_present:
            summary.results.append(_build_result(
                tally, None, MatchMethod.NO_MATCH, ReconciliationStatus.NEEDS_REVIEW,
                _financial_issue_explanation(tally, "Accounts"),
            ))
            continue
        # Check for Tally duplicate
        dupe_key = f"{tally.gstin_normalized}|{tally.invoice_number.normalized}"
        if dupe_key in tally_dupes:
            dupe_group = tally_dupes[dupe_key]
            if len(dupe_group) > 1:
                result = _build_result(
                    tally, None, MatchMethod.NO_MATCH,
                    ReconciliationStatus.POTENTIAL_DUPLICATE,
                    f"Potential duplicate: {len(dupe_group)} Tally records "
                    f"with same GSTIN+invoice",
                    candidate_count=len(dupe_group),
                )
                summary.results.append(result)
                continue

        result = _match_single_tally_invoice(
            tally, aggregated, gst_by_gstin, tally_purchases,
        )
        summary.results.append(result)

    # Step 5: Not in Tally — unconsumed GST invoices
    for agg in aggregated:
        if not agg.consumed:
            summary.not_in_tally_records.append(agg)
            nit_result = ReconciliationResult(
                supplier_gstin=agg.gstin,
                supplier_gst=agg.supplier_name,
                invoice_number_gst=agg.invoice_number_original,
                invoice_date=agg.invoice_date,
                taxable_value=agg.taxable_value,
                status=ReconciliationStatus.NOT_IN_TALLY,
                match_method=MatchMethod.NO_MATCH,
                remarks="GST invoice not matched to any Tally record",
            )
            nit_audit = AuditTrail(
                gst_rows=agg.source_rows,
                gst_invoice_original=agg.invoice_number_original,
                gst_invoice_normalized=agg.invoice_number_normalized,
                gst_gstin=agg.gstin,
                gst_supplier=agg.supplier_name,
                gst_taxable=agg.taxable_value,
                final_status=ReconciliationStatus.NOT_IN_TALLY,
                explanation="GST invoice not matched to any Tally record",
            )
            nit_result.audit = nit_audit
            summary.results.append(nit_result)

    # Step 6: Compute counts
    from backend.app.services.result_provenance import explain_cancelled_counterpart
    for result in summary.results:
        explain_cancelled_counterpart(result, summary.cancelled_vouchers)
        result.refresh_source_values()
    summary.compute_counts()
    return summary


def _match_single_tally_invoice(
    tally: ParsedInvoiceRecord,
    all_gst: List[AggregatedGSTInvoice],
    gst_by_gstin: Dict[str, List[AggregatedGSTInvoice]],
    tally_purchases: List[ParsedInvoiceRecord],
) -> ReconciliationResult:
    """
    Match a single Tally invoice against aggregated GST invoices
    using the full priority chain.
    """
    # A malformed GST candidate is never eligible for automatic matching. If
    # it has this row's exact identity, surface the data-quality reason without
    # consuming it; otherwise continue with valid candidates only.
    invalid_exact = [
        candidate for candidate in all_gst
        if candidate.financial_parse_issues
        and candidate.gstin == tally.gstin_normalized
        and candidate.invoice_number_normalized == tally.invoice_number.normalized
    ]
    valid_exact = [
        candidate for candidate in all_gst
        if not candidate.financial_parse_issues
        and candidate.gstin == tally.gstin_normalized
        and candidate.invoice_number_normalized == tally.invoice_number.normalized
        and not candidate.consumed
    ]
    if invalid_exact and not valid_exact:
        return _build_result(
            tally, None, MatchMethod.AMBIGUOUS, ReconciliationStatus.NEEDS_REVIEW,
            _aggregated_issue_explanation(invalid_exact[0]),
            supplier_match_type="gstin_based", candidate_count=len(invalid_exact),
            candidates=invalid_exact,
        )

    matchable_gst = [candidate for candidate in all_gst if not candidate.financial_parse_issues]
    # Determine candidate pool based on GSTIN
    candidates: List[AggregatedGSTInvoice] = []
    supplier_match_type = "no_match"

    if tally.gstin_normalized:
        # GSTIN exists → exact match only
        candidates = [c for c in gst_by_gstin.get(tally.gstin_normalized, []) if not c.financial_parse_issues]
        supplier_match_type = "gstin_based" if candidates else "gstin_no_match"
    else:
        # GSTIN missing → supplier name fallback
        candidates = _fuzzy_supplier_match(tally.supplier_name, matchable_gst)
        supplier_match_type = "name_fallback" if candidates else "no_match"

    # Filter out already-consumed
    available = [c for c in candidates if not c.consumed]

    if not available:
        # Strong Document Evidence Fallback
        if not tally.gstin_normalized and tally.invoice_number.normalized:
            fallback_candidates = _find_strong_evidence_fallback(tally, matchable_gst)
            if fallback_candidates:
                fc = fallback_candidates[0]
                t_date = tally.invoice_date
                g_date = fc.invoice_date
                date_delta = abs((t_date - g_date).days) if t_date and g_date else None
                
                if t_date and g_date and date_delta == 0:
                    explanation = "Strong Review Candidate: Exact invoice, unique candidate, matching financials, and exact date match."
                elif t_date and g_date:
                    explanation = f"Review Candidate with Date Warning: Exact invoice, unique candidate, matching financials, but dates differ by {date_delta} days ({t_date} vs {g_date})."
                else:
                    explanation = "Review Candidate with Date Warning: Exact invoice, unique candidate, matching financials, but one or both dates are missing."

                return _build_result(
                    tally, None, MatchMethod.STRONG_EVIDENCE_FALLBACK,
                    ReconciliationStatus.NEEDS_REVIEW,
                    explanation,
                    supplier_match_type="strong_evidence_fallback",
                    candidate_count=len(fallback_candidates),
                    candidates=fallback_candidates,
                )

        return _build_result(
            tally, None, MatchMethod.NO_MATCH,
            ReconciliationStatus.MISSING_IN_GSTR,
            "No matching GST invoice found for this GSTIN/supplier",
            supplier_match_type=supplier_match_type,
            candidate_count=0,
        )

    inv_info = tally.invoice_number

    # Priority A: Exact original invoice number
    exact = _prioritize_supplier_then_date(
        tally, _find_exact_match(inv_info.original, available)
    )
    if len(exact) == 1:
        exact[0].consumed = True
        return _build_result(
            tally, exact[0], MatchMethod.EXACT_INVOICE,
            ReconciliationStatus.PERFECTLY_MATCHED,  # Will be reclassified
            "Exact invoice number match",
            supplier_match_type=supplier_match_type,
            candidate_count=1,
        )
    if len(exact) > 1:
        return _build_result(
            tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Multiple exact invoice matches found ({len(exact)} candidates)",
            supplier_match_type=supplier_match_type,
            candidate_count=len(exact),
            candidates=exact,
        )

    # Priority B: Normalized invoice number
    norm = _prioritize_supplier_then_date(
        tally, _find_normalized_match(inv_info.normalized, available)
    )
    if len(norm) == 1:
        norm[0].consumed = True
        return _build_result(
            tally, norm[0], MatchMethod.NORMALIZED_INVOICE,
            ReconciliationStatus.PERFECTLY_MATCHED,
            f"Normalized invoice match: "
            f"Tally '{inv_info.original}' ↔ GST '{norm[0].invoice_number_original}'",
            supplier_match_type=supplier_match_type,
            candidate_count=1,
        )
    if len(norm) > 1:
        return _build_result(
            tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Multiple normalized invoice matches ({len(norm)} candidates)",
            supplier_match_type=supplier_match_type,
            candidate_count=len(norm),
            candidates=norm,
        )

    # Priority C: Last 4 digits (with date confirmation)
    last4 = _prioritize_supplier_then_date(
        tally, _find_last_n_match(tally, inv_info.last_4, available, 4)
    )
    if len(last4) == 1:
        last4[0].consumed = True
        return _build_result(
            tally, last4[0], MatchMethod.INVOICE_LAST_4,
            ReconciliationStatus.PERFECTLY_MATCHED,
            ( "Matched by unique Last-4 invoice identity using exact GSTIN and exact invoice date. Financial differences are reported separately: "
              if tally.gstin_normalized else "Last-4 match with supplier, date and financial confirmation: " ) +
            f"Tally '{inv_info.original}' ↔ GST '{last4[0].invoice_number_original}'",
            supplier_match_type=supplier_match_type,
            candidate_count=1,
        )
    if len(last4) > 1:
        return _build_result(
            tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Last-4 digits match but {len(last4)} candidates with similar values",
            supplier_match_type=supplier_match_type,
            candidate_count=len(last4),
            candidates=last4,
        )

    # Priority D: Last 2 digits (with date confirmation)
    last2 = _prioritize_supplier_then_date(
        tally, _find_last_n_match(tally, inv_info.last_2, available, 2)
    )
    if len(last2) == 1:
        last2[0].consumed = True
        return _build_result(
            tally, last2[0], MatchMethod.INVOICE_LAST_2,
            ReconciliationStatus.PERFECTLY_MATCHED,
            ( "Last-2 match with date and financial confirmation: " if tally.taxable_value_present
              else "Last-2 identity selected; Accounts taxable value is missing, so taxable confirmation is unavailable: " ) +
            f"Tally '{inv_info.original}' ↔ GST '{last2[0].invoice_number_original}'",
            supplier_match_type=supplier_match_type,
            candidate_count=1,
        )
    if len(last2) > 1:
        return _build_result(
            tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Last-2 digits match but {len(last2)} candidates with similar values",
            supplier_match_type=supplier_match_type,
            candidate_count=len(last2),
            candidates=last2,
        )

    # Priority E: Invoice Number Mismatch (Exact GSTIN, Date, Financials)
    if tally.gstin_normalized and tally.invoice_date:
        inv_mismatch_cands = _prioritize_supplier_then_date(
            tally, _find_invoice_mismatch_candidates(
                tally, available, tally_purchases
            )
        )
        if len(inv_mismatch_cands) == 1:
            inv_mismatch_cands[0].consumed = True
            return _build_result(
                tally, inv_mismatch_cands[0], MatchMethod.INVOICE_MISMATCH,
                ReconciliationStatus.INVOICE_NUMBER_MISMATCH,
                "Exact GSTIN, date and financial values match, but invoice numbers differ.",
                supplier_match_type=supplier_match_type,
                candidate_count=1,
            )
        if len(inv_mismatch_cands) > 1:
            return _build_result(
                tally, None, MatchMethod.AMBIGUOUS,
                ReconciliationStatus.NEEDS_REVIEW,
                f"Invoice Number Mismatch criteria met, but {len(inv_mismatch_cands)} candidates exist.",
                supplier_match_type=supplier_match_type,
                candidate_count=len(inv_mismatch_cands),
                candidates=inv_mismatch_cands,
            )

    # No match found
    return _build_result(
        tally, None, MatchMethod.NO_MATCH,
        ReconciliationStatus.MISSING_IN_GSTR,
        "No matching GST invoice found after all matching attempts",
        supplier_match_type=supplier_match_type,
        candidate_count=0,
    )
