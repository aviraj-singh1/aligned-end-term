"""Session-backed filtering, sorting and pagination for dashboard results."""
from __future__ import annotations

import math
from typing import Any, Dict, List

from backend.app.models.reconciliation import ReconciliationStatus
from backend.app.services.result_provenance import result_provenance


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

CATEGORY_ALIASES = {
    "all": "all",
    "perfect": "perfect",
    "perfectly matched": "perfect",
    "minor": "minor",
    "minor rounding difference": "minor",
    "major": "major",
    "major difference": "major",
    "taxable value missing": "taxable_value_missing",
    "missing": "missing",
    "missing in gstr-2b": "missing",
    "not in tally": "not_in_tally",
    "needs review": "needs_review",
    "needs review / ambiguous": "needs_review",
    "potential duplicate": "potential_duplicate",
    "date mismatch": "date_mismatch",
    "date mismatches": "date_mismatch",
    "invoice mismatch": "invoice_mismatch",
    "invoice mismatches": "invoice_mismatch",
    "invoice number mismatch": "invoice_mismatch",
    "cancelled vouchers": "cancelled_vouchers",
    "notes": "notes",
    "notes all": "notes",
    "notes perfect": "notes_perfect",
    "notes perfectly matched": "notes_perfect",
    "notes minor": "notes_minor",
    "notes minor rounding difference": "notes_minor",
    "notes tax mismatch": "notes_major",
    "notes major difference": "notes_major",
    "notes missing in gstr": "notes_missing_in_gstr",
    "notes missing in debit notes": "notes_missing_in_debit_notes",
    "notes needs review": "notes_needs_review",
    "notes potential duplicate": "notes_potential_duplicate",
}

STATUS_CATEGORIES = {
    "perfect": ReconciliationStatus.PERFECTLY_MATCHED,
    "minor": ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE,
    "major": ReconciliationStatus.MAJOR_DIFFERENCE,
    "taxable_value_missing": ReconciliationStatus.TAXABLE_VALUE_MISSING,
    "missing": ReconciliationStatus.MISSING_IN_GSTR,
    "needs_review": ReconciliationStatus.NEEDS_REVIEW,
    "potential_duplicate": ReconciliationStatus.POTENTIAL_DUPLICATE,
    "invoice_mismatch": ReconciliationStatus.INVOICE_NUMBER_MISMATCH,
}

SORT_FIELDS = {
    "supplier": "supplier",
    "gstin": "gstin",
    "accounts_invoice": "accounts_invoice",
    "accounts_invoice_number": "accounts_invoice",
    "gstr_invoice": "gst_invoice",
    "gstr_invoice_number": "gst_invoice",
    "accounts_date": "accounts_date",
    "accounts_invoice_date": "accounts_date",
    "gstr_date": "gst_date",
    "gstr_invoice_date": "gst_date",
    "taxable_value": "taxable_value",
    "status": "status",
    "match_method": "match_method",
    "source_system": "source_system",
}


def normalize_category(category: str) -> str:
    key = " ".join(str(category or "all").strip().lower().replace("_", " ").split())
    if key not in CATEGORY_ALIASES:
        raise ValueError("Unsupported result category")
    return CATEGORY_ALIASES[key]


def _source_value(result, attribute):
    rows = getattr(result.audit, "tally_rows", [])
    if not rows:
        return None
    value = getattr(rows[0], attribute, None)
    return None if value is None else str(value)


def _source_date(result):
    rows = getattr(result.audit, "tally_rows", [])
    if not rows:
        return None
    value = rows[0].invoice_date or getattr(rows[0], "note_date", None)
    return value.isoformat() if value else None


def _financial_issues(rows):
    return [issue for row in rows for issue in getattr(row, "financial_parse_issues", [])]


def _issue_payload(issues):
    return [{"field": i.field_name, "raw_value": i.raw_value, "reason": i.reason} for i in issues]


def _safe_value(value, issues, field):
    return None if value is None or any(issue.field_name == field for issue in issues) else str(value)


def serialize_result(result, note: bool = False) -> Dict[str, Any]:
    result.refresh_source_values()
    gst_rows = getattr(result.audit, "gst_rows", [])
    gst_date = None
    if gst_rows:
        value = gst_rows[0].invoice_date or getattr(gst_rows[0], "note_date", None)
        gst_date = value.isoformat() if value else None
    missing_taxable = bool(getattr(result, "taxable_value_missing", False))
    tally_issues = _financial_issues(getattr(result.audit, "tally_rows", []))
    candidate_rows = [
        row for candidate in getattr(result.audit, "candidates", [])
        for row in getattr(candidate, "source_rows", [])
    ]
    gst_issues = _financial_issues(gst_rows or candidate_rows)
    issues = tally_issues + gst_issues
    return {
        "result_id": getattr(result, "api_result_id", ""),
        "reconciliation_type": "NOTE" if note else getattr(result, "reconciliation_type", "PURCHASE"),
        **({"gst_note_direction": gst_rows[0].note_direction.value if gst_rows else None,
            "accounts_note_direction": (
                result.audit.tally_rows[0].note_direction.value if result.audit.tally_rows else None)} if note else {}),
        "status": result.status.value,
        "accounts_invoice": result.invoice_number_tally,
        "gst_invoice": result.invoice_number_gst,
        "supplier": result.supplier_tally or result.supplier_gst,
        "gstin": result.supplier_gstin,
        "accounts_supplier": _source_value(result, "supplier_name"),
        "accounts_gstin": _source_value(result, "gstin_original") or _source_value(result, "gstin_normalized"),
        "taxable_value": None if missing_taxable else _safe_value(result.taxable_value, issues, "taxable_value"),
        "accounts_taxable": None if missing_taxable else _safe_value(_source_value(result, "taxable_value"), tally_issues, "taxable_value"),
        "gst_taxable": _safe_value(result.audit.gst_taxable, gst_issues, "taxable_value"),
        "taxable_difference": _safe_value(result.taxable_difference, issues, "taxable_value"),
        "accounts_gross_total": _safe_value(result.audit.tally_gross_total, tally_issues, "gross_total"),
        "gst_invoice_value": _safe_value(result.audit.gst_invoice_value, gst_issues, "total_invoice_value"),
        "gross_total_difference": _safe_value(result.audit.gross_total_difference, issues, "gross_total"),
        "igst_diff": _safe_value(result.igst_diff, issues, "igst"), "cgst_diff": _safe_value(result.cgst_diff, issues, "cgst"), "sgst_diff": _safe_value(result.sgst_diff, issues, "sgst"),
        "accounts_igst": _safe_value(_source_value(result, "igst"), tally_issues, "igst"), "accounts_cgst": _safe_value(_source_value(result, "cgst"), tally_issues, "cgst"), "accounts_sgst": _safe_value(_source_value(result, "sgst"), tally_issues, "sgst"),
        "gst_igst": _safe_value(result.audit.igst_comparison.gst_value, gst_issues, "igst"), "gst_cgst": _safe_value(result.audit.cgst_comparison.gst_value, gst_issues, "cgst"), "gst_sgst": _safe_value(result.audit.sgst_comparison.gst_value, gst_issues, "sgst"),
        "accounts_date": result.tally_date.isoformat() if result.tally_date else None, "gst_date": result.gstr_date.isoformat() if result.gstr_date else None,
        **result_provenance(result),
        "date_mismatch": bool(getattr(result, "date_mismatch", False)),
        "date_difference_days": getattr(result, "date_difference_days", None),
        "invoice_mismatch": bool(getattr(result, "invoice_mismatch", False)),
        "match_method": result.match_method.value, "remarks": result.remarks,
        "evidence": result.audit.explanation,
        "financial_parse_issues": _issue_payload(issues),
        "candidates": [{
            **({"gst_note_direction": candidate.source_rows[0].note_direction.value if candidate.source_rows else "Unknown"} if note else {}),
            "candidate_id": getattr(candidate, "api_candidate_id", ""),
            "gst_invoice": getattr(candidate, "note_number_original", "") if note else candidate.invoice_number_original,
            "supplier": candidate.supplier_name, "gstin": candidate.gstin,
            "invoice_date": (candidate.note_date if note else candidate.invoice_date).isoformat() if (candidate.note_date if note else candidate.invoice_date) else None,
            "taxable_value": _safe_value(candidate.taxable_value, getattr(candidate, "financial_parse_issues", []), "taxable_value"),
            "cgst": _safe_value(candidate.cgst, getattr(candidate, "financial_parse_issues", []), "cgst"),
            "sgst": _safe_value(candidate.sgst, getattr(candidate, "financial_parse_issues", []), "sgst"),
            "igst": _safe_value(candidate.igst, getattr(candidate, "financial_parse_issues", []), "igst"),
        } for candidate in getattr(result.audit, "candidates", [])],
    }


def serialize_not_in_tally(record) -> Dict[str, Any]:
    issues = getattr(record, "financial_parse_issues", [])
    return {
        "result_id": getattr(record, "api_candidate_id", "") or f"not-in-{record.gstin}-{record.invoice_number_normalized}",
        "reconciliation_type": "PURCHASE",
        "gst_invoice": record.invoice_number_original, "supplier": record.supplier_name,
        "gstin": record.gstin, "taxable_value": _safe_value(record.taxable_value, issues, "taxable_value"),
        "gst_date": record.invoice_date.isoformat() if record.invoice_date else None,
        "status": ReconciliationStatus.NOT_IN_TALLY.value,
        "gst_taxable": _safe_value(record.taxable_value, issues, "taxable_value"),
        **{f"gst_{head}": _safe_value(getattr(record, head), issues, head) for head in ("igst", "cgst", "sgst")},
        "source_system": "GSTR",
        "gstr_source_category": record.source_rows[0].sheet_category.value if record.source_rows else None,
        "gstr_source_sheet": record.source_rows[0].source_sheet if record.source_rows else None,
        "gstr_source_rows": [row.source_row for row in record.source_rows],
        "financial_parse_issues": _issue_payload(issues),
    }


def serialize_cancelled(voucher) -> Dict[str, Any]:
    return {
        "id": voucher.id, "result_id": voucher.id, "source_side": voucher.source_side.value,
        "source_system": voucher.source_system.value, "supplier": voucher.supplier,
        "gstin": voucher.gstin, "invoice_number": voucher.invoice_number,
        "invoice_date": voucher.invoice_date.isoformat() if voucher.invoice_date else None,
        "taxable_value": str(voucher.taxable_value), "igst": str(voucher.igst),
        "cgst": str(voucher.cgst), "sgst": str(voucher.sgst),
        "source_sheet": voucher.source_sheet, "source_row": voucher.source_row,
        "status": voucher.status, "cancellation_reason": voucher.cancellation_reason,
        "cancellation_evidence": voucher.cancellation_evidence,
    }


def _category_items(purchases_summary, notes_summary, category: str) -> List[Dict[str, Any]]:
    if category == "not_in_tally":
        return [serialize_not_in_tally(record) for record in purchases_summary.not_in_tally_records]
    if category == "cancelled_vouchers":
        return [serialize_cancelled(voucher) for voucher in purchases_summary.cancelled_vouchers]
    if category.startswith("notes"):
        results = notes_summary.results
        note_statuses = {
            "notes_perfect": {ReconciliationStatus.PERFECTLY_MATCHED},
            "notes_minor": {ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE},
            "notes_major": {ReconciliationStatus.MAJOR_DIFFERENCE},
            "notes_missing_in_gstr": {ReconciliationStatus.MISSING_IN_GSTR},
            "notes_missing_in_debit_notes": {ReconciliationStatus.NOT_IN_TALLY},
            "notes_needs_review": {
                ReconciliationStatus.NEEDS_REVIEW,
                ReconciliationStatus.TAXABLE_VALUE_MISSING,
            },
            "notes_potential_duplicate": {ReconciliationStatus.POTENTIAL_DUPLICATE},
        }
        if category != "notes":
            statuses = note_statuses.get(category)
            if statuses is None:
                raise ValueError("Unsupported result category")
            results = [result for result in results if result.status in statuses]
        return [serialize_result(result, note=True) for result in results]
    results = purchases_summary.results
    if category == "date_mismatch":
        results = [result for result in results if getattr(result, "date_mismatch", False)]
    elif category in STATUS_CATEGORIES:
        status = STATUS_CATEGORIES[category]
        results = [result for result in results if result.status == status]
    elif category != "all":
        raise ValueError("Unsupported result category")
    return [serialize_result(result) for result in results]


def _search(items: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    query = str(query or "").strip().casefold()
    if not query:
        return items
    fields = ("supplier", "gstin", "accounts_invoice", "gst_invoice", "invoice_number")
    return [item for item in items if any(query in str(item.get(field) or "").casefold() for field in fields)]


def paginate_results(purchases_summary, notes_summary, *, category="all", page=1,
                     page_size=DEFAULT_PAGE_SIZE, search="", sort_by=None, sort_order="asc"):
    normalized = normalize_category(category)
    items = _search(_category_items(purchases_summary, notes_summary, normalized), search)
    if sort_by:
        if sort_by not in SORT_FIELDS:
            raise ValueError("Unsupported sort field")
        if sort_order not in ("asc", "desc"):
            raise ValueError("Unsupported sort order")
        field = SORT_FIELDS[sort_by]
        indexed = list(enumerate(items))
        def key(pair):
            index, item = pair
            value = item.get(field)
            if field == "taxable_value":
                try: value = float(value) if value is not None else float("-inf")
                except (TypeError, ValueError): value = float("-inf")
            else:
                value = str(value or "").casefold()
            return (value, str(item.get("result_id") or item.get("id") or ""), index)
        indexed.sort(key=key, reverse=sort_order == "desc")
        items = [item for _, item in indexed]
    total = len(items)
    total_pages = math.ceil(total / page_size) if total else 0
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size], "total": total, "page": page,
        "page_size": page_size, "total_pages": total_pages, "category": normalized,
    }
