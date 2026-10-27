"""
Credit Note ↔ Debit Note Reconciliation Engine.

Matches GST Supplier Credit Notes (from CDNR/CDNRA sheets) against
Tally Debit Notes using the same priority-chain principles as purchase
invoice reconciliation, but kept logically separate.

Matching priority:
  GSTIN exact → Supplier name fallback (if GSTIN missing)
  → Exact note number → Normalized → Last-4 → Last-2
  → Taxable + tax value confirmation → Unique candidate validation

One-to-one: consumed GST credit notes cannot be reused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from backend.app.models.records import (
    ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType,
)
from backend.app.models.reconciliation import (
    AggregatedGSTInvoice, AuditTrail, MatchMethod,
    ReconciliationResult, ReconciliationStatus, TaxComparison,
)
from backend.app.services.normalizer import (
    normalize_invoice_number, normalize_supplier_name,
)

_ZERO = Decimal("0.00")
_ONE_RUPEE = Decimal("1.00")
_SUPPLIER_FUZZY_THRESHOLD = 75


# ---------------------------------------------------------------------------
# Aggregated GST Credit Note
# ---------------------------------------------------------------------------

@dataclass
class AggregatedGSTCreditNote:
    """A GST credit note, possibly aggregated from multiple CDNR rows."""
    gstin: str = ""
    supplier_name: str = ""
    note_number_original: str = ""
    note_number_normalized: str = ""
    note_number_trailing: str = ""
    note_number_last_4: str = ""
    note_number_last_2: str = ""
    note_date: Optional[date] = None
    note_type: str = ""
    original_invoice_number: str = ""

    taxable_value: Decimal = _ZERO
    igst: Decimal = _ZERO
    cgst: Decimal = _ZERO
    sgst: Decimal = _ZERO
    cess: Decimal = _ZERO
    cess_present: bool = False
    total_invoice_value: Decimal = _ZERO
    total_invoice_value_present: bool = False

    source_rows: List[ParsedInvoiceRecord] = field(default_factory=list)
    financial_parse_issues: List[object] = field(default_factory=list)
    consumed: bool = False

    def total_tax(self) -> Decimal:
        return self.igst + self.cgst + self.sgst


# ---------------------------------------------------------------------------
# Notes Reconciliation Summary
# ---------------------------------------------------------------------------

@dataclass
class NotesReconciliationSummary:
    """Aggregate counts for notes reconciliation."""
    total_tally_debit_notes: int = 0
    total_gst_credit_notes: int = 0
    perfectly_matched: int = 0
    minor_differences: int = 0
    major_differences: int = 0
    missing_in_gst: int = 0
    not_in_tally: int = 0
    needs_review: int = 0
    potential_duplicates: int = 0

    results: List[ReconciliationResult] = field(default_factory=list)
    not_in_tally_notes: List[AggregatedGSTCreditNote] = field(default_factory=list)

    def compute_counts(self) -> None:
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
        self.missing_in_gst = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.MISSING_IN_GSTR
        )
        self.not_in_tally = len(self.not_in_tally_notes)
        self.needs_review = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.NEEDS_REVIEW
        )
        self.potential_duplicates = sum(
            1 for r in self.results
            if r.status == ReconciliationStatus.POTENTIAL_DUPLICATE
        )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_gst_credit_notes(
    gst_records: List[ParsedInvoiceRecord],
) -> List[AggregatedGSTCreditNote]:
    """
    Aggregate CDNR rows into note-level records.
    Groups by (GSTIN, note_number_normalized, note_date).
    Only includes records that are Credit Notes from CDNR/CDNRA sources.
    """
    groups: Dict[Tuple, AggregatedGSTCreditNote] = {}

    for rec in gst_records:
        # Must be a credit note from GST source
        if rec.source != RecordSource.GST:
            continue
        if rec.note_direction != VoucherType.CREDIT_NOTE:
            continue

        # Use note_number if available, fall back to invoice_number
        note_raw = rec.note_number or rec.invoice_number.original
        if not note_raw:
            continue
        note_info = normalize_invoice_number(note_raw)

        key = (rec.gstin_normalized, note_info.normalized, rec.note_date)

        if key not in groups:
            groups[key] = AggregatedGSTCreditNote(
                gstin=rec.gstin_normalized,
                supplier_name=rec.supplier_name,
                note_number_original=note_info.original,
                note_number_normalized=note_info.normalized,
                note_number_trailing=note_info.trailing_number,
                note_number_last_4=note_info.last_4,
                note_number_last_2=note_info.last_2,
                note_date=rec.note_date or rec.invoice_date,
                note_type=rec.note_type,
                original_invoice_number=rec.original_invoice_number,
                total_invoice_value=rec.total_invoice_value,
                total_invoice_value_present=rec.total_invoice_value_present,
            )

        agg = groups[key]
        agg.taxable_value += rec.taxable_value
        agg.igst += rec.igst
        agg.cgst += rec.cgst
        agg.sgst += rec.sgst
        agg.cess += rec.cess
        agg.cess_present = agg.cess_present or rec.cess_present
        if rec.total_invoice_value_present and not agg.total_invoice_value_present:
            agg.total_invoice_value = rec.total_invoice_value
            agg.total_invoice_value_present = True
        agg.source_rows.append(rec)
        agg.financial_parse_issues.extend(rec.financial_parse_issues)

    return list(groups.values())


# ---------------------------------------------------------------------------
# Helpers (mirror Phase 2 but operate on AggregatedGSTCreditNote)
# ---------------------------------------------------------------------------

def _make_tax_cmp(head: str, t: Decimal, g: Decimal) -> TaxComparison:
    """Compare note values by magnitude without mutating raw source values.

    Credit and debit note exports commonly express the same financial event
    with opposite accounting signs.  Magnitude normalization is deliberately
    confined to this notes engine.
    """
    d = abs(t) - abs(g)
    return TaxComparison(tax_head=head, tally_value=t, gst_value=g,
                         difference=d, abs_difference=abs(d))


def compare_note_financials(tally, gst):
    """Return notes-domain taxable/tax comparisons using absolute magnitudes."""
    return (
        abs(tally.taxable_value) - abs(gst.taxable_value),
        _make_tax_cmp("IGST", tally.igst, gst.igst),
        _make_tax_cmp("CGST", tally.cgst, gst.cgst),
        _make_tax_cmp("SGST", tally.sgst, gst.sgst),
    )


def _classify(td: Decimal, i: TaxComparison, c: TaxComparison,
              s: TaxComparison) -> ReconciliationStatus:
    mx = max(i.abs_difference, c.abs_difference, s.abs_difference, abs(td))
    if mx == _ZERO:
        return ReconciliationStatus.PERFECTLY_MATCHED
    if mx <= _ONE_RUPEE:
        return ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE
    return ReconciliationStatus.MAJOR_DIFFERENCE


def _cn_by_gstin(
    notes: List[AggregatedGSTCreditNote],
) -> Dict[str, List[AggregatedGSTCreditNote]]:
    groups: Dict[str, List[AggregatedGSTCreditNote]] = {}
    for n in notes:
        groups.setdefault(n.gstin, []).append(n)
    return groups


def _fuzzy_supplier(
    name: str, notes: List[AggregatedGSTCreditNote],
) -> List[AggregatedGSTCreditNote]:
    norm = normalize_supplier_name(name)
    if not norm:
        return []
    return [n for n in notes
            if fuzz.token_sort_ratio(norm, normalize_supplier_name(n.supplier_name))
            >= _SUPPLIER_FUZZY_THRESHOLD]


def _exact_note(
    tally_note: str, candidates: List[AggregatedGSTCreditNote],
) -> List[AggregatedGSTCreditNote]:
    return [c for c in candidates if not c.consumed
            and c.note_number_original.strip() == tally_note.strip()
            and tally_note.strip()]


def _norm_note(
    tally_norm: str, candidates: List[AggregatedGSTCreditNote],
) -> List[AggregatedGSTCreditNote]:
    return [c for c in candidates if not c.consumed
            and c.note_number_normalized == tally_norm and tally_norm]


def _last_n_note(
    tally_last: str, candidates: List[AggregatedGSTCreditNote], n: int,
    tv: Decimal, ti: Decimal, tc: Decimal, ts: Decimal,
) -> List[AggregatedGSTCreditNote]:
    if not tally_last or len(tally_last) < n:
        return []
    matches = []
    for c in candidates:
        if c.consumed:
            continue
        gl = c.note_number_last_4 if n == 4 else c.note_number_last_2
        if gl != tally_last:
            continue
        if abs(abs(tv) - abs(c.taxable_value)) > _ONE_RUPEE:
            continue
        mx = max(
            abs(abs(ti) - abs(c.igst)),
            abs(abs(tc) - abs(c.cgst)),
            abs(abs(ts) - abs(c.sgst)),
        )
        if mx > Decimal("5.00"):
            continue
        matches.append(c)
    return matches


# ---------------------------------------------------------------------------
# Build Note Result
# ---------------------------------------------------------------------------

def _build_note_result(
    tally: ParsedInvoiceRecord,
    gst: Optional[AggregatedGSTCreditNote],
    method: MatchMethod,
    status: ReconciliationStatus,
    explanation: str,
    supplier_match_type: str = "gstin_based",
    candidate_count: int = 0,
    candidates: Optional[List[Any]] = None,
) -> ReconciliationResult:
    result = ReconciliationResult(reconciliation_type="NOTE")
    result.supplier_gstin = tally.gstin_normalized
    result.supplier_tally = tally.supplier_name
    result.invoice_number_tally = tally.invoice_number.original
    result.invoice_date = tally.invoice_date
    result.match_method = method

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
        result.invoice_number_gst = gst.note_number_original
        result.supplier_gstin = gst.gstin or tally.gstin_normalized
        result.taxable_value = tally.taxable_value

        td, igst_c, cgst_c, sgst_c = compare_note_financials(tally, gst)

        result.igst_diff = igst_c.difference
        result.cgst_diff = cgst_c.difference
        result.sgst_diff = sgst_c.difference

        audit.gst_rows = gst.source_rows
        audit.gst_invoice_original = gst.note_number_original
        audit.gst_invoice_normalized = gst.note_number_normalized
        audit.gst_gstin = gst.gstin
        audit.gst_supplier = gst.supplier_name
        audit.gst_taxable = gst.taxable_value
        audit.taxable_difference = td
        audit.igst_comparison = igst_c
        audit.cgst_comparison = cgst_c
        audit.sgst_comparison = sgst_c

        if method != MatchMethod.AMBIGUOUS:
            status = _classify(td, igst_c, cgst_c, sgst_c)

        audit.gstin_match = (
            "exact" if tally.gstin_normalized and tally.gstin_normalized == gst.gstin
            else "missing_in_tally" if not tally.gstin_normalized
            else "mismatch"
        )
    else:
        result.taxable_value = tally.taxable_value
        audit.gstin_match = "no_gst_match"
        audit.gst_taxable = _ZERO

    result.status = status
    result.remarks = explanation
    audit.final_status = status
    audit.explanation = explanation
    result.audit = audit
    result.refresh_source_values()
    return result


def _issue_explanation(issue, side: str) -> str:
    return (
        f"{issue.field_name} contains an invalid monetary value in the {side} source: "
        f"{issue.raw_value!r}. Review the source data before reconciliation."
    )


# ---------------------------------------------------------------------------
# Main Notes Reconciliation
# ---------------------------------------------------------------------------

def reconcile_notes(
    tally_records: List[ParsedInvoiceRecord],
    gst_records: List[ParsedInvoiceRecord],
) -> NotesReconciliationSummary:
    """
    Reconcile Tally Debit Notes against GST Supplier Credit Notes.

    Filters:
    - Tally: VoucherType.DEBIT_NOTE
    - GST: confirmed canonical Credit Note direction only

    Returns NotesReconciliationSummary with all results and counts.
    """
    # Filter Tally debit notes
    tally_dn = [
        r for r in tally_records
        if r.source == RecordSource.TALLY
        and r.voucher_type == VoucherType.DEBIT_NOTE
        and r.invoice_number.original
    ]

    # Aggregate GST credit notes
    aggregated = aggregate_gst_credit_notes(gst_records)

    # Group by GSTIN
    by_gstin = _cn_by_gstin(aggregated)

    # Detect duplicates
    dup_groups: Dict[str, List[ParsedInvoiceRecord]] = {}
    for rec in tally_dn:
        k = f"{rec.gstin_normalized}|{rec.invoice_number.normalized}"
        dup_groups.setdefault(k, []).append(rec)
    dupes = {k: v for k, v in dup_groups.items() if len(v) > 1}

    summary = NotesReconciliationSummary()
    summary.total_tally_debit_notes = len(tally_dn)
    summary.total_gst_credit_notes = len(aggregated)

    for tally in tally_dn:
        if tally.financial_parse_issues:
            summary.results.append(_build_note_result(
                tally, None, MatchMethod.NO_MATCH, ReconciliationStatus.NEEDS_REVIEW,
                _issue_explanation(tally.financial_parse_issues[0], "Accounts"),
            ))
            continue
        dk = f"{tally.gstin_normalized}|{tally.invoice_number.normalized}"
        if dk in dupes and len(dupes[dk]) > 1:
            summary.results.append(_build_note_result(
                tally, None, MatchMethod.NO_MATCH,
                ReconciliationStatus.POTENTIAL_DUPLICATE,
                f"Potential duplicate: {len(dupes[dk])} Tally debit notes "
                f"with same GSTIN+note number",
                candidate_count=len(dupes[dk]),
            ))
            continue

        summary.results.append(_match_single_note(tally, aggregated, by_gstin))

    # Unconsumed GST credit notes → Not in Tally
    for agg in aggregated:
        if not agg.consumed:
            summary.not_in_tally_notes.append(agg)
            nit = ReconciliationResult(
                reconciliation_type="NOTE",
                supplier_gstin=agg.gstin, supplier_gst=agg.supplier_name,
                invoice_number_gst=agg.note_number_original,
                invoice_date=agg.note_date, taxable_value=agg.taxable_value,
                status=ReconciliationStatus.NOT_IN_TALLY,
                match_method=MatchMethod.NO_MATCH,
                remarks="GST credit note not matched to any Tally debit note",
            )
            nit.audit = AuditTrail(
                gst_rows=agg.source_rows,
                gst_invoice_original=agg.note_number_original,
                gst_invoice_normalized=agg.note_number_normalized,
                gst_gstin=agg.gstin, gst_supplier=agg.supplier_name,
                gst_taxable=agg.taxable_value,
                final_status=ReconciliationStatus.NOT_IN_TALLY,
                explanation="GST credit note not matched to any Tally debit note",
            )
            summary.results.append(nit)

    for result in summary.results:
        result.refresh_source_values()
    summary.compute_counts()
    return summary


def _match_single_note(
    tally: ParsedInvoiceRecord,
    all_cn: List[AggregatedGSTCreditNote],
    by_gstin: Dict[str, List[AggregatedGSTCreditNote]],
) -> ReconciliationResult:
    """Match one Tally debit note using the priority chain."""
    invalid_exact = [
        candidate for candidate in all_cn
        if candidate.financial_parse_issues
        and candidate.gstin == tally.gstin_normalized
        and candidate.note_number_normalized == tally.invoice_number.normalized
    ]
    valid_exact = [
        candidate for candidate in all_cn
        if not candidate.financial_parse_issues
        and candidate.gstin == tally.gstin_normalized
        and candidate.note_number_normalized == tally.invoice_number.normalized
        and not candidate.consumed
    ]
    if invalid_exact and not valid_exact:
        return _build_note_result(
            tally, None, MatchMethod.AMBIGUOUS, ReconciliationStatus.NEEDS_REVIEW,
            _issue_explanation(invalid_exact[0].financial_parse_issues[0], "GSTR"),
            "gstin_based", len(invalid_exact), candidates=invalid_exact,
        )

    matchable = [candidate for candidate in all_cn if not candidate.financial_parse_issues]
    candidates: List[AggregatedGSTCreditNote] = []
    smt = "no_match"

    if tally.gstin_normalized:
        candidates = [c for c in by_gstin.get(tally.gstin_normalized, []) if not c.financial_parse_issues]
        smt = "gstin_based" if candidates else "gstin_no_match"
    else:
        candidates = _fuzzy_supplier(tally.supplier_name, matchable)
        smt = "name_fallback" if candidates else "no_match"

    available = [c for c in candidates if not c.consumed]
    if not available:
        return _build_note_result(
            tally, None, MatchMethod.NO_MATCH,
            ReconciliationStatus.MISSING_IN_GSTR,
            "No matching GST credit note found", supplier_match_type=smt,
        )

    inv = tally.invoice_number

    # A: Exact
    ex = _exact_note(inv.original, available)
    if len(ex) == 1:
        ex[0].consumed = True
        return _build_note_result(tally, ex[0], MatchMethod.EXACT_INVOICE,
            ReconciliationStatus.PERFECTLY_MATCHED,
            "Exact note number match", smt, 1)
    if len(ex) > 1:
        return _build_note_result(tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Multiple exact note matches ({len(ex)})", smt, len(ex), candidates=ex)

    # B: Normalized
    nm = _norm_note(inv.normalized, available)
    if len(nm) == 1:
        nm[0].consumed = True
        return _build_note_result(tally, nm[0], MatchMethod.NORMALIZED_INVOICE,
            ReconciliationStatus.PERFECTLY_MATCHED,
            f"Normalized note match: '{inv.original}' ↔ '{nm[0].note_number_original}'",
            smt, 1)
    if len(nm) > 1:
        return _build_note_result(tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Multiple normalized matches ({len(nm)})", smt, len(nm), candidates=nm)

    # C: Last 4
    l4 = _last_n_note(inv.last_4, available, 4,
                      tally.taxable_value, tally.igst, tally.cgst, tally.sgst)
    if len(l4) == 1:
        l4[0].consumed = True
        return _build_note_result(tally, l4[0], MatchMethod.INVOICE_LAST_4,
            ReconciliationStatus.PERFECTLY_MATCHED,
            f"Last-4 note match: '{inv.original}' ↔ '{l4[0].note_number_original}'",
            smt, 1)
    if len(l4) > 1:
        return _build_note_result(tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Last-4 ambiguous ({len(l4)} candidates)", smt, len(l4), candidates=l4)

    # D: Last 2
    l2 = _last_n_note(inv.last_2, available, 2,
                      tally.taxable_value, tally.igst, tally.cgst, tally.sgst)
    if len(l2) == 1:
        l2[0].consumed = True
        return _build_note_result(tally, l2[0], MatchMethod.INVOICE_LAST_2,
            ReconciliationStatus.PERFECTLY_MATCHED,
            f"Last-2 note match: '{inv.original}' ↔ '{l2[0].note_number_original}'",
            smt, 1)
    if len(l2) > 1:
        return _build_note_result(tally, None, MatchMethod.AMBIGUOUS,
            ReconciliationStatus.NEEDS_REVIEW,
            f"Last-2 ambiguous ({len(l2)} candidates)", smt, len(l2), candidates=l2)

    return _build_note_result(
        tally, None, MatchMethod.NO_MATCH,
        ReconciliationStatus.MISSING_IN_GSTR,
        "No matching GST credit note after all attempts", smt, 0)
