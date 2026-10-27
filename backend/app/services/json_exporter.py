import json
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict

from backend.app.models.reconciliation import (
    ReconciliationSummary, ReconciliationStatus, MatchMethod
)
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary
from backend.app.services.cancellation_detector import detect_accounts_source_system
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.result_provenance import result_provenance


def _financial_issues(rows):
    return [issue for row in rows for issue in getattr(row, "financial_parse_issues", [])]


def _serialize_issues(issues):
    return [
        {"field": issue.field_name, "raw_value": issue.raw_value, "reason": issue.reason}
        for issue in issues
    ]


def _valid_value(value, issues, field):
    return None if any(issue.field_name == field for issue in issues) else value


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)  # Ensure precision is not lost
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


def generate_json_report(
    purchases_summary: ReconciliationSummary,
    notes_summary: NotesReconciliationSummary,
    reporting_period: str,
) -> str:
    """
    Generate the structured JSON report for reconciliation results.
    """
    # Helper to serialize a ReconciliationResult
    def serialize_result(res, reconciliation_type="PURCHASE"):
        res.refresh_source_values()
        audit = res.audit
        tally_row = audit.tally_rows[0] if audit.tally_rows else None
        gst_row = audit.gst_rows[0] if audit.gst_rows else None
        tally_issues = _financial_issues(audit.tally_rows)
        candidate_rows = [
            row for candidate in audit.candidates
            for row in getattr(candidate, "source_rows", [])
        ]
        gst_issues = _financial_issues(audit.gst_rows or candidate_rows)
        issues = tally_issues + gst_issues
        return {
            "result_id": getattr(res, "api_result_id", ""),
            "reconciliation_type": reconciliation_type,
            "supplier": res.supplier_tally or res.supplier_gst,
            "gstin": res.supplier_gstin,
            "supplier_gstin": res.supplier_gstin,
            "supplier_tally": res.supplier_tally,
            "supplier_gst": res.supplier_gst,
            "tally_invoice_number": res.invoice_number_tally,
            "gstr_invoice_number": res.invoice_number_gst,
            "accounts_invoice_number": res.invoice_number_tally,
            "invoice_number_tally": res.invoice_number_tally,
            "invoice_number_gst": res.invoice_number_gst,
            "invoice_date": res.invoice_date,
            "tally_date": res.tally_date or (tally_row.invoice_date if tally_row else None),
            "gstr_date": res.gstr_date or (gst_row.invoice_date if gst_row else None),
            "accounts_invoice_date": res.tally_date or (tally_row.invoice_date if tally_row else None),
            "gstr_invoice_date": res.gstr_date or (gst_row.invoice_date if gst_row else None),
            "taxable_value": None if res.taxable_value_missing else _valid_value(res.taxable_value, issues, "taxable_value"),
            "accounts_taxable_value": None if res.taxable_value_missing else _valid_value(audit.tally_taxable, tally_issues, "taxable_value"),
            "tally_taxable_value": None if res.taxable_value_missing else _valid_value(audit.tally_taxable, tally_issues, "taxable_value"),
            "gstr_taxable_value": _valid_value(audit.gst_taxable, gst_issues, "taxable_value"),
            "accounts_gross_total": _valid_value(audit.tally_gross_total, tally_issues, "gross_total"),
            "gstr_invoice_value": _valid_value(audit.gst_invoice_value, gst_issues, "total_invoice_value"),
            "gross_total_difference": audit.gross_total_difference,
            "tally_igst": _valid_value(audit.igst_comparison.tally_value, tally_issues, "igst"),
            "gstr_igst": _valid_value(audit.igst_comparison.gst_value, gst_issues, "igst"),
            "tally_cgst": _valid_value(audit.cgst_comparison.tally_value, tally_issues, "cgst"),
            "gstr_cgst": _valid_value(audit.cgst_comparison.gst_value, gst_issues, "cgst"),
            "tally_sgst": _valid_value(audit.sgst_comparison.tally_value, tally_issues, "sgst"),
            "gstr_sgst": _valid_value(audit.sgst_comparison.gst_value, gst_issues, "sgst"),
            "taxable_difference": res.taxable_difference,
            "igst_difference": res.igst_diff,
            "cgst_difference": res.cgst_diff,
            "sgst_difference": res.sgst_diff,
            "match_method": res.match_method,
            "status": res.status,
            "invoice_mismatch": getattr(res, "invoice_mismatch", False),
            "remarks": res.remarks,
            "reason": res.remarks,
            "evidence": audit.explanation,
            "date_mismatch": getattr(res, "date_mismatch", False),
            "date_difference_days": getattr(res, "date_difference_days", None),
            "source_system": detect_accounts_source_system(tally_row) if tally_row else "ACCOUNTS",
            "source_sheet": tally_row.source_sheet if tally_row else "",
            "source_row": tally_row.source_row if tally_row else None,
            "gstr_source_category": gst_row.sheet_category if gst_row else None,
            "gstr_source_sheet": gst_row.source_sheet if gst_row else "",
            "gstr_source_rows": [row.source_row for row in audit.gst_rows],
            "amendment_original_number": gst_row.amendment_original_number if gst_row else "",
            "amendment_original_date": gst_row.amendment_original_date if gst_row else None,
            "financial_parse_issues": _serialize_issues(issues),
            **result_provenance(res),
        }

    # Missing in GSTR
    missing_in_gstr = [
        serialize_result(r) for r in purchases_summary.results
        if r.status == ReconciliationStatus.MISSING_IN_GSTR
    ]

    # Major Differences (only from purchases for this section)
    major_diffs = [
        serialize_result(r) for r in purchases_summary.results
        if r.status == ReconciliationStatus.MAJOR_DIFFERENCE
    ]
    taxable_value_missing = [
        serialize_result(r) for r in purchases_summary.results
        if r.status == ReconciliationStatus.TAXABLE_VALUE_MISSING
    ]

    invoice_mismatches = [
        serialize_result(r) for r in purchases_summary.results
        if r.status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH
    ]

    # Invoice Number Remarks
    inv_remarks = [
        serialize_result(r) for r in purchases_summary.results
        if r.match_method in (
            MatchMethod.NORMALIZED_INVOICE,
            MatchMethod.INVOICE_LAST_4,
            MatchMethod.INVOICE_LAST_2
        )
    ]

    # Not in Tally (Purchases)
    not_in_tally = []
    for agg in purchases_summary.not_in_tally_records:
        issues = getattr(agg, "financial_parse_issues", [])
        not_in_tally.append({
            "supplier_gstin": agg.gstin,
            "supplier_name": agg.supplier_name,
            "invoice_number": agg.invoice_number_original,
            "invoice_date": agg.invoice_date,
            "taxable_value": _valid_value(agg.taxable_value, issues, "taxable_value"),
            "igst": _valid_value(agg.igst, issues, "igst"),
            "cgst": _valid_value(agg.cgst, issues, "cgst"),
            "sgst": _valid_value(agg.sgst, issues, "sgst"),
            "total_tax": None if issues else agg.total_tax(),
            "remarks": issues[0].reason if issues else "Present in GSTR-2B but not found in Tally",
            "financial_parse_issues": _serialize_issues(issues),
            "gstr_source_category": agg.source_rows[0].sheet_category if agg.source_rows else None,
            "gstr_source_sheet": agg.source_rows[0].source_sheet if agg.source_rows else "",
            "gstr_source_rows": [row.source_row for row in agg.source_rows],
            "amendment_original_number": agg.source_rows[0].amendment_original_number if agg.source_rows else "",
            "amendment_original_date": agg.source_rows[0].amendment_original_date if agg.source_rows else None,
        })

    # Notes
    notes_list = [dict(serialize_result(r, "NOTE"),
                       gst_note_direction=r.audit.gst_rows[0].note_direction.value if r.audit.gst_rows else None,
                       accounts_note_direction=r.audit.tally_rows[0].note_direction.value if r.audit.tally_rows else None)
                  for r in notes_summary.results]
    represented_unmatched = {
        (r.supplier_gstin, normalize_invoice_number(r.invoice_number_gst).normalized)
        for r in notes_summary.results
        if r.status == ReconciliationStatus.NOT_IN_TALLY
    }
    for agg in notes_summary.not_in_tally_notes:
        if agg.consumed or (agg.gstin, agg.note_number_normalized) in represented_unmatched:
            continue
        issues = getattr(agg, "financial_parse_issues", [])
        notes_list.append({
            "reconciliation_type": "NOTE",
            "source_system": "GSTR",
            "gst_note_direction": agg.source_rows[0].note_direction.value if agg.source_rows else "Credit Note",
            "gstr_date": agg.note_date, "gstr_invoice_date": agg.note_date,
            "gstr_taxable_value": _valid_value(agg.taxable_value, issues, "taxable_value"),
            "gstr_igst": _valid_value(agg.igst, issues, "igst"),
            "gstr_cgst": _valid_value(agg.cgst, issues, "cgst"),
            "gstr_sgst": _valid_value(agg.sgst, issues, "sgst"),
            "supplier_gstin": agg.gstin,
            "supplier_tally": "",
            "supplier_gst": agg.supplier_name,
            "invoice_number_tally": "",
            "invoice_number_gst": agg.note_number_original,
            "invoice_date": agg.note_date,
            "taxable_value": _valid_value(agg.taxable_value, issues, "taxable_value"),
            "igst_difference": None,
            "cgst_difference": None,
            "sgst_difference": None,
            "match_method": MatchMethod.NO_MATCH,
            "status": ReconciliationStatus.NOT_IN_TALLY,
            "remarks": issues[0].reason if issues else "GST credit note not matched to any Tally debit note",
            "financial_parse_issues": _serialize_issues(issues),
            "gstr_source_category": agg.source_rows[0].sheet_category if agg.source_rows else None,
            "gstr_source_sheet": agg.source_rows[0].source_sheet if agg.source_rows else "",
            "gstr_source_rows": [row.source_row for row in agg.source_rows],
            "amendment_original_number": agg.source_rows[0].amendment_original_number if agg.source_rows else "",
            "amendment_original_date": agg.source_rows[0].amendment_original_date if agg.source_rows else None,
        })

    report = {
        "metadata": {
            "reporting_period": reporting_period,
            "generated_at": date.today().isoformat(),
            "reconciliation_scope": (
                "Reconciliation results reflect data matching between Accounts and GSTR records only. "
                "They do not determine ITC eligibility or GST compliance. "
                "Review eligibility separately before claiming ITC."
            ),
            "accounts_invoice_count": purchases_summary.total_tally_invoices,
            "gstr_invoice_count": purchases_summary.total_gst_invoices,
            "tally_debit_note_count": notes_summary.total_tally_debit_notes,
            "gst_credit_note_count": notes_summary.total_gst_credit_notes,
        },
        "counts": {
            "perfectly_matched": purchases_summary.perfectly_matched,
            "minor_rounding_difference": purchases_summary.minor_differences,
            "major_difference": purchases_summary.major_differences,
            "taxable_value_missing": purchases_summary.taxable_value_missing,
            "missing_in_gstr_2b": purchases_summary.missing_in_gstr,
            "not_in_tally": purchases_summary.not_in_tally,
            "needs_review_ambiguous": purchases_summary.needs_review,
            "potential_duplicate": purchases_summary.potential_duplicates,
            "date_mismatches": getattr(purchases_summary, "date_mismatches", 0),
            "invoice_mismatches": getattr(purchases_summary, "invoice_mismatches", 0),
            "cancelled_vouchers": purchases_summary.cancelled_voucher_count,
            "cancelled_accounts_vouchers": purchases_summary.cancelled_accounts_vouchers,
            "cancelled_gstr_vouchers": purchases_summary.cancelled_gstr_vouchers,
        },
        "purchase_summary": {
            "total_accounts": purchases_summary.total_tally_invoices,
            "total_gstr": purchases_summary.total_gst_invoices,
            "perfect": purchases_summary.perfectly_matched,
            "minor": purchases_summary.minor_differences,
            "major": purchases_summary.major_differences,
            "missing_in_gstr": purchases_summary.missing_in_gstr,
            "missing_in_accounts": purchases_summary.not_in_tally,
            "needs_review": purchases_summary.needs_review,
        },
        "notes_summary": {
            "total_debit_notes": notes_summary.total_tally_debit_notes,
            "total_gstr_credit_notes": notes_summary.total_gst_credit_notes,
            "perfect": notes_summary.perfectly_matched,
            "minor": notes_summary.minor_differences,
            "major": notes_summary.major_differences,
            "missing_in_gstr": notes_summary.missing_in_gst,
            "missing_in_debit_notes": notes_summary.not_in_tally,
            "needs_review": notes_summary.needs_review,
        },
        "reconciliation_summary": [serialize_result(r) for r in purchases_summary.results],
        "invoice_mismatches": invoice_mismatches,
        "missing_in_gstr": missing_in_gstr,
        "major_differences": major_diffs,
        "taxable_value_missing": taxable_value_missing,
        "invoice_number_remarks": inv_remarks,
        "not_in_tally": not_in_tally,
        "notes": notes_list
        ,"cancelled_vouchers": [
            {
                "id": voucher.id, "source": voucher.source_side,
                "source_system": voucher.source_system, "status": voucher.status,
                "supplier": voucher.supplier, "gstin": voucher.gstin,
                "invoice_number": voucher.invoice_number, "invoice_date": voucher.invoice_date,
                "taxable_value": voucher.taxable_value, "igst": voucher.igst,
                "cgst": voucher.cgst, "sgst": voucher.sgst,
                "source_sheet": voucher.source_sheet, "source_row": voucher.source_row,
                "reason": voucher.cancellation_reason,
                "evidence": voucher.cancellation_evidence,
            } for voucher in purchases_summary.cancelled_vouchers
        ]
    }

    return json.dumps(report, cls=CustomJSONEncoder, indent=2)
