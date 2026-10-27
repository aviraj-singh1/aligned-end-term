import os
import uuid
from datetime import date
from decimal import Decimal
from typing import Dict, Any, Optional

from backend.app.services.session_manager import get_session, update_session, mutate_session
from backend.app.services.file_parser import parse_file, reparse_persisted_file
from backend.app.models.records import RecordSource, SheetCategory, VoucherType
from backend.app.services.reconciliation_engine import reconcile_purchases, MatchMethod, ReconciliationStatus
from backend.app.services.notes_reconciliation_engine import reconcile_notes
from backend.app.services.excel_exporter import export_notes_to_excel, export_purchase_to_excel
from backend.app.services.json_exporter import generate_json_report
from backend.app.services.summary_generator import generate_text_summary
from backend.app.services.amendment_resolver import resolve_amendments
from backend.app.services.cancellation_detector import detect_accounts_source_system
from backend.app.services.object_storage import persist_artifact


def _reparse_all_gstr(inspected, b2b_sheet, mapping_override=None):
    """Reparse every GST sheet, applying user mappings only to selected B2B."""
    parsed = reparse_persisted_file(inspected, RecordSource.GST)
    if mapping_override is not None and b2b_sheet:
        mapped_b2b = reparse_persisted_file(
            inspected, RecordSource.GST, b2b_sheet, mapping_override,
        )
        parsed.records = [
            row for row in parsed.records if row.source_sheet != b2b_sheet
        ] + mapped_b2b.records
    return parsed

def inspect_session_files(session_id: str) -> Dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise ValueError("Session expired or not found")
        
    accounts_path = session["files"].get("accounts")
    gstr_path = session["files"].get("gstr")
    debit_notes_path = session["files"].get("debit_notes")
    
    if not accounts_path or not gstr_path:
        raise ValueError("Missing required files for inspection")

    # 1. Parse Tally Purchases
    tally_parsed = parse_file(accounts_path, source_hint=RecordSource.TALLY)
    if tally_parsed.errors:
        raise ValueError(
            "Unable to read the Accounts file. Upload a valid .xls, .xlsx or .json file and try again."
        )
    
    # 2. Parse GSTR
    gstr_parsed = parse_file(gstr_path, source_hint=RecordSource.GST)
    if gstr_parsed.errors:
        raise ValueError(
            "Unable to read the GSTR file. Upload a valid .xls, .xlsx or .json file and try again."
        )
    
    # 3. Debit Notes
    dn_parsed = None
    if debit_notes_path:
        dn_parsed = parse_file(debit_notes_path, source_hint=RecordSource.TALLY)
        if dn_parsed.errors:
            raise ValueError(
                "Unable to read the Debit Notes file. Upload a valid .xls, .xlsx or .json file and try again."
            )

    def _find_sheet(sheets, category):
        for s in sheets:
            if s.category == category:
                return s
        return None
        
    acc_sheet = _find_sheet(tally_parsed.sheets, SheetCategory.PURCHASE_REGISTER)
    tally_sheet = acc_sheet.sheet_name if acc_sheet else None
    
    b2b_sheet = _find_sheet(gstr_parsed.sheets, SheetCategory.B2B)
    b2ba_sheet_obj = _find_sheet(gstr_parsed.sheets, SheetCategory.B2BA)
    gstr_sheet = b2b_sheet.sheet_name if b2b_sheet else None
    
    dn_sheet_obj = _find_sheet(dn_parsed.sheets, SheetCategory.DEBIT_NOTE_REGISTER) if dn_parsed else _find_sheet(tally_parsed.sheets, SheetCategory.DEBIT_NOTE_REGISTER)
    cdnr_sheet_obj = _find_sheet(gstr_parsed.sheets, SheetCategory.CDNR)
    cdnra_sheet_obj = _find_sheet(gstr_parsed.sheets, SheetCategory.CDNRA)
    
    def _extract_purchases(records, sheet_filter=None):
        return [r for r in records if r.voucher_type in (VoucherType.PURCHASE, VoucherType.UNKNOWN) and (not sheet_filter or r.source_sheet == sheet_filter)]
        
    def _extract_credit_notes(records, sheet_filter=None):
        return [r for r in records if (r.note_direction == VoucherType.CREDIT_NOTE) and (not sheet_filter or r.source_sheet == sheet_filter)]
        
    def _extract_debit_notes(records, sheet_filter=None):
        return [r for r in records if r.voucher_type == VoucherType.DEBIT_NOTE and (not sheet_filter or r.source_sheet == sheet_filter)]
        
    # Safely convert decimals to string for JSON serialization
    def _safe_preview(records, limit=10):
        preview = []
        for r in records[:limit]:
            preview.append({
                "supplier": r.supplier_name,
                "gstin": r.gstin_normalized,
                "invoice_number": r.invoice_number.original if hasattr(r.invoice_number, 'original') else r.invoice_number,
                "date": r.invoice_date.isoformat() if r.invoice_date else (r.note_date.isoformat() if hasattr(r, 'note_date') and r.note_date else None),
                "taxable_value": str(r.taxable_value),
                "igst": str(r.igst),
                "cgst": str(r.cgst),
                "sgst": str(r.sgst)
            })
        return preview

    warnings = tally_parsed.warnings + gstr_parsed.warnings
    if not acc_sheet:
        warnings.append("Unable to detect a Tally Purchase Register sheet. Verify the workbook or upload the correct Accounts file.")
    if not b2b_sheet:
        warnings.append("Unable to detect a GSTR B2B sheet. Verify the workbook or upload the correct GST file.")

    # Preview only: mirror purchase invoice presence before cancellation
    # partitioning. Keep real cancelled vouchers, and leave parser/mapping
    # records and downstream reconciliation inputs untouched.
    accounts_preview = [
        r for r in _extract_purchases(tally_parsed.records, tally_sheet)
        if r.source == RecordSource.TALLY and r.invoice_number.original
    ] if acc_sheet else []

    inspection_results = {
        "session_id": session_id,
        "files": {
            "accounts": {
                "original_filename": session["original_filenames"].get("accounts"),
                "detected_sheets": [s.sheet_name for s in tally_parsed.sheets],
                "selected_sheet": tally_sheet,
                "row_count": len(accounts_preview)
            },
            "gstr": {
                "original_filename": session["original_filenames"].get("gstr"),
                "detected_sheets": [s.sheet_name for s in gstr_parsed.sheets],
                "selected_sheet": gstr_sheet,
                "row_count": len([r for r in gstr_parsed.records if r.sheet_category in (SheetCategory.B2B, SheetCategory.B2BA)])
            }
        },
        "mappings": {
            "accounts": {m.semantic_name: m.detected_header for m in acc_sheet.column_mappings} if acc_sheet else {},
            "gstr": {m.semantic_name: m.detected_header for m in b2b_sheet.column_mappings} if b2b_sheet else {}
        },
        "available_columns": {
            "accounts": acc_sheet.headers if acc_sheet else [],
            "gstr": b2b_sheet.headers if b2b_sheet else [],
            "debit_notes": dn_sheet_obj.headers if dn_sheet_obj else [],
            "credit_notes": cdnr_sheet_obj.headers if cdnr_sheet_obj else []
        },
        "preview": {
            "accounts": _safe_preview(accounts_preview),
            "gstr": _safe_preview(_extract_purchases(gstr_parsed.records, gstr_sheet)) if b2b_sheet else [],
            "debit_notes": _safe_preview(_extract_debit_notes(dn_parsed.records if dn_parsed else tally_parsed.records, dn_sheet_obj.sheet_name if dn_sheet_obj else None)) if dn_sheet_obj else [],
            "credit_notes": _safe_preview(_extract_credit_notes(gstr_parsed.records, cdnr_sheet_obj.sheet_name if cdnr_sheet_obj else None)) if cdnr_sheet_obj else []
        },
        "warnings": warnings
    }
    inspection_results["files"]["gstr"]["amendment_sheets"] = {
        "b2ba": b2ba_sheet_obj.sheet_name if b2ba_sheet_obj else None,
        "cdnra": cdnra_sheet_obj.sheet_name if cdnra_sheet_obj else None,
    }
    inspection_results["files"]["gstr"]["raw_counts"] = {
        "b2b": len(gstr_parsed.records_by_category(SheetCategory.B2B)),
        "b2ba": len(gstr_parsed.records_by_category(SheetCategory.B2BA)),
        "cdnr": len(gstr_parsed.records_by_category(SheetCategory.CDNR)),
        "cdnra": len(gstr_parsed.records_by_category(SheetCategory.CDNRA)),
    }
    
    update_session(session_id, "inspection_results", inspection_results)
    
    # Persist one canonical row representation. Parsed records are rebuilt from
    # it so mapping overrides never need the original instance-local upload.
    tally_parsed.records = []
    gstr_parsed.records = []
    if dn_parsed:
        dn_parsed.records = []
    update_session(session_id, "parsed_tally", tally_parsed)
    update_session(session_id, "parsed_gstr", gstr_parsed)
    update_session(session_id, "parsed_dn", dn_parsed)
    _delete_raw_uploads(session_id)
    
    return inspection_results

def run_reconciliation(session_id: str, mapping_overrides: Optional[Dict] = None):
    session = get_session(session_id)
    if not session:
        raise ValueError("Session expired or not found")
        
    # Return existing if already computed
    if session.get("reconciliation_summary") and session.get("notes_summary"):
        return build_reconciliation_summary_response(session_id, session["reconciliation_summary"], session["notes_summary"])
        
    inspected_tally = session.get("parsed_tally")
    inspected_gstr = session.get("parsed_gstr")
    inspected_dn = session.get("parsed_dn")
    inspection = session.get("inspection_results", {})
    tally_sheet = inspection.get("files", {}).get("accounts", {}).get("selected_sheet")
    b2b_sheet = inspection.get("files", {}).get("gstr", {}).get("selected_sheet")
    dn_sheet = inspection.get("files", {}).get("debit_notes", {}).get("selected_sheet")

    tally_parsed = reparse_persisted_file(
        inspected_tally, RecordSource.TALLY, tally_sheet,
        (mapping_overrides or {}).get("accounts"),
    ) if inspected_tally else None
    gstr_parsed = _reparse_all_gstr(
        inspected_gstr, b2b_sheet, (mapping_overrides or {}).get("gstr"),
    ) if inspected_gstr else None
    dn_parsed = reparse_persisted_file(
        inspected_dn, RecordSource.TALLY, dn_sheet,
        (mapping_overrides or {}).get("debit_notes"),
    ) if inspected_dn else None
            
    if not tally_parsed or not gstr_parsed:
        # Re-parse if lost from memory but file exists
        raise ValueError("Parsed data missing. Please inspect again.")
        
    def _extract_purchases(records, sheet_filter=None):
        return [r for r in records if r.voucher_type in (VoucherType.PURCHASE, VoucherType.UNKNOWN) and (not sheet_filter or r.source_sheet == sheet_filter)]
        
    def _extract_credit_notes(records, sheet_filter=None):
        return [r for r in records if (r.note_direction == VoucherType.CREDIT_NOTE) and (not sheet_filter or r.source_sheet == sheet_filter)]
        
    def _extract_debit_notes(records, sheet_filter=None):
        return [r for r in records if r.voucher_type == VoucherType.DEBIT_NOTE and (not sheet_filter or r.source_sheet == sheet_filter)]
        
    # Phase 2: Purchase Reconciliation
    authoritative_purchases, purchase_amendment_stats = resolve_amendments(
        gstr_parsed.records, SheetCategory.B2B, SheetCategory.B2BA,
    )
    purchases_summary = reconcile_purchases(
        _extract_purchases(tally_parsed.records, tally_sheet), authoritative_purchases,
    )
    purchases_summary.amendment_stats = vars(purchase_amendment_stats)
    
    # Phase 3: Notes Reconciliation
    dn_sheet = inspection.get("files", {}).get("debit_notes", {}).get("selected_sheet")
    cdnr_sheet = inspection.get("files", {}).get("credit_notes", {}).get("selected_sheet")
    
    tally_dn_records = _extract_debit_notes(dn_parsed.records, dn_sheet) if dn_parsed else _extract_debit_notes(tally_parsed.records, tally_sheet)
    authoritative_notes, notes_amendment_stats = resolve_amendments(
        gstr_parsed.records, SheetCategory.CDNR, SheetCategory.CDNRA,
    )
    notes_summary = reconcile_notes(tally_dn_records, authoritative_notes)
    notes_summary.amendment_stats = vars(notes_amendment_stats)
    
    # Assign Result IDs for API usage
    manual_match_map = {}
    _assign_result_ids(purchases_summary.results, manual_match_map)
    _assign_result_ids(notes_summary.results, manual_match_map)
    
    _compact_authoritative_state(purchases_summary, notes_summary)

    # Reports are regenerated from this durable snapshot when requested. They
    # must never be required for restart recovery on an ephemeral filesystem.
    reports = {}
    def _publish_authoritative_state(stored):
        stored["reconciliation_summary"] = purchases_summary
        stored["notes_summary"] = notes_summary
        stored["manual_match_map"] = manual_match_map
        stored["parsed_tally"] = None
        stored["parsed_gstr"] = None
        stored["parsed_dn"] = None
        stored["inspection_results"] = None
        stored["reports"] = reports
    mutate_session(session_id, _publish_authoritative_state)
    
    return build_reconciliation_summary_response(session_id, purchases_summary, notes_summary)


def _delete_raw_uploads(session_id: str):
    """Remove source files once durable normalized/reconciliation state exists."""
    session = get_session(session_id)
    if not session:
        return
    for path in session.get("files", {}).values():
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                # Source files are instance-local and expire with the runtime.
                pass
    update_session(session_id, "files", {})

def _assign_result_ids(results, manual_match_map: Dict):
    for r in results:
        if not getattr(r, "api_result_id", None):
            r.api_result_id = str(uuid.uuid4())
            
        if r.status in (
            ReconciliationStatus.NEEDS_REVIEW,
            ReconciliationStatus.TAXABLE_VALUE_MISSING,
        ) and getattr(r.audit, 'candidates', []):
            if r.api_result_id not in manual_match_map:
                manual_match_map[r.api_result_id] = {}
                
            for c in r.audit.candidates:
                if not getattr(c, "api_candidate_id", None):
                    c.api_candidate_id = str(uuid.uuid4())
                # The candidate already lives authoritatively in the result's
                # audit trail. Persist only its allowed reference here.
                manual_match_map[r.api_result_id][c.api_candidate_id] = True


def _compact_authoritative_state(purchases_summary, notes_summary):
    """Drop full source dictionaries after extracting durable evidence."""
    seen = set()
    records = []
    for result in list(purchases_summary.results) + list(notes_summary.results):
        records.extend(result.audit.tally_rows)
        records.extend(result.audit.gst_rows)
        for candidate in result.audit.candidates:
            records.extend(getattr(candidate, "source_rows", []))
    for candidate in purchases_summary.not_in_tally_records:
        records.extend(candidate.source_rows)
    for candidate in notes_summary.not_in_tally_notes:
        records.extend(candidate.source_rows)
    for voucher in purchases_summary.cancelled_vouchers:
        records.extend(voucher.source_rows)

    for record in records:
        if id(record) in seen:
            continue
        seen.add(id(record))
        if record.source == RecordSource.TALLY:
            record.accounting_source_system = detect_accounts_source_system(record).value
        record.raw_data = {}

def _generate_exports(session_id: str):
    session = get_session(session_id)
    purchases_summary = session["reconciliation_summary"]
    notes_summary = session["notes_summary"]
    
    reports = _write_exports(session, purchases_summary, notes_summary)
    update_session(session_id, "reports", reports)


def _write_exports(session, purchases_summary, notes_summary):
    excel_path = session["directory"] / f"GSTR2B_Reconciliation.xlsx"
    notes_excel_path = session["directory"] / f"GSTR2B_Notes_Reconciliation.xlsx"
    json_path = session["directory"] / f"GSTR2B_Reconciliation.json"
    token = uuid.uuid4().hex
    temporary_excel = session["directory"] / f".{token}.xlsx"
    temporary_notes_excel = session["directory"] / f".{token}.notes.xlsx"
    temporary_json = session["directory"] / f".{token}.json"
    try:
        export_purchase_to_excel(str(temporary_excel), purchases_summary, "Export")
        if notes_summary.results:
            filenames = session.get("original_filenames", {})
            export_notes_to_excel(
                str(temporary_notes_excel), notes_summary, "Export",
                {
                    "Accounts source file": filenames.get("accounts"),
                    "GSTR source file": filenames.get("gstr"),
                    "Debit notes source file": filenames.get("debit_notes"),
                },
            )
        with open(temporary_json, "w", encoding="utf-8") as f:
            f.write(generate_json_report(purchases_summary, notes_summary, "Export"))
        os.replace(temporary_excel, excel_path)
        if notes_summary.results:
            os.replace(temporary_notes_excel, notes_excel_path)
        else:
            notes_excel_path.unlink(missing_ok=True)
        os.replace(temporary_json, json_path)
    finally:
        temporary_excel.unlink(missing_ok=True)
        temporary_notes_excel.unlink(missing_ok=True)
        temporary_json.unlink(missing_ok=True)
        
    session_id = str(session["id"])
    reports = {}
    reports["excel"] = persist_artifact(
        session_id, excel_path, category="reports", filename=excel_path.name,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    if notes_summary.results:
        reports["notes_excel"] = persist_artifact(
            session_id, notes_excel_path, category="reports", filename=notes_excel_path.name,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    reports["json"] = persist_artifact(
        session_id, json_path, category="reports", filename=json_path.name,
        content_type="application/json",
    )
    if any(value.startswith("s3://") for value in reports.values()):
        excel_path.unlink(missing_ok=True)
        notes_excel_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
    return reports

def _tally_source_value(result, attribute):
    """Return an actual parsed Tally source value, never an audit default."""
    rows = getattr(result.audit, "tally_rows", [])
    if not rows:
        return None
    value = getattr(rows[0], attribute, None)
    return None if value is None else str(value)

def _tally_source_date(result):
    rows = getattr(result.audit, "tally_rows", [])
    if not rows:
        return None
    value = rows[0].invoice_date or getattr(rows[0], "note_date", None)
    return value.isoformat() if value else None


def _financial_issues(rows):
    return [issue for row in rows for issue in getattr(row, "financial_parse_issues", [])]


def _safe_financial(value, issues, field):
    return None if any(issue.field_name == field for issue in issues) else (None if value is None else str(value))


def _issue_payload(issues):
    return [{"field": i.field_name, "raw_value": i.raw_value, "reason": i.reason} for i in issues]


def _result_gst_rows(result):
    if result.audit.gst_rows:
        return result.audit.gst_rows
    return [
        row for candidate in getattr(result.audit, "candidates", [])
        for row in getattr(candidate, "source_rows", [])
    ]

def _build_reconciliation_response(session_id, purchases_summary, notes_summary):
    def _cancelled(voucher):
        return {
            "id": voucher.id,
            "source_side": voucher.source_side.value,
            "source_system": voucher.source_system.value,
            "supplier": voucher.supplier,
            "gstin": voucher.gstin,
            "invoice_number": voucher.invoice_number,
            "invoice_date": voucher.invoice_date.isoformat() if voucher.invoice_date else None,
            "taxable_value": str(voucher.taxable_value),
            "igst": str(voucher.igst), "cgst": str(voucher.cgst), "sgst": str(voucher.sgst),
            "source_sheet": voucher.source_sheet, "source_row": voucher.source_row,
            "status": voucher.status, "cancellation_reason": voucher.cancellation_reason,
            "cancellation_evidence": voucher.cancellation_evidence,
        }
    response = {
        "session_id": session_id,
        **_engine_summary_payload(purchases_summary, notes_summary),
        "amendment_counts": _amendment_counts(purchases_summary, notes_summary),
        "counts": {
            "all": len(purchases_summary.results),
            "accounts_invoices": purchases_summary.total_tally_invoices,
            "gstr_invoices": purchases_summary.total_gst_invoices,
            "perfectly_matched": purchases_summary.perfectly_matched,
            "minor_rounding_difference": purchases_summary.minor_differences,
            "major_difference": purchases_summary.major_differences,
            "taxable_value_missing": purchases_summary.taxable_value_missing,
            "missing_in_gstr_2b": purchases_summary.missing_in_gstr,
            "not_in_tally": purchases_summary.not_in_tally,
            "needs_review_ambiguous": purchases_summary.needs_review,
            "potential_duplicate": purchases_summary.potential_duplicates,
            "notes_matched": len([r for r in notes_summary.results if r.status in (ReconciliationStatus.PERFECTLY_MATCHED, ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE, ReconciliationStatus.MAJOR_DIFFERENCE)]),
            "total_notes": len(notes_summary.results),
            "date_mismatches": getattr(purchases_summary, "date_mismatches", 0),
            "invoice_mismatches": getattr(purchases_summary, "invoice_mismatches", 0)
            ,"cancelled_vouchers": purchases_summary.cancelled_voucher_count
            ,"cancelled_accounts_vouchers": purchases_summary.cancelled_accounts_vouchers
            ,"cancelled_gstr_vouchers": purchases_summary.cancelled_gstr_vouchers
            ,"active_accounts_invoices": purchases_summary.active_tally_invoices
            ,"active_gstr_invoices": purchases_summary.active_gst_invoices
        },
        "results": [
            {
                "result_id": getattr(r, "api_result_id", ""),
                "status": r.status.value,
                "accounts_invoice": r.invoice_number_tally,
                "gst_invoice": r.invoice_number_gst,
                "supplier": r.supplier_tally or r.supplier_gst,
                "gstin": r.supplier_gstin,
                "accounts_supplier": _tally_source_value(r, "supplier_name"),
                "accounts_gstin": _tally_source_value(r, "gstin_original") or _tally_source_value(r, "gstin_normalized"),
                "taxable_value": None if r.taxable_value_missing else _safe_financial(r.taxable_value, _financial_issues(r.audit.tally_rows + _result_gst_rows(r)), "taxable_value"),
                "accounts_taxable": None if r.taxable_value_missing else _safe_financial(_tally_source_value(r, "taxable_value"), _financial_issues(r.audit.tally_rows), "taxable_value"),
                "gst_taxable": _safe_financial(r.audit.gst_taxable, _financial_issues(_result_gst_rows(r)), "taxable_value"),
                "accounts_gross_total": _safe_financial(r.audit.tally_gross_total, _financial_issues(r.audit.tally_rows), "gross_total"),
                "gst_invoice_value": _safe_financial(r.audit.gst_invoice_value, _financial_issues(_result_gst_rows(r)), "total_invoice_value"),
                "gross_total_difference": _safe_financial(r.audit.gross_total_difference, _financial_issues(r.audit.tally_rows + _result_gst_rows(r)), "gross_total"),
                "igst_diff": str(r.igst_diff),
                "cgst_diff": str(r.cgst_diff),
                "sgst_diff": str(r.sgst_diff),
                "accounts_igst": _safe_financial(_tally_source_value(r, "igst"), _financial_issues(r.audit.tally_rows), "igst"),
                "accounts_cgst": _safe_financial(_tally_source_value(r, "cgst"), _financial_issues(r.audit.tally_rows), "cgst"),
                "accounts_sgst": _safe_financial(_tally_source_value(r, "sgst"), _financial_issues(r.audit.tally_rows), "sgst"),
                "gst_igst": _safe_financial(r.audit.igst_comparison.gst_value, _financial_issues(_result_gst_rows(r)), "igst"),
                "gst_cgst": _safe_financial(r.audit.cgst_comparison.gst_value, _financial_issues(_result_gst_rows(r)), "cgst"),
                "gst_sgst": _safe_financial(r.audit.sgst_comparison.gst_value, _financial_issues(_result_gst_rows(r)), "sgst"),
                "accounts_date": _tally_source_date(r),
                "gst_date": (r.audit.gst_rows[0].invoice_date.isoformat() if r.audit.gst_rows and r.audit.gst_rows[0].invoice_date else (r.audit.gst_rows[0].note_date.isoformat() if r.audit.gst_rows and getattr(r.audit.gst_rows[0], "note_date", None) else None)) if hasattr(r.audit, 'gst_rows') and r.audit.gst_rows else None,
                "date_mismatch": getattr(r, "date_mismatch", False),
                "date_difference_days": getattr(r, "date_difference_days", None),
                "invoice_mismatch": getattr(r, "invoice_mismatch", False),
                "match_method": r.match_method.value,
                "remarks": r.remarks,
                "gstr_source_category": r.audit.gst_rows[0].sheet_category.value if r.audit.gst_rows else None,
                "amendment_original_number": r.audit.gst_rows[0].amendment_original_number if r.audit.gst_rows else "",
                "amendment_original_date": r.audit.gst_rows[0].amendment_original_date.isoformat() if r.audit.gst_rows and r.audit.gst_rows[0].amendment_original_date else None,
                "gstr_source_rows": [row.source_row for row in r.audit.gst_rows],
                "financial_parse_issues": _issue_payload(_financial_issues(r.audit.tally_rows + _result_gst_rows(r))),
                "candidates": [
                    {
                        "candidate_id": getattr(c, "api_candidate_id", ""),
                        "gst_invoice": c.invoice_number_original if hasattr(c, "invoice_number_original") else getattr(c, "note_number_original", ""),
                        "supplier": c.supplier_name,
                        "gstin": c.gstin,
                        "invoice_date": c.invoice_date.isoformat() if c.invoice_date else None,
                        "taxable_value": str(c.taxable_value),
                        "cgst": str(c.cgst),
                        "sgst": str(c.sgst),
                        "igst": str(c.igst)
                    } for c in getattr(r.audit, "candidates", [])
                ]
            } for r in purchases_summary.results
        ],
        "not_in_tally": [
            {
                "gst_invoice": r.invoice_number_original,
                "supplier": r.supplier_name,
                "gstin": r.gstin,
                "taxable_value": str(r.taxable_value)
            } for r in purchases_summary.not_in_tally_records
        ],
        "notes": [
            {
                "result_id": getattr(r, "api_result_id", ""),
                "gst_note_direction": r.audit.gst_rows[0].note_direction.value if r.audit.gst_rows else None,
                "accounts_note_direction": r.audit.tally_rows[0].note_direction.value if r.audit.tally_rows else None,
                "status": r.status.value,
                "accounts_invoice": r.invoice_number_tally,
                "gst_invoice": r.invoice_number_gst,
                "supplier": r.supplier_tally or r.supplier_gst,
                "gstin": r.supplier_gstin,
                "accounts_supplier": _tally_source_value(r, "supplier_name"),
                "accounts_gstin": _tally_source_value(r, "gstin_original") or _tally_source_value(r, "gstin_normalized"),
                "taxable_value": _safe_financial(r.taxable_value, _financial_issues(r.audit.tally_rows + _result_gst_rows(r)), "taxable_value"),
                "accounts_taxable": _safe_financial(_tally_source_value(r, "taxable_value"), _financial_issues(r.audit.tally_rows), "taxable_value"),
                "gst_taxable": _safe_financial(r.audit.gst_taxable, _financial_issues(_result_gst_rows(r)), "taxable_value"),
                "igst_diff": str(r.igst_diff),
                "cgst_diff": str(r.cgst_diff),
                "sgst_diff": str(r.sgst_diff),
                "accounts_igst": _safe_financial(_tally_source_value(r, "igst"), _financial_issues(r.audit.tally_rows), "igst"),
                "accounts_cgst": _safe_financial(_tally_source_value(r, "cgst"), _financial_issues(r.audit.tally_rows), "cgst"),
                "accounts_sgst": _safe_financial(_tally_source_value(r, "sgst"), _financial_issues(r.audit.tally_rows), "sgst"),
                "gst_igst": _safe_financial(r.audit.igst_comparison.gst_value, _financial_issues(_result_gst_rows(r)), "igst"),
                "gst_cgst": _safe_financial(r.audit.cgst_comparison.gst_value, _financial_issues(_result_gst_rows(r)), "cgst"),
                "gst_sgst": _safe_financial(r.audit.sgst_comparison.gst_value, _financial_issues(_result_gst_rows(r)), "sgst"),
                "accounts_date": _tally_source_date(r),
                "gst_date": (r.audit.gst_rows[0].invoice_date.isoformat() if r.audit.gst_rows and r.audit.gst_rows[0].invoice_date else (r.audit.gst_rows[0].note_date.isoformat() if r.audit.gst_rows and getattr(r.audit.gst_rows[0], "note_date", None) else None)) if hasattr(r.audit, 'gst_rows') and r.audit.gst_rows else None,
                "match_method": r.match_method.value,
                "remarks": r.remarks,
                "gstr_source_category": r.audit.gst_rows[0].sheet_category.value if r.audit.gst_rows else None,
                "amendment_original_number": r.audit.gst_rows[0].amendment_original_number if r.audit.gst_rows else "",
                "amendment_original_date": r.audit.gst_rows[0].amendment_original_date.isoformat() if r.audit.gst_rows and r.audit.gst_rows[0].amendment_original_date else None,
                "gstr_source_rows": [row.source_row for row in r.audit.gst_rows],
                "financial_parse_issues": _issue_payload(_financial_issues(r.audit.tally_rows + _result_gst_rows(r))),
                "candidates": [
                    {
                        "candidate_id": getattr(c, "api_candidate_id", ""),
                        "gst_invoice": getattr(c, "note_number_original", ""),
                        "supplier": c.supplier_name,
                        "gstin": c.gstin,
                        "invoice_date": c.note_date.isoformat() if c.note_date else None,
                        "taxable_value": str(c.taxable_value),
                        "cgst": str(c.cgst),
                        "sgst": str(c.sgst),
                        "igst": str(c.igst)
                    } for c in getattr(r.audit, "candidates", [])
                ]
            } for r in notes_summary.results
        ],
        "cancelled_vouchers": [_cancelled(v) for v in purchases_summary.cancelled_vouchers],
        "summary_text": generate_text_summary(purchases_summary, notes_summary),
        "has_ambiguous_matches": purchases_summary.needs_review > 0 or notes_summary.needs_review > 0
    }

    from backend.app.services.result_pagination import serialize_result
    for payload, result in zip(response["results"], purchases_summary.results):
        payload.update(serialize_result(result))
    for payload, result in zip(response["notes"], notes_summary.results):
        payload.update(serialize_result(result, note=True))
    return response


def build_reconciliation_summary_response(session_id, purchases_summary, notes_summary):
    """Return dashboard metadata without materializing the full result arrays."""
    summary_text = generate_text_summary(purchases_summary, notes_summary).split("\n--- ", 1)[0].rstrip()
    return {
        "session_id": session_id,
        **_engine_summary_payload(purchases_summary, notes_summary),
        "amendment_counts": _amendment_counts(purchases_summary, notes_summary),
        "counts": {
            "all": len(purchases_summary.results),
            "accounts_invoices": purchases_summary.total_tally_invoices,
            "gstr_invoices": purchases_summary.total_gst_invoices,
            "perfectly_matched": purchases_summary.perfectly_matched,
            "minor_rounding_difference": purchases_summary.minor_differences,
            "major_difference": purchases_summary.major_differences,
            "taxable_value_missing": purchases_summary.taxable_value_missing,
            "missing_in_gstr_2b": purchases_summary.missing_in_gstr,
            "not_in_tally": purchases_summary.not_in_tally,
            "needs_review_ambiguous": purchases_summary.needs_review,
            "potential_duplicate": purchases_summary.potential_duplicates,
            "notes_matched": len([r for r in notes_summary.results if r.status in (
                ReconciliationStatus.PERFECTLY_MATCHED,
                ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE,
                ReconciliationStatus.MAJOR_DIFFERENCE,
            )]),
            "total_notes": len(notes_summary.results),
            "date_mismatches": purchases_summary.date_mismatches,
            "invoice_mismatches": purchases_summary.invoice_mismatches,
            "cancelled_vouchers": purchases_summary.cancelled_voucher_count,
            "cancelled_accounts_vouchers": purchases_summary.cancelled_accounts_vouchers,
            "cancelled_gstr_vouchers": purchases_summary.cancelled_gstr_vouchers,
            "active_accounts_invoices": purchases_summary.active_tally_invoices,
            "active_gstr_invoices": purchases_summary.active_gst_invoices,
        },
        "results": [], "not_in_tally": [], "notes": [], "cancelled_vouchers": [],
        "summary_text": summary_text,
        "has_ambiguous_matches": purchases_summary.needs_review > 0 or notes_summary.needs_review > 0,
    }


def _engine_summary_payload(purchases_summary, notes_summary):
    """Separate run-level counts without changing the legacy flat count contract."""
    return {
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
            # Legacy sessions/tests may deserialize the notes partition as the
            # older generic ReconciliationSummary.  Preserve that contract.
            "total_debit_notes": getattr(
                notes_summary, "total_tally_debit_notes",
                getattr(notes_summary, "total_tally_invoices", 0),
            ),
            "total_gstr_credit_notes": getattr(
                notes_summary, "total_gst_credit_notes",
                getattr(notes_summary, "total_gst_invoices", 0),
            ),
            "perfect": notes_summary.perfectly_matched,
            "minor": notes_summary.minor_differences,
            "major": notes_summary.major_differences,
            "missing_in_gstr": getattr(
                notes_summary, "missing_in_gst",
                getattr(notes_summary, "missing_in_gstr", 0),
            ),
            "missing_in_debit_notes": notes_summary.not_in_tally,
            "needs_review": notes_summary.needs_review,
            "potential_duplicate": notes_summary.potential_duplicates,
        },
    }


def _amendment_counts(purchases_summary, notes_summary):
    def _one(summary, authoritative_documents):
        stats = getattr(summary, "amendment_stats", None)
        if stats is None:
            result = {
                "raw_regular_rows": 0, "raw_amendment_rows": 0,
                "superseded_regular_documents": 0, "authoritative_rows": 0,
            }
        elif isinstance(stats, dict):
            result = dict(stats)
        else:
            result = {
                "raw_regular_rows": stats.raw_regular_rows,
                "raw_amendment_rows": stats.raw_amendment_rows,
                "superseded_regular_documents": stats.superseded_regular_documents,
                "authoritative_rows": stats.authoritative_rows,
            }
        result["authoritative_documents"] = authoritative_documents
        return result
    return {
        "purchases": _one(purchases_summary, purchases_summary.total_gst_invoices),
        "notes": _one(notes_summary, getattr(notes_summary, "total_gst_credit_notes", 0)),
    }
