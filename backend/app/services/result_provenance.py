"""Side-specific provenance for result presentation, including one-sided rows."""
from backend.app.services.cancellation_detector import detect_accounts_source_system
from backend.app.services.normalizer import normalize_invoice_number


def explain_cancelled_counterpart(result, cancelled_vouchers):
    """Annotate unique exact-GSTIN/exact-date document evidence only.

    Cancelled records never enter candidates, consumption, or classification.
    A suffix additionally needs all four financial values within the existing
    Minor tolerance. No supplier-name/fuzzy cancellation linking is permitted.
    """
    if result.status.value != "Missing in GSTR-2B" or not result.audit.tally_rows:
        return
    tally = result.audit.tally_rows[0]
    if not tally.gstin_normalized or not tally.invoice_date or not tally.invoice_number.normalized:
        return
    pool = [v for v in cancelled_vouchers
            if v.source_side.value == "GSTR" and v.gstin == tally.gstin_normalized
            and v.invoice_date == tally.invoice_date]
    matches = [v for v in pool
               if normalize_invoice_number(v.invoice_number).normalized == tally.invoice_number.normalized]
    if not matches:
        # Use the frozen engine's suffix eligibility and financial classifier;
        # operate only on separate explanatory objects, never active candidates.
        from backend.app.models.reconciliation import AggregatedGSTInvoice, ReconciliationStatus
        from backend.app.services.reconciliation_engine import _find_last_n_match, _classify_status, _make_tax_comparison
        eligible = []
        for voucher in pool:
            info = normalize_invoice_number(voucher.invoice_number)
            candidate = AggregatedGSTInvoice(
                invoice_number_last_4=info.last_4, invoice_number_last_2=info.last_2,
                invoice_date=voucher.invoice_date, taxable_value=voucher.taxable_value,
                igst=voucher.igst, cgst=voucher.cgst, sgst=voucher.sgst,
            )
            financial = _classify_status(tally.taxable_value - voucher.taxable_value,
                *[_make_tax_comparison(head.upper(), getattr(tally, head), getattr(voucher, head))
                  for head in ("igst", "cgst", "sgst")])
            if (financial != ReconciliationStatus.MAJOR_DIFFERENCE and not tally.financial_parse_issues
                    and not any(row.financial_parse_issues for row in voucher.source_rows)):
                eligible.append((voucher, candidate))
        for length in (4, 2):
            matches = [v for v, c in eligible if _find_last_n_match(
                tally, getattr(tally.invoice_number, f"last_{length}"), [c], length)]
            if matches:
                break
    if len(matches) != 1:
        return
    voucher = matches[0]
    result.cancelled_gstr_counterpart = True
    result.cancelled_gstr_source_sheet = voucher.source_sheet
    result.cancelled_gstr_source_rows = [r.source_row for r in voucher.source_rows]
    result.remarks = "Matching GSTR invoice was found but is marked cancelled and was excluded from active reconciliation."
    result.audit.explanation = result.remarks


def result_provenance(result):
    accounts = result.audit.tally_rows
    gst = result.audit.gst_rows
    a = accounts[0] if accounts else None
    g = gst[0] if gst else None
    system = detect_accounts_source_system(a).value if a else None
    return {
        "source_system": system or ("GSTR" if g else None),
        "source_sheet": a.source_sheet if a else (g.source_sheet if g else None),
        "source_row": a.source_row if a else (g.source_row if g else None),
        "accounts_source_system": system,
        "accounts_source_sheet": a.source_sheet if a else None,
        "accounts_source_row": a.source_row if a else None,
        "gstr_source_category": g.sheet_category.value if g else None,
        "gstr_source_sheet": g.source_sheet if g else None,
        "gstr_source_rows": [r.source_row for r in gst],
        "gst_note_direction": g.note_direction.value if g and g.note_number else None,
        "accounts_note_direction": a.note_direction.value if a and a.note_direction.value != "Unknown" else None,
        "cancelled_gstr_counterpart": result.cancelled_gstr_counterpart,
        "cancelled_gstr_source_sheet": result.cancelled_gstr_source_sheet,
        "cancelled_gstr_source_rows": result.cancelled_gstr_source_rows,
    }
