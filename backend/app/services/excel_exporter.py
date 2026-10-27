from decimal import Decimal
from typing import Any, Dict, List, Optional
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from backend.app.models.reconciliation import (
    ReconciliationSummary, ReconciliationStatus, MatchMethod, ReconciliationResult, AuditTrail
)
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary
from backend.app.services.normalizer import normalize_invoice_number


# --- Styles ---
HEADER_FILL = PatternFill(start_color="2F75B5", end_color="2F75B5", fill_type="solid")
HEADER_FONT = Font(name="Arial", size=11, bold=True, color="FFFFFF")
DEFAULT_FONT = Font(name="Arial", size=10)
BOLD_FONT = Font(name="Arial", size=10, bold=True)
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
THIN_BORDER = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin')
)

STATUS_COLORS = {
    ReconciliationStatus.PERFECTLY_MATCHED: "C6EFCE",          # Green
    ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE: "FFEB9C",  # Yellow
    ReconciliationStatus.MAJOR_DIFFERENCE: "FFC7CE",             # Orange/Light Red
    ReconciliationStatus.MISSING_IN_GSTR: "FF9999",            # Red
    ReconciliationStatus.NOT_IN_TALLY: "FFCCCC",               # Light Red/Pink
    ReconciliationStatus.NEEDS_REVIEW: "E4DFEC",               # Light Purple / Amber
    ReconciliationStatus.POTENTIAL_DUPLICATE: "D9D9D9",        # Grey
    ReconciliationStatus.TAXABLE_VALUE_MISSING: "FCE4D6",      # Data-quality orange
}
STATUS_FILLS = {
    status: PatternFill(start_color=color, end_color=color, fill_type="solid")
    for status, color in STATUS_COLORS.items()
}


def _apply_cached_style(cell, key, *, font, alignment, border, fill=None, number_format=None):
    """Assign an immutable composite style once per workbook/style variant."""
    workbook = cell.parent.parent
    cache = getattr(workbook, "_export_style_cache", None)
    if cache is None:
        cache = {}
        workbook._export_style_cache = cache

    style = cache.get(key)
    if style is None:
        cell.font = font
        cell.alignment = alignment
        cell.border = border
        if fill is not None:
            cell.fill = fill
        if number_format is not None:
            cell.number_format = number_format
        cache[key] = cell._style
    else:
        # openpyxl styles are immutable after assignment, so sharing the
        # registered StyleArray avoids hashing identical components per cell.
        cell._style = style


def _issues_from_rows(rows):
    return [issue for row in rows for issue in getattr(row, "financial_parse_issues", [])]


def _safe_financial(value, issues, field_name):
    issue = next((item for item in issues if item.field_name == field_name), None)
    return f"Invalid: {issue.raw_value}" if issue else ("—" if value is None else value)


def _result_issues(result):
    gst_rows = result.audit.gst_rows or [
        row for candidate in getattr(result.audit, "candidates", [])
        for row in getattr(candidate, "source_rows", [])
    ]
    return _issues_from_rows(result.audit.tally_rows) + _issues_from_rows(gst_rows)


def _apply_header_style(ws, row_idx: int, col_count: int):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_idx, column=col)
        _apply_cached_style(
            cell, ("header",), font=HEADER_FONT, alignment=ALIGN_CENTER,
            border=THIN_BORDER, fill=HEADER_FILL,
        )


def _apply_row_style(
    ws, row_idx: int, col_count: int,
    status: Optional[ReconciliationStatus] = None, is_bold: bool = False,
    *, header_row: int = 1,
):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row_idx, column=col)
        fill = STATUS_FILLS.get(status)
        header = str(ws.cell(row=header_row, column=col).value or "")
        is_number = (
            isinstance(cell.value, (Decimal, int, float))
            and not isinstance(cell.value, bool)
        )
        is_non_currency_number = (
            header == "#"
            or "count" in header.casefold()
            or "row" in header.casefold()
            or "difference (days)" in header.casefold()
        )
        if is_number and is_non_currency_number:
            kind = "number"
            alignment = ALIGN_RIGHT
            number_format = '#,##0'
        elif is_number:
            kind = "money"
            alignment = ALIGN_RIGHT
            number_format = '"₹"#,##0.00'
        elif "Date" in header:
            kind = "date"
            alignment = ALIGN_CENTER
            number_format = 'DD-MM-YYYY'
        else:
            # Preserve formats that openpyxl inferred while appending values
            # (notably dates in multi-section sheets whose header is not row 1).
            kind = ("text", cell.number_format)
            alignment = ALIGN_LEFT
            number_format = cell.number_format
        _apply_cached_style(
            cell,
            ("row", status, is_bold, kind),
            font=BOLD_FONT if is_bold else DEFAULT_FONT,
            alignment=alignment,
            border=THIN_BORDER,
            fill=fill,
            number_format=number_format,
        )


def _auto_adjust_columns(ws):
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter 
        for cell in col:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except:
                pass
        
        # Leave enough room for table filters and formatted row numbers so
        # Excel does not render narrow cells as ####.
        adjusted_width = max(8, min(max_length + 2, 40)) # Cap at 40
        ws.column_dimensions[column].width = adjusted_width


def export_to_excel(
    file_path: str,
    purchases_summary: ReconciliationSummary,
    notes_summary: NotesReconciliationSummary,
    reporting_period: str
):
    """Generate Excel report."""
    for result in purchases_summary.results + notes_summary.results:
        result.refresh_source_values()
    wb = Workbook()
    
    # Remove default sheet
    if "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])
        
    _create_summary_sheet(wb, purchases_summary)
    _create_missing_diff_sheet(wb, purchases_summary)
    _create_taxable_value_missing_sheet(wb, purchases_summary)
    _create_date_mismatches_sheet(wb, purchases_summary)
    _create_invoice_mismatches_sheet(wb, purchases_summary)
    _create_cancelled_vouchers_sheet(wb, purchases_summary)
    _create_invoice_remarks_sheet(wb, purchases_summary)
    _create_not_in_tally_sheet(wb, purchases_summary)
    _create_notes_sheet(wb, notes_summary)
    
    wb.save(file_path)


def export_purchase_to_excel(
    file_path: str,
    purchases_summary: ReconciliationSummary,
    reporting_period: str,
):
    """Generate a categorized, audit-ready purchase workbook."""
    for result in purchases_summary.results:
        result.refresh_source_values()
    wb = Workbook()
    wb.remove(wb["Sheet"])
    ordered_results = sorted(purchases_summary.results, key=_purchase_result_sort_key)
    _create_purchase_summary_sheet(wb, purchases_summary, reporting_period)
    _create_purchase_category_sheet(wb, "All Results", ordered_results)
    categories = [
        ("Perfect", lambda result: result.status == ReconciliationStatus.PERFECTLY_MATCHED, "both"),
        ("Minor", lambda result: result.status == ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE, "compact_match"),
        ("Major", lambda result: result.status == ReconciliationStatus.MAJOR_DIFFERENCE, "compact_match"),
        ("Invoice Mismatch", lambda result: result.status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH, "compact_match"),
        ("Date Mismatch", lambda result: result.date_mismatch, "both"),
        ("Missing in GSTR", lambda result: result.status == ReconciliationStatus.MISSING_IN_GSTR, "accounts"),
        ("Missing in Accounts", lambda result: result.status == ReconciliationStatus.NOT_IN_TALLY, "gstr"),
        ("Needs Review", lambda result: result.status == ReconciliationStatus.NEEDS_REVIEW, "both"),
        ("Taxable Missing", lambda result: result.status == ReconciliationStatus.TAXABLE_VALUE_MISSING, "both"),
        ("Potential Duplicate", lambda result: result.status == ReconciliationStatus.POTENTIAL_DUPLICATE, "potential_duplicate"),
    ]
    for sheet_name, predicate, source_view in categories:
        _create_purchase_category_sheet(
            wb, sheet_name, [result for result in ordered_results if predicate(result)],
            source_view=source_view,
        )
    _create_cancelled_vouchers_sheet(wb, purchases_summary)
    # Keep the two historical sheet names as hidden compatibility views for
    # integrations that still read them. The visible report above is the
    # categorized workbook users should work from.
    _create_summary_sheet(wb, purchases_summary)
    wb["Reconciliation Summary"].sheet_state = "hidden"
    _create_invoice_remarks_sheet(wb, purchases_summary)
    wb["Invoice No Remarks"].sheet_state = "hidden"
    wb.save(file_path)


_PURCHASE_STATUS_PRIORITY = {
    ReconciliationStatus.NEEDS_REVIEW: 0,
    ReconciliationStatus.POTENTIAL_DUPLICATE: 1,
    ReconciliationStatus.TAXABLE_VALUE_MISSING: 2,
    ReconciliationStatus.MISSING_IN_GSTR: 3,
    ReconciliationStatus.NOT_IN_TALLY: 4,
    ReconciliationStatus.INVOICE_NUMBER_MISMATCH: 5,
    ReconciliationStatus.MAJOR_DIFFERENCE: 6,
    ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE: 7,
    ReconciliationStatus.PERFECTLY_MATCHED: 8,
}


def _purchase_result_sort_key(result: ReconciliationResult):
    """Keep attention items first, then use stable business identifiers."""
    invoice_number = result.invoice_number_tally or result.invoice_number_gst or ""
    invoice_date = result.tally_date or result.gstr_date or result.invoice_date
    return (
        _PURCHASE_STATUS_PRIORITY.get(result.status, 99),
        str(result.supplier_gstin or "").casefold(),
        str(result.supplier_tally or result.supplier_gst or "").casefold(),
        str(invoice_number).casefold(),
        invoice_date.isoformat() if invoice_date else "",
    )


def _create_purchase_summary_sheet(
    wb: Workbook, summary: ReconciliationSummary, reporting_period: str,
):
    ws = wb.create_sheet("Summary")
    ws.append(["Purchase Reconciliation Report"])
    ws["A1"].font = Font(name="Arial", size=16, bold=True, color="2F5D3A")
    ws.append(["Reporting period", reporting_period])
    ws.append(["Accounts invoices", summary.total_tally_invoices])
    ws.append(["GSTR-2B invoices", summary.total_gst_invoices])
    ws.append([])
    ws.append(["Category", "Count", "Review priority"])
    header_row = ws.max_row
    _apply_header_style(ws, header_row, 3)
    category_rows = [
        ("Needs Review", summary.needs_review, "1 - Review first"),
        ("Potential Duplicate", summary.potential_duplicates, "2"),
        ("Taxable Missing", summary.taxable_value_missing, "3"),
        ("Missing in GSTR", summary.missing_in_gstr, "4"),
        ("Missing in Accounts", summary.not_in_tally, "5"),
        ("Invoice Mismatch", summary.invoice_mismatches, "6"),
        ("Major", summary.major_differences, "7"),
        ("Date Mismatch", summary.date_mismatches, "Supporting signal"),
        ("Minor", summary.minor_differences, "8"),
        ("Perfect", summary.perfectly_matched, "9"),
        ("Cancelled Vouchers", summary.cancelled_voucher_count, "Separate population"),
    ]
    for row_idx, row in enumerate(category_rows, header_row + 1):
        ws.append(row)
        _apply_row_style(ws, row_idx, 3, header_row=header_row)
    ws.append([])
    ws.append(["Scope", (
        "Reconciliation results reflect data matching between Accounts and GSTR records only. "
        "They do not determine ITC eligibility or GST compliance."
    )])
    ws.cell(ws.max_row, 2).alignment = ALIGN_LEFT
    ws.freeze_panes = f"A{header_row + 1}"
    ws.sheet_view.showGridLines = False
    _auto_adjust_columns(ws)


_PURCHASE_AUDIT_HEADERS = [
    "#", "Supplier GSTIN", "Supplier (Accounts)", "Supplier (GSTR-2B)",
    "Invoice No. (Accounts)", "Invoice No. (GSTR-2B)",
    "Normalized Invoice (Accounts)", "Normalized Invoice (GSTR-2B)",
    "Accounts Date", "GSTR Date", "Date Difference (Days)",
    "Taxable Value (Accounts)", "Taxable Value (GSTR-2B)",
    "Gross Total (Accounts)", "Invoice Value (GSTR-2B)",
    "IGST (Accounts)", "IGST (GSTR-2B)", "CGST (Accounts)", "CGST (GSTR-2B)",
    "SGST (Accounts)", "SGST (GSTR-2B)", "Taxable Difference", "Gross Total Difference",
    "IGST Difference", "CGST Difference", "SGST Difference",
    "Match Method", "Status", "Remarks", "Evidence",
    "GSTR Category", "GSTR Source Sheet", "GSTR Source Rows",
    "Accounts Source Sheet", "Accounts Source Rows",
]

_PURCHASE_COMPACT_MATCH_HEADERS = [
    header for header in _PURCHASE_AUDIT_HEADERS
    if header not in {
        "Supplier (Accounts)", "Invoice No. (Accounts)",
        "Normalized Invoice (GSTR-2B)", "GSTR Date", "Evidence",
        "GSTR Source Sheet", "GSTR Source Rows",
        "Accounts Source Sheet", "Accounts Source Rows",
    }
]

_PURCHASE_POTENTIAL_DUPLICATE_HEADERS = [
    header for header in _PURCHASE_AUDIT_HEADERS
    if header not in {
        "Evidence", "GSTR Source Sheet", "GSTR Source Rows",
        "Accounts Source Sheet", "Accounts Source Rows",
    }
]

_PURCHASE_ACCOUNTS_ONLY_HEADERS = [
    "#", "Supplier GSTIN (Accounts)", "Supplier (Accounts)",
    "Invoice No. (Accounts)", "Accounts Date",
    "Taxable Value (Accounts)", "Gross Total (Accounts)",
    "IGST (Accounts)", "CGST (Accounts)", "SGST (Accounts)",
    "Match Method", "Status", "Remarks",
]

_PURCHASE_GSTR_ONLY_HEADERS = [
    "#", "Supplier GSTIN (GSTR-2B)", "Supplier (GSTR-2B)",
    "Invoice No. (GSTR-2B)", "GSTR Date",
    "Taxable Value (GSTR-2B)", "Invoice Value (GSTR-2B)",
    "IGST (GSTR-2B)", "CGST (GSTR-2B)", "SGST (GSTR-2B)",
    "Match Method", "Status", "Remarks", "GSTR Category",
]


def _purchase_accounts_only_row(row_number, result, issues):
    audit = result.audit
    return [
        row_number, result.supplier_gstin, result.supplier_tally,
        result.invoice_number_tally, result.tally_date,
        _safe_financial(audit.tally_taxable, issues, "taxable_value"),
        _safe_financial(audit.tally_gross_total, issues, "gross_total"),
        _safe_financial(audit.igst_comparison.tally_value, issues, "igst"),
        _safe_financial(audit.cgst_comparison.tally_value, issues, "cgst"),
        _safe_financial(audit.sgst_comparison.tally_value, issues, "sgst"),
        result.match_method.value, result.status.value, _invoice_remarks_explanation(result),
    ]


def _purchase_gstr_only_row(row_number, result, issues):
    audit = result.audit
    gst_row = audit.gst_rows[0] if audit.gst_rows else None
    return [
        row_number, result.supplier_gstin, result.supplier_gst,
        result.invoice_number_gst, result.gstr_date,
        _safe_financial(audit.gst_taxable, issues, "taxable_value"),
        _safe_financial(audit.gst_invoice_value, issues, "total_invoice_value"),
        _safe_financial(audit.igst_comparison.gst_value, issues, "igst"),
        _safe_financial(audit.cgst_comparison.gst_value, issues, "cgst"),
        _safe_financial(audit.sgst_comparison.gst_value, issues, "sgst"),
        result.match_method.value, result.status.value, _invoice_remarks_explanation(result),
        gst_row.sheet_category.value if gst_row else "—",
    ]


def _purchase_audit_row(row_number, result, issues):
    audit = result.audit
    gst_row = audit.gst_rows[0] if audit.gst_rows else None
    accounts_row = audit.tally_rows[0] if audit.tally_rows else None
    return [
        row_number, result.supplier_gstin, result.supplier_tally, result.supplier_gst,
        result.invoice_number_tally, result.invoice_number_gst,
        audit.tally_invoice_normalized, audit.gst_invoice_normalized,
        result.tally_date, result.gstr_date, result.date_difference_days,
        _safe_financial(audit.tally_taxable, issues, "taxable_value"),
        _safe_financial(audit.gst_taxable, issues, "taxable_value"),
        _safe_financial(audit.tally_gross_total, issues, "gross_total"),
        _safe_financial(audit.gst_invoice_value, issues, "total_invoice_value"),
        _safe_financial(audit.igst_comparison.tally_value, issues, "igst"),
        _safe_financial(audit.igst_comparison.gst_value, issues, "igst"),
        _safe_financial(audit.cgst_comparison.tally_value, issues, "cgst"),
        _safe_financial(audit.cgst_comparison.gst_value, issues, "cgst"),
        _safe_financial(audit.sgst_comparison.tally_value, issues, "sgst"),
        _safe_financial(audit.sgst_comparison.gst_value, issues, "sgst"),
        _safe_financial(audit.taxable_difference, issues, "taxable_value"),
        _safe_financial(audit.gross_total_difference, issues, "gross_total"),
        _safe_financial(result.igst_diff, issues, "igst"),
        _safe_financial(result.cgst_diff, issues, "cgst"),
        _safe_financial(result.sgst_diff, issues, "sgst"),
        result.match_method.value, result.status.value, result.remarks, audit.explanation,
        gst_row.sheet_category.value if gst_row else "—",
        gst_row.source_sheet if gst_row else "—",
        ", ".join(str(row.source_row) for row in audit.gst_rows),
        accounts_row.source_sheet if accounts_row else "—",
        ", ".join(str(row.source_row) for row in audit.tally_rows),
    ]


def _project_purchase_audit_row(headers, row_number, result, issues, *, merge_remarks=False):
    values = dict(zip(_PURCHASE_AUDIT_HEADERS, _purchase_audit_row(row_number, result, issues)))
    if merge_remarks:
        values["Remarks"] = _invoice_remarks_explanation(result)
    return [values[header] for header in headers]


def _is_export_blank(value):
    return value is None or (isinstance(value, str) and value.strip() in {"", "—"})


def _prune_empty_data_columns(ws, data_start_row=2):
    """Remove columns with no meaningful data while retaining the table's populated schema."""
    for col_idx in range(ws.max_column, 0, -1):
        values = (
            ws.cell(row=row_idx, column=col_idx).value
            for row_idx in range(data_start_row, ws.max_row + 1)
        )
        if all(_is_export_blank(value) for value in values):
            ws.delete_cols(col_idx)


def _create_purchase_category_sheet(
    wb: Workbook, sheet_name: str, results: List[ReconciliationResult], *, source_view: str = "both",
):
    headers = {
        "both": _PURCHASE_AUDIT_HEADERS,
        "compact_match": _PURCHASE_COMPACT_MATCH_HEADERS,
        "potential_duplicate": _PURCHASE_POTENTIAL_DUPLICATE_HEADERS,
        "accounts": _PURCHASE_ACCOUNTS_ONLY_HEADERS,
        "gstr": _PURCHASE_GSTR_ONLY_HEADERS,
    }[source_view]
    ws = wb.create_sheet(sheet_name)
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    if not results:
        ws.append([f"No {sheet_name.lower()} records"])
        ws.cell(2, 1).font = Font(name="Arial", color="008000")
    for row_idx, result in enumerate(results, 2):
        result.refresh_source_values()
        audit = result.audit
        issues = _result_issues(result)
        if source_view == "accounts":
            row = _purchase_accounts_only_row(row_idx - 1, result, issues)
        elif source_view == "gstr":
            row = _purchase_gstr_only_row(row_idx - 1, result, issues)
        else:
            row = _project_purchase_audit_row(
                headers, row_idx - 1, result, issues,
                merge_remarks=source_view in {"compact_match", "potential_duplicate"},
            )
        ws.append(row)
        _apply_row_style(ws, row_idx, len(headers), result.status)
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False
    if results and source_view == "potential_duplicate":
        _prune_empty_data_columns(ws)
    if results:
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{len(results) + 1}"
    _auto_adjust_columns(ws)


def export_notes_to_excel(
    file_path: str,
    notes_summary: NotesReconciliationSummary,
    reporting_period: str,
    run_metadata: Optional[Dict[str, Any]] = None,
):
    """Generate an audit-ready workbook containing Notes Engine data only."""
    for result in notes_summary.results:
        result.refresh_source_values()
    wb = Workbook()
    wb.remove(wb["Sheet"])
    _create_notes_summary_sheet(wb, notes_summary, reporting_period, run_metadata or {})
    categories = [
        ("Perfect", {ReconciliationStatus.PERFECTLY_MATCHED}),
        ("Minor", {ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE}),
        ("Tax Mismatch", {ReconciliationStatus.MAJOR_DIFFERENCE}),
        ("Missing in GSTR", {ReconciliationStatus.MISSING_IN_GSTR}),
        ("Missing in Debit Notes", {ReconciliationStatus.NOT_IN_TALLY}),
        ("Needs Review", {
            ReconciliationStatus.NEEDS_REVIEW,
            ReconciliationStatus.TAXABLE_VALUE_MISSING,
            ReconciliationStatus.POTENTIAL_DUPLICATE,
        }),
    ]
    for sheet_name, statuses in categories:
        _create_notes_category_sheet(
            wb, sheet_name,
            [result for result in notes_summary.results if result.status in statuses],
        )
    wb.save(file_path)


def _create_notes_summary_sheet(wb: Workbook, summary: NotesReconciliationSummary, reporting_period: str,
                                run_metadata: Dict[str, Any]):
    ws = wb.create_sheet("Summary")
    ws.append(["Notes Reconciliation Report"])
    ws["A1"].font = Font(name="Arial", size=16, bold=True, color="2F5D3A")
    ws.append(["Reporting period", reporting_period])
    ws.append(["Accounts debit notes", summary.total_tally_debit_notes])
    ws.append(["GSTR credit notes", summary.total_gst_credit_notes])
    for label, value in run_metadata.items():
        if value:
            ws.append([label, value])
    ws.append([])
    ws.append(["Category", "Count"])
    header_row = ws.max_row
    _apply_header_style(ws, header_row, 2)
    rows = [
        ("Perfect", summary.perfectly_matched),
        ("Minor", summary.minor_differences),
        ("Tax Mismatch", summary.major_differences),
        ("Missing in GSTR", summary.missing_in_gst),
        ("Missing in Debit Notes", summary.not_in_tally),
        ("Needs Review", summary.needs_review),
        ("Potential Duplicate", summary.potential_duplicates),
    ]
    for row_idx, row in enumerate(rows, header_row + 1):
        ws.append(row)
        _apply_row_style(ws, row_idx, 2)
    ws.freeze_panes = f"A{header_row + 1}"
    _auto_adjust_columns(ws)


_NOTES_AUDIT_HEADERS = [
    "#", "Supplier GSTIN", "Supplier (Accounts)", "Supplier (GSTR)",
    "Debit Note No. (Accounts)", "Credit Note No. (GSTR)",
    "Normalized Debit Note No.", "Normalized Credit Note No.",
    "Accounts Date", "GSTR Date", "Taxable Value (Accounts)", "Taxable Value (GSTR)",
    "IGST (Accounts)", "IGST (GSTR)", "CGST (Accounts)", "CGST (GSTR)",
    "SGST (Accounts)", "SGST (GSTR)", "Taxable Difference", "IGST Difference",
    "CGST Difference", "SGST Difference", "Match Method", "Status", "Remarks",
    "GST Category", "GST Source Sheet", "GST Source Rows",
    "Accounts Source Sheet", "Accounts Source Rows",
]


def _create_notes_category_sheet(wb: Workbook, sheet_name: str, results: List[ReconciliationResult]):
    ws = wb.create_sheet(sheet_name)
    ws.append(_NOTES_AUDIT_HEADERS)
    _apply_header_style(ws, 1, len(_NOTES_AUDIT_HEADERS))
    if not results:
        ws.append([f"No {sheet_name.lower()} records"])
        ws.cell(2, 1).font = Font(name="Arial", color="008000")
    for row_idx, result in enumerate(results, 2):
        result.refresh_source_values()
        audit = result.audit
        issues = _result_issues(result)
        gst_row = audit.gst_rows[0] if audit.gst_rows else None
        accounts_row = audit.tally_rows[0] if audit.tally_rows else None
        ws.append([
            row_idx - 1, result.supplier_gstin, result.supplier_tally, result.supplier_gst,
            result.invoice_number_tally, result.invoice_number_gst,
            audit.tally_invoice_normalized, audit.gst_invoice_normalized,
            result.tally_date, result.gstr_date,
            _safe_financial(audit.tally_taxable, issues, "taxable_value"),
            _safe_financial(audit.gst_taxable, issues, "taxable_value"),
            _safe_financial(audit.igst_comparison.tally_value, issues, "igst"),
            _safe_financial(audit.igst_comparison.gst_value, issues, "igst"),
            _safe_financial(audit.cgst_comparison.tally_value, issues, "cgst"),
            _safe_financial(audit.cgst_comparison.gst_value, issues, "cgst"),
            _safe_financial(audit.sgst_comparison.tally_value, issues, "sgst"),
            _safe_financial(audit.sgst_comparison.gst_value, issues, "sgst"),
            _safe_financial(audit.taxable_difference, issues, "taxable_value"),
            _safe_financial(result.igst_diff, issues, "igst"),
            _safe_financial(result.cgst_diff, issues, "cgst"),
            _safe_financial(result.sgst_diff, issues, "sgst"),
            result.match_method.value, result.status.value, result.remarks,
            gst_row.sheet_category.value if gst_row else "—",
            gst_row.source_sheet if gst_row else "—",
            ", ".join(str(row.source_row) for row in audit.gst_rows),
            accounts_row.source_sheet if accounts_row else "—",
            ", ".join(str(row.source_row) for row in audit.tally_rows),
        ])
        _apply_row_style(ws, row_idx, len(_NOTES_AUDIT_HEADERS), result.status)
    ws.freeze_panes = "A2"
    if results:
        ws.auto_filter.ref = f"A1:AD{len(results) + 1}"
    _auto_adjust_columns(ws)


def _create_summary_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Reconciliation Summary")
    headers = [
        "#", "Supplier GSTIN", "Supplier (Accounts)", "Supplier (GSTR-2B)",
        "Invoice No (Accounts)", "Invoice No (GSTR-2B)", "Invoice Date",
        "Taxable Value (₹)", "CGST Diff (₹)", "SGST Diff (₹)", "IGST Diff (₹)",
        "Status", "Match Method", "Remarks"
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    
    source_headers = [
        "IGST (Accounts)", "CGST (Accounts)", "SGST (Accounts)",
        "IGST (GSTR)", "CGST (GSTR)", "SGST (GSTR)", "Taxable Diff",
        "Gross Total (Accounts)", "Invoice Value (GSTR)", "Gross Total Diff",
    ]
    for column, title in enumerate(source_headers, 19):
        cell = ws.cell(1, column, title)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    row_idx = 2
    for i, res in enumerate(summary.results, 1):
        if res.status == ReconciliationStatus.NOT_IN_TALLY:
            continue # Goes to Sheet 4
            
        issues = _result_issues(res)
        row_data = [
            i,
            res.supplier_gstin,
            res.supplier_tally,
            res.supplier_gst,
            res.invoice_number_tally,
            res.invoice_number_gst,
            res.invoice_date,
            "—" if res.taxable_value_missing else _safe_financial(res.taxable_value, issues, "taxable_value"),
            _safe_financial(res.cgst_diff, issues, "cgst"),
            _safe_financial(res.sgst_diff, issues, "sgst"),
            _safe_financial(res.igst_diff, issues, "igst"),
            res.status.value,
            res.match_method.value,
            res.remarks
        ]
        ws.append(row_data)
        values = [getattr(res.audit, f"{head}_comparison").tally_value for head in ("igst", "cgst", "sgst")] + [getattr(res.audit, f"{head}_comparison").gst_value for head in ("igst", "cgst", "sgst")] + [res.taxable_difference, res.audit.tally_gross_total, res.audit.gst_invoice_value, res.audit.gross_total_difference]
        value_fields = ("igst", "cgst", "sgst", "igst", "cgst", "sgst", "taxable_value", "gross_total", "total_invoice_value", "gross_total")
        for column, value in enumerate(values, 19):
            ws.cell(row_idx, column, _safe_financial(value, issues, value_fields[column - 19]))
        _apply_row_style(ws, row_idx, 28, res.status)
        row_idx += 1
        
    # Totals Row
    ws.append(["TOTALS", "", "", "", "", "", ""])
    _apply_row_style(ws, row_idx, len(headers), is_bold=True)
    if row_idx > 2:
        ws.cell(row=row_idx, column=8).value = f"=SUM(H2:H{row_idx-1})"
        ws.cell(row=row_idx, column=9).value = f"=SUM(I2:I{row_idx-1})"
        ws.cell(row=row_idx, column=10).value = f"=SUM(J2:J{row_idx-1})"
        ws.cell(row=row_idx, column=11).value = f"=SUM(K2:K{row_idx-1})"
    ws["P1"] = "Cancelled Vouchers"
    ws["Q1"] = summary.cancelled_voucher_count
    ws["P2"] = "Cancelled Accounts"
    ws["Q2"] = summary.cancelled_accounts_vouchers
    ws["P3"] = "Cancelled GSTR"
    ws["Q3"] = summary.cancelled_gstr_vouchers
    ws["P4"] = "Taxable Value Missing"
    ws["Q4"] = summary.taxable_value_missing
    ws["P6"] = "Important"
    ws["Q6"] = (
        "Reconciliation results reflect data matching between Accounts and GSTR records only. "
        "They do not determine ITC eligibility or GST compliance. "
        "Review eligibility separately before claiming ITC."
    )
    ws["Q6"].alignment = ALIGN_LEFT
    for cell in (ws["P1"], ws["P2"], ws["P3"], ws["P4"], ws["P6"]):
        cell.font = BOLD_FONT
    
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:N{row_idx-1}"
    _auto_adjust_columns(ws)


def _create_missing_diff_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Missing & Differences")
    
    # SECTION 1
    ws.append(["Invoices in Accounts but Missing in GSTR-2B"])
    ws.cell(row=1, column=1).font = BOLD_FONT
    
    headers1 = [
        "#", "Supplier", "GSTIN", "Voucher No.", "Date", 
        "Taxable Value", "IGST", "CGST", "SGST", "Reason"
    ]
    ws.append(headers1)
    _apply_header_style(ws, 2, len(headers1))
    
    row_idx = 3
    missing_idx = 1
    missing = [r for r in summary.results if r.status == ReconciliationStatus.MISSING_IN_GSTR]
    
    if not missing:
        ws.append(["No missing invoices"])
        row_idx = ws.max_row
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000") # Green
    else:
        start_row = ws.max_row
        for res in missing:
            row_data = [
                missing_idx, res.supplier_tally, res.supplier_gstin, res.invoice_number_tally,
                res.invoice_date, res.taxable_value, res.audit.tally_rows[0].igst if res.audit.tally_rows else Decimal("0"),
                res.audit.tally_rows[0].cgst if res.audit.tally_rows else Decimal("0"),
                res.audit.tally_rows[0].sgst if res.audit.tally_rows else Decimal("0"), res.remarks
            ]
            ws.append(row_data)
            row_idx = ws.max_row
            _apply_row_style(ws, row_idx, len(headers1), res.status)
            missing_idx += 1
            
        end_row = ws.max_row
        tab1 = Table(displayName="MissingInvoices", ref=f"A{start_row}:J{end_row}")
        tab1.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        ws.add_table(tab1)

    # SECTION 2
    ws.append([]) # blank
    ws.append(["Major Differences"])
    row_idx = ws.max_row
    ws.cell(row=row_idx, column=1).font = BOLD_FONT
    
    headers2 = [
        "#", "Supplier", "GSTIN", "Invoice No.", "Financial Field", 
        "Accounts Value", "GSTR-2B Value", "Difference", "Status"
    ]
    ws.append(headers2)
    row_idx = ws.max_row
    _apply_header_style(ws, row_idx, len(headers2))
    
    diff_idx = 1
    diffs = [r for r in summary.results if r.status == ReconciliationStatus.MAJOR_DIFFERENCE]
    
    if not diffs:
        ws.append(["No major differences"])
        row_idx = ws.max_row
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        start_row2 = row_idx
        for res in diffs:
            # Check Taxable Value
            taxable_diff = res.audit.taxable_difference if hasattr(res.audit, 'taxable_difference') else Decimal("0.00")
            taxable_tally = res.audit.tally_taxable if hasattr(res.audit, 'tally_taxable') else Decimal("0.00")
            taxable_gst = res.audit.gst_taxable if hasattr(res.audit, 'gst_taxable') else Decimal("0.00")
            
            comparisons = [
                ("Taxable Value", taxable_tally, taxable_gst, taxable_diff),
                ("IGST", res.audit.igst_comparison.tally_value, res.audit.igst_comparison.gst_value, res.audit.igst_comparison.difference) if res.audit.igst_comparison else None,
                ("CGST", res.audit.cgst_comparison.tally_value, res.audit.cgst_comparison.gst_value, res.audit.cgst_comparison.difference) if res.audit.cgst_comparison else None,
                ("SGST", res.audit.sgst_comparison.tally_value, res.audit.sgst_comparison.gst_value, res.audit.sgst_comparison.difference) if res.audit.sgst_comparison else None,
            ]
            
            for cmp in comparisons:
                if cmp:
                    head, t_val, g_val, diff = cmp
                    if diff is not None and abs(diff) > Decimal("1.00"):
                        row_data = [
                            diff_idx, res.supplier_tally, res.supplier_gstin, res.invoice_number_tally,
                            head, t_val, g_val, diff, res.status.value
                        ]
                        ws.append(row_data)
                        row_idx = ws.max_row
                        _apply_row_style(ws, row_idx, len(headers2), res.status)
                        diff_idx += 1
                    
        end_row2 = row_idx
        tab2 = Table(displayName="MajorDifferences", ref=f"A{start_row2}:I{end_row2}")
        tab2.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        ws.add_table(tab2)
                    
    ws.freeze_panes = "A3"
    _auto_adjust_columns(ws)


def _create_taxable_value_missing_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Taxable Value Missing")
    headers = [
        "#", "Supplier", "GSTIN", "Accounts Invoice", "GSTR Invoice",
        "Accounts Date", "GSTR Date", "Accounts Taxable Value",
        "GSTR Taxable Value", "IGST (Accounts)", "CGST (Accounts)",
        "SGST (Accounts)", "Match Method", "Status", "Reason",
        "Source Sheet", "Source Row",
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    missing = [
        r for r in summary.results
        if r.status == ReconciliationStatus.TAXABLE_VALUE_MISSING
    ]
    if not missing:
        ws.append(["No Accounts invoices have a missing taxable value"])
        ws.cell(row=2, column=1).font = Font(name="Arial", color="008000")
    else:
        for index, res in enumerate(missing, 1):
            tally = res.audit.tally_rows[0] if res.audit.tally_rows else None
            ws.append([
                index, res.supplier_tally, res.supplier_gstin,
                res.invoice_number_tally, res.invoice_number_gst,
                res.tally_date or (tally.invoice_date if tally else None),
                res.gstr_date, "—",
                res.audit.gst_taxable if res.audit.gst_rows else None,
                tally.igst if tally else Decimal("0.00"),
                tally.cgst if tally else Decimal("0.00"),
                tally.sgst if tally else Decimal("0.00"),
                res.match_method.value, res.status.value, res.remarks,
                tally.source_sheet if tally else "",
                tally.source_row if tally else None,
            ])
            _apply_row_style(ws, ws.max_row, len(headers), res.status)
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = f"A1:Q{ws.max_row}"
    _auto_adjust_columns(ws)


def _create_invoice_mismatches_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Invoice Mismatches")
    headers = [
        "#", "Supplier", "GSTIN", "Tally Invoice", "GSTR Invoice",
        "Invoice Date", "Taxable Value", "IGST", "CGST", "SGST",
        "Reason", "Status"
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    
    mismatches = [r for r in summary.results if r.status == ReconciliationStatus.INVOICE_NUMBER_MISMATCH]
    row_idx = 2
    if not mismatches:
        ws.append(["No invoice mismatches"])
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        for i, res in enumerate(mismatches, 1):
            row_data = [
                i, res.supplier_tally, res.supplier_gstin, res.invoice_number_tally,
                res.invoice_number_gst, res.invoice_date, res.taxable_value, 
                res.audit.tally_rows[0].igst if res.audit.tally_rows else Decimal("0"),
                res.audit.tally_rows[0].cgst if res.audit.tally_rows else Decimal("0"),
                res.audit.tally_rows[0].sgst if res.audit.tally_rows else Decimal("0"),
                res.remarks, res.status.value
            ]
            ws.append(row_data)
            _apply_row_style(ws, row_idx, len(headers), res.status)
            row_idx += 1
            
        tab = Table(displayName="InvoiceMismatches", ref=f"A1:L{row_idx-1}")
        tab.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        ws.add_table(tab)
        
    ws.freeze_panes = "A2"
    _auto_adjust_columns(ws)


def _create_date_mismatches_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Date Mismatches")
    headers = [
        "#", "Supplier", "GSTIN", "Tally Invoice", "GSTR Invoice",
        "Tally Date", "GSTR Date", "Date Difference",
        "Taxable Value", "IGST Diff", "CGST Diff", "SGST Diff",
        "Match Method", "Financial Status"
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    
    mismatches = [r for r in summary.results if getattr(r, 'date_mismatch', False)]
    row_idx = 2
    if not mismatches:
        ws.append(["No date mismatches"])
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        for i, res in enumerate(mismatches, 1):
            diff_str = f"{res.date_difference_days} days" if res.date_difference_days is not None else ""
            row_data = [
                i, res.supplier_tally, res.supplier_gstin, res.invoice_number_tally,
                res.invoice_number_gst, res.tally_date, res.gstr_date, diff_str,
                res.taxable_value, res.igst_diff, res.cgst_diff, res.sgst_diff,
                res.match_method.value, res.status.value
            ]
            ws.append(row_data)
            _apply_row_style(ws, row_idx, len(headers), res.status)
            row_idx += 1
            
    ws.freeze_panes = "A2"
    if row_idx > 2:
        ws.auto_filter.ref = f"A1:N{row_idx-1}"
    _auto_adjust_columns(ws)

def _invoice_remarks_explanation(res: ReconciliationResult) -> str:
    """Compose canonical match evidence and status context without duplication."""
    evidence = res.audit.explanation
    warning = res.remarks
    if evidence and warning and evidence != warning:
        return f"{evidence} {warning}"
    return evidence or warning


def _create_invoice_remarks_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Invoice No Remarks")
    headers = [
        "#", "Supplier", "GSTIN", "Invoice No. (Accounts)", 
        "Invoice No. (GSTR-2B)", "Match Method", "Explanation"
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    
    remarks = [
        r for r in summary.results 
        if r.match_method in (MatchMethod.NORMALIZED_INVOICE, MatchMethod.INVOICE_LAST_4, MatchMethod.INVOICE_LAST_2)
    ]
    
    row_idx = 2
    if not remarks:
        ws.append(["No invoice number remarks"])
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        for i, res in enumerate(remarks, 1):
            expl = _invoice_remarks_explanation(res)
                
            row_data = [
                i, res.supplier_tally, res.supplier_gstin, res.invoice_number_tally,
                res.invoice_number_gst, res.match_method.value, expl
            ]
            ws.append(row_data)
            _apply_row_style(ws, row_idx, len(headers), res.status)
            row_idx += 1
            
    ws.freeze_panes = "A2"
    if row_idx > 2:
        ws.auto_filter.ref = f"A1:G{row_idx-1}"
    _auto_adjust_columns(ws)


def _create_cancelled_vouchers_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Cancelled Vouchers")
    headers = [
        "#", "Supplier", "GSTIN", "Invoice/Voucher No.", "Invoice Date",
        "Taxable Value", "IGST", "CGST", "SGST", "Source", "Source System",
        "Status", "Reason", "Evidence", "Source Sheet", "Source Row",
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    row_idx = 2
    if not summary.cancelled_vouchers:
        ws.append(["No cancelled vouchers"])
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        for index, voucher in enumerate(summary.cancelled_vouchers, 1):
            ws.append([
                index, voucher.supplier, voucher.gstin, voucher.invoice_number,
                voucher.invoice_date, voucher.taxable_value, voucher.igst,
                voucher.cgst, voucher.sgst, voucher.source_side.value,
                voucher.source_system.value, voucher.status,
                voucher.cancellation_reason, voucher.cancellation_evidence,
                voucher.source_sheet, voucher.source_row,
            ])
            _apply_row_style(ws, row_idx, len(headers))
            row_idx += 1
    ws.freeze_panes = "A2"
    if row_idx > 2:
        ws.auto_filter.ref = f"A1:P{row_idx - 1}"
    _auto_adjust_columns(ws)


def _create_not_in_tally_sheet(wb: Workbook, summary: ReconciliationSummary):
    ws = wb.create_sheet("Not in Tally")
    headers = [
        "#", "Supplier GSTIN", "Supplier Name", "Invoice No.", "Invoice Date",
        "Taxable Value", "IGST", "CGST", "SGST", "Total Tax", "Remark"
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    
    row_idx = 2
    if not summary.not_in_tally_records:
        ws.append(["No GST invoices missing in Tally"])
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        for i, agg in enumerate(summary.not_in_tally_records, 1):
            issues = getattr(agg, "financial_parse_issues", [])
            row_data = [
                i, agg.gstin, agg.supplier_name, agg.invoice_number_original,
                agg.invoice_date, _safe_financial(agg.taxable_value, issues, "taxable_value"),
                _safe_financial(agg.igst, issues, "igst"),
                _safe_financial(agg.cgst, issues, "cgst"),
                _safe_financial(agg.sgst, issues, "sgst"),
                "—" if issues else agg.total_tax(),
                issues[0].reason if issues else "Present in GSTR-2B but not found in Tally"
            ]
            ws.append(row_data)
            _apply_row_style(ws, row_idx, len(headers), ReconciliationStatus.NOT_IN_TALLY)
            row_idx += 1
            
    ws.freeze_panes = "A2"
    if row_idx > 2:
        ws.auto_filter.ref = f"A1:K{row_idx-1}"
    _auto_adjust_columns(ws)


def _create_notes_sheet(wb: Workbook, summary: NotesReconciliationSummary):
    ws = wb.create_sheet("Notes")
    headers = [
        "#", "Supplier GSTIN", "Supplier (Tally)", "Supplier (GST)",
        "Debit Note No. (Tally)", "Credit Note No. (GST)", "Tally Date",
        "GST Note Date", "Taxable Value (Tally)", "Taxable Value (GST)",
        "IGST Diff", "CGST Diff", "SGST Diff", "Match Method", "Status", "Remarks",
        "IGST (Accounts)", "CGST (Accounts)", "SGST (Accounts)",
        "IGST (GST)", "CGST (GST)", "SGST (GST)",
        "GST Note Direction", "GST Category", "GST Source Sheet", "GST Source Rows",
        "Accounts Source Sheet", "Accounts Source Rows",
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    
    row_idx = 2
    represented_unmatched = {
        (res.supplier_gstin, normalize_invoice_number(res.invoice_number_gst).normalized)
        for res in summary.results
        if res.status == ReconciliationStatus.NOT_IN_TALLY
    }
    compatibility_unmatched = [
        ReconciliationResult(
            supplier_gstin=agg.gstin,
            supplier_gst=agg.supplier_name,
            invoice_number_gst=agg.note_number_original,
            invoice_date=agg.note_date,
            taxable_value=agg.taxable_value,
            status=ReconciliationStatus.NOT_IN_TALLY,
            match_method=MatchMethod.NO_MATCH,
            remarks="GST credit note not matched to any Tally debit note",
            audit=AuditTrail(gst_rows=agg.source_rows),
        )
        for agg in summary.not_in_tally_notes
        if not agg.consumed
        and (agg.gstin, agg.note_number_normalized) not in represented_unmatched
    ]
    all_notes = summary.results + compatibility_unmatched
    
    if not all_notes:
        ws.append(["No notes available"])
        ws.cell(row=row_idx, column=1).font = Font(name="Arial", color="008000")
    else:
        for i, res in enumerate(all_notes, 1):
            res.refresh_source_values()
            issues = _result_issues(res)
            row_data = [
                i, res.supplier_gstin, res.supplier_tally, res.supplier_gst,
                res.invoice_number_tally, res.invoice_number_gst,
                res.tally_date,
                res.gstr_date,
                _safe_financial(res.audit.tally_taxable, issues, "taxable_value") if hasattr(res, 'audit') else Decimal("0.00"),
                _safe_financial(res.audit.gst_taxable, issues, "taxable_value"),
                _safe_financial(res.igst_diff, issues, "igst"),
                _safe_financial(res.cgst_diff, issues, "cgst"),
                _safe_financial(res.sgst_diff, issues, "sgst"),
                res.match_method.value, res.status.value, res.remarks,
                *[_safe_financial(getattr(res.audit, f"{head}_comparison").tally_value, issues, head) for head in ("igst", "cgst", "sgst")],
                *[_safe_financial(getattr(res.audit, f"{head}_comparison").gst_value, issues, head) for head in ("igst", "cgst", "sgst")],
                res.audit.gst_rows[0].note_direction.value if res.audit.gst_rows else "—",
                res.audit.gst_rows[0].sheet_category.value if res.audit.gst_rows else "—",
                res.audit.gst_rows[0].source_sheet if res.audit.gst_rows else "—",
                ", ".join(str(row.source_row) for row in res.audit.gst_rows),
                res.audit.tally_rows[0].source_sheet if res.audit.tally_rows else "—",
                ", ".join(str(row.source_row) for row in res.audit.tally_rows),
            ]
            ws.append(row_data)
            _apply_row_style(ws, row_idx, len(headers), res.status)
            row_idx += 1
            
    ws.freeze_panes = "A2"
    if row_idx > 2:
        ws.auto_filter.ref = f"A1:AB{row_idx-1}"
    _auto_adjust_columns(ws)
