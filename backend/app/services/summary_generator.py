from backend.app.models.reconciliation import ReconciliationSummary, ReconciliationStatus
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary


def generate_text_summary(
    purchases_summary: ReconciliationSummary,
    notes_summary: NotesReconciliationSummary
) -> str:
    """
    Create a clean text summary of the reconciliation results.
    """
    lines = []
    lines.append("Reconciliation completed successfully.")
    lines.append("")
    lines.append(f"Accounts invoices: {purchases_summary.total_tally_invoices}")
    lines.append(f"GSTR-2B invoices: {purchases_summary.total_gst_invoices}")
    lines.append("")
    lines.append(f"Perfectly matched: {purchases_summary.perfectly_matched}")
    lines.append(f"Minor differences: {purchases_summary.minor_differences}")
    lines.append(f"Major differences: {purchases_summary.major_differences}")
    lines.append(f"Taxable Value Missing: {purchases_summary.taxable_value_missing}")
    lines.append(f"Missing in GSTR-2B: {purchases_summary.missing_in_gstr}")
    lines.append(f"Present in GSTR-2B but not in Tally: {purchases_summary.not_in_tally}")
    lines.append(f"Needs manual review: {purchases_summary.needs_review}")
    lines.append(f"Potential duplicates: {purchases_summary.potential_duplicates}")
    lines.append(f"Cancelled Vouchers: {purchases_summary.cancelled_voucher_count}")
    lines.append(f"  Accounts: {purchases_summary.cancelled_accounts_vouchers}")
    lines.append(f"  GSTR: {purchases_summary.cancelled_gstr_vouchers}")
    if hasattr(purchases_summary, "invoice_mismatches") and purchases_summary.invoice_mismatches > 0:
        lines.append(f"Invoice Number Mismatches: {purchases_summary.invoice_mismatches}")
    lines.append("")
    
    missing_invoices = [
        r for r in purchases_summary.results 
        if r.status == ReconciliationStatus.MISSING_IN_GSTR
    ]
    if missing_invoices:
        lines.append("--- Missing Invoices ---")
        for m in missing_invoices:
            lines.append(f"- Invoice {m.invoice_number_tally} from {m.supplier_tally} (GSTIN: {m.supplier_gstin})")
        lines.append("")
        
    tax_diffs = [
        r for r in purchases_summary.results 
        if r.status == ReconciliationStatus.MAJOR_DIFFERENCE
    ]
    if tax_diffs:
        lines.append("--- Major Differences ---")
        for t in tax_diffs:
            lines.append(f"- Invoice {t.invoice_number_tally} from {t.supplier_tally}: {t.remarks}")
        lines.append("")
        
    return "\n".join(lines)
