import os
import tempfile
import pandas as pd
from datetime import date
from decimal import Decimal

from backend.app.services.session_manager import create_session
from backend.app.services.reconciliation_orchestrator import inspect_session_files, run_reconciliation
from backend.app.services.result_pagination import paginate_results
from backend.app.models.reconciliation import ReconciliationStatus
from backend.app.models.records import ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType

def create_mock_excel(path, sheets_data):
    with pd.ExcelWriter(path) as writer:
        for sheet_name, data in sheets_data.items():
            df = pd.DataFrame(data)
            df.to_excel(writer, sheet_name=sheet_name, index=False)

def test_mapping_override_api_regression():
    # Prove that mapping override for GSTIN to None works and no GSTIN leaks
    with tempfile.TemporaryDirectory() as tmpdir:
        tally_path = os.path.join(tmpdir, "tally.xlsx")
        gst_path = os.path.join(tmpdir, "gst.xlsx")
        
        # Tally has GSTIN column, but we will override it to None
        create_mock_excel(tally_path, {
            "Purchase Register": [
                {"Date": "01-04-2025", "Particulars": "SYNTHETIC SUPPLIER LTD", "Voucher Type": "Purchase", "Voucher No.": "17852", "GSTIN/UIN": "07MOCKX1234A1Z5", "Value": 1000}
            ]
        })
        
        create_mock_excel(gst_path, {
            "b2b": [
                {"GSTIN/UIN": "07MOCKX1234A1Z5", "Party Name": "SYNTHETIC SUPPLIER LIMITED", "Invoice No.": "SSIP-17852", "Invoice Date": "01-04-2025", "Invoice Type": "Regular", "Taxable Value": 1000}
            ]
        })
        
        session_id = create_session()
        from backend.app.services.session_manager import update_session
        update_session(session_id, "files", {"accounts": tally_path, "gstr": gst_path})
        update_session(session_id, "original_filenames", {"accounts": "tally.xlsx", "gstr": "gst.xlsx"})
        
        inspect_session_files(session_id)
        
        # Override GSTIN to None
        mapping_overrides = {"accounts": {"gstin": None}}
        
        response = run_reconciliation(session_id, mapping_overrides=mapping_overrides)
        
        # The reconcile response is lightweight; the authoritative result remains paged in-session.
        assert response["results"] == []
        
        # Since GSTIN is unmapped, engine falls back to name + last-4 match (or exact date etc)
        # Because the tally value is unmapped, tally_gstin must be blank!
        # The frontend result exposes 'gstin' which is the supplier_gstin (coalesced).
        # We need to assert the audit trail specifically.
        
        from backend.app.services.session_manager import get_session
        s = get_session(session_id)
        purchases_summary = s["reconciliation_summary"]
        page = paginate_results(purchases_summary, s["notes_summary"], page_size=25)
        assert len(page["items"]) == 1
        
        result_obj = purchases_summary.results[0]
        assert not result_obj.audit.tally_gstin, "Tally GSTIN should be None due to override"
        assert result_obj.audit.supplier_match == "name_fallback", "Should fallback to supplier name match"

def test_b2b_sheet_isolation_regression():
    # Prove that b2csa records do not bleed into B2B counts
    with tempfile.TemporaryDirectory() as tmpdir:
        tally_path = os.path.join(tmpdir, "tally.xlsx")
        gst_path = os.path.join(tmpdir, "gst.xlsx")
        
        create_mock_excel(tally_path, {
            "Purchase Register": []
        })
        
        # GST has 2 B2B records and 1 B2CSA record
        create_mock_excel(gst_path, {
            "b2b": [
                {"GSTIN/UIN": "06AAA", "Party Name": "A", "Invoice No.": "INV1", "Invoice Date": "01-04-2025", "Taxable Value": 100},
                {"GSTIN/UIN": "06BBB", "Party Name": "B", "Invoice No.": "INV2", "Invoice Date": "01-04-2025", "Taxable Value": 200}
            ],
            "b2csa": [
                {"Type": "OE", "Place Of Supply": "06-Haryana", "Taxable Value": 500} # Unknown voucher
            ]
        })
        
        session_id = create_session()
        from backend.app.services.session_manager import update_session
        update_session(session_id, "files", {"accounts": tally_path, "gstr": gst_path})
        update_session(session_id, "original_filenames", {"accounts": "tally.xlsx", "gstr": "gst.xlsx"})
        
        inspect_session_files(session_id)
        response = run_reconciliation(session_id)
        
        counts = response["counts"]
        assert counts["gstr_invoices"] == 2, "B2CSA record should be isolated"
        assert counts["not_in_tally"] == 2, "Only 2 B2B invoices should be not in tally"

def test_notes_kpi_regression():
    # Prove that notes_matched only includes actual matches, not MISSING_IN_GSTR or NOT_IN_TALLY
    from backend.app.models.reconciliation import ReconciliationResult, ReconciliationStatus
    from backend.app.models.reconciliation import ReconciliationSummary
    from backend.app.services.reconciliation_orchestrator import _build_reconciliation_response
    
    # 1. 71 GST credit notes, all NOT_IN_TALLY
    results_71 = []
    for i in range(71):
        r = ReconciliationResult()
        r.status = ReconciliationStatus.NOT_IN_TALLY
        r.invoice_number_gst = f"CN-{i}"
        results_71.append(r)
        
    summary_71 = ReconciliationSummary(results=results_71)
    
    # Empty purchases summary
    purchases_summary = ReconciliationSummary()
    
    resp_71 = _build_reconciliation_response("dummy", purchases_summary, summary_71)
    
    assert resp_71["counts"]["notes_matched"] == 0, "Matched notes should be 0 when all are Not in Tally"
    
    # 2. Mixed fixture: 2 Perfect, 1 Minor, 1 Major, 3 Not in Tally
    mixed = []
    mixed.extend([ReconciliationResult(status=ReconciliationStatus.PERFECTLY_MATCHED)] * 2)
    mixed.append(ReconciliationResult(status=ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE))
    mixed.append(ReconciliationResult(status=ReconciliationStatus.MAJOR_DIFFERENCE))
    mixed.extend([ReconciliationResult(status=ReconciliationStatus.NOT_IN_TALLY)] * 3)
    
    summary_mixed = ReconciliationSummary(results=mixed)
    resp_mixed = _build_reconciliation_response("dummy", purchases_summary, summary_mixed)
    
    assert resp_mixed["counts"]["notes_matched"] == 4, "Only Perfectly Matched, Minor, and Major count as matched"
