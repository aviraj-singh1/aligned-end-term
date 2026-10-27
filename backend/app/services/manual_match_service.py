from decimal import Decimal
from typing import Dict, Any

from backend.app.services.session_manager import mutate_session
from backend.app.models.reconciliation import (
    AggregatedGSTInvoice, ReconciliationStatus, MatchMethod, TaxComparison
)
from backend.app.services.reconciliation_engine import _classify_status, _make_tax_comparison
from backend.app.services.notes_reconciliation_engine import (
    AggregatedGSTCreditNote, compare_note_financials, _classify as _classify_note,
)

def _execute_manual_match_in_session(session: Dict[str, Any], result_id: str, candidate_id: str) -> Dict[str, Any]:
    purchases_summary = session.get("reconciliation_summary")
    notes_summary = session.get("notes_summary")
    
    # Find candidate from map
    manual_match_map = session.get("manual_match_map", {})
    if result_id not in manual_match_map:
        raise ValueError("Result not found or not in a reviewable state")
        
    candidates_map = manual_match_map[result_id]
    if candidate_id not in candidates_map:
        raise ValueError("Candidate not found or invalid for this result")
        
    # Find result to update
    result = None
    is_note = False
    
    if purchases_summary:
        for r in purchases_summary.results:
            if getattr(r, "api_result_id", None) == result_id:
                result = r
                break
                
    if not result and notes_summary:
        for r in notes_summary.results:
            if getattr(r, "api_result_id", None) == result_id:
                result = r
                is_note = True
                break
                
    if not result:
        raise ValueError("Result not found in summary")

    mapped_candidate = candidates_map[candidate_id]
    if mapped_candidate is True:
        candidate = next(
            (
                item for item in result.audit.candidates
                if getattr(item, "api_candidate_id", None) == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("Candidate reference is missing from authoritative result state")
    else:
        # Backward compatibility for sessions serialized before compact maps.
        candidate = mapped_candidate

    if is_note and not isinstance(candidate, AggregatedGSTCreditNote):
        raise ValueError("Notes results can only be matched to note candidates")
    if not is_note and not isinstance(candidate, AggregatedGSTInvoice):
        raise ValueError("Purchase results can only be matched to purchase candidates")

    if candidate.consumed:
        raise ValueError("Candidate is already consumed elsewhere")

    tally = result.audit.tally_rows[0]
    if tally.financial_parse_issues or getattr(candidate, "financial_parse_issues", []):
        raise ValueError(
            "Manual matching is unavailable until invalid monetary source values are corrected"
        )
        
    if result.status not in (
        ReconciliationStatus.NEEDS_REVIEW,
        ReconciliationStatus.TAXABLE_VALUE_MISSING,
    ):
        raise ValueError("Result is not in a reviewable state")
        
    # Apply manual match
    candidate.consumed = True
    
    result.supplier_gst = candidate.supplier_name
    result.invoice_number_gst = candidate.note_number_original if is_note else candidate.invoice_number_original
    result.supplier_gstin = candidate.gstin or tally.gstin_normalized
    result.taxable_value = tally.taxable_value
    
    if is_note:
        taxable_diff, igst_cmp, cgst_cmp, sgst_cmp = compare_note_financials(tally, candidate)
    else:
        igst_cmp = _make_tax_comparison("IGST", tally.igst, candidate.igst)
        cgst_cmp = _make_tax_comparison("CGST", tally.cgst, candidate.cgst)
        sgst_cmp = _make_tax_comparison("SGST", tally.sgst, candidate.sgst)
        taxable_diff = tally.taxable_value - candidate.taxable_value
    gross_total_diff = (
        tally.gross_total - candidate.total_invoice_value
        if not is_note and tally.gross_total_present and candidate.total_invoice_value_present
        else None
    )
    
    result.igst_diff = igst_cmp.difference
    result.cgst_diff = cgst_cmp.difference
    result.sgst_diff = sgst_cmp.difference
    result.gross_total_diff = gross_total_diff

    tally_date = tally.note_date if is_note and tally.note_date else tally.invoice_date
    gst_date = candidate.note_date if is_note else candidate.invoice_date
    result.tally_date = tally_date
    result.gstr_date = gst_date
    if tally_date and gst_date:
        result.date_difference_days = abs((tally_date - gst_date).days)
        result.date_mismatch = result.date_difference_days != 0
    else:
        result.date_difference_days = None
        result.date_mismatch = False
    
    status = (
        ReconciliationStatus.TAXABLE_VALUE_MISSING
        if not tally.taxable_value_present
        else (
            _classify_note(taxable_diff, igst_cmp, cgst_cmp, sgst_cmp)
            if is_note else _classify_status(
                taxable_diff, igst_cmp, cgst_cmp, sgst_cmp, gross_total_diff
            )
        )
    )
    
    result.status = status
    result.match_method = MatchMethod.MANUAL_MATCH
    result.remarks = (
        "Taxable value is blank in the Accounts source; identity manually confirmed."
        if status == ReconciliationStatus.TAXABLE_VALUE_MISSING
        else "Manually matched by user"
    )
    
    result.audit.gst_rows = candidate.source_rows
    result.audit.gst_invoice_original = result.invoice_number_gst
    result.audit.gst_invoice_normalized = candidate.note_number_normalized if is_note else candidate.invoice_number_normalized
    result.audit.gst_gstin = candidate.gstin
    result.audit.gst_supplier = candidate.supplier_name
    result.audit.gst_taxable = candidate.taxable_value
    result.audit.taxable_difference = taxable_diff
    result.audit.tally_gross_total = tally.gross_total if tally.gross_total_present else None
    result.audit.gst_invoice_value = (
        candidate.total_invoice_value
        if not is_note and candidate.total_invoice_value_present else None
    )
    result.audit.gross_total_difference = gross_total_diff
    result.audit.igst_comparison = igst_cmp
    result.audit.cgst_comparison = cgst_cmp
    result.audit.sgst_comparison = sgst_cmp
    result.audit.final_status = status
    result.audit.explanation = "Manually matched by user"
    result.audit.invoice_match_method = MatchMethod.MANUAL_MATCH

    # The selected GST candidate is now represented by the manually matched
    # Tally result, so remove its former synthetic Not-in-Tally result. This
    # keeps the API result list and all future count recomputations one-to-one.
    if is_note:
        notes_summary.not_in_tally_notes = [
            existing for existing in notes_summary.not_in_tally_notes
            if not (
                existing.note_number_normalized == candidate.note_number_normalized
                and existing.gstin == candidate.gstin
            )
        ]
        notes_summary.results = [
            existing for existing in notes_summary.results
            if not (
                existing.status == ReconciliationStatus.NOT_IN_TALLY
                and existing.audit.gst_invoice_normalized == candidate.note_number_normalized
                and existing.audit.gst_gstin == candidate.gstin
            )
        ]
    else:
        purchases_summary.not_in_tally_records = [
            existing for existing in purchases_summary.not_in_tally_records
            if not (
                existing.invoice_number_normalized == candidate.invoice_number_normalized
                and existing.gstin == candidate.gstin
            )
        ]
        purchases_summary.results = [
            existing for existing in purchases_summary.results
            if not (
                existing.status == ReconciliationStatus.NOT_IN_TALLY
                and existing.audit.gst_invoice_normalized == candidate.invoice_number_normalized
                and existing.audit.gst_gstin == candidate.gstin
            )
        ]
    
    # Recompute counts
    if is_note:
        notes_summary.compute_counts()
    else:
        purchases_summary.compute_counts()
        
    # Clear the old exports so they get regenerated on next download
    session["reports"] = {}
    
    return {"status": "success", "new_status": status.value}


def execute_manual_match(session_id: str, result_id: str, candidate_id: str) -> Dict[str, Any]:
    result = mutate_session(
        session_id,
        lambda session: _execute_manual_match_in_session(session, result_id, candidate_id),
    )
    if result is None:
        raise ValueError("Session expired or not found")
    return result
