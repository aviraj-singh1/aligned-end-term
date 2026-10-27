import pytest
import io
import uuid
import json
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)

@pytest.fixture
def ambiguous_excel():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Register"
    ws.append(["Voucher No.", "Date", "Particulars", "GSTIN/UIN", "Value", "Igst", "CGST", "SGST"])
    ws.append(["TLLY1234", "2025-04-01", "Supplier XYZ", "27AAAFK9103A1Z1", 1000, 0, 90, 90])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

@pytest.fixture
def ambiguous_gstr_excel():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "B2B"
    ws.append(["Invoice No.", "Invoice Date", "Party Name", "GSTIN/UIN", "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"])
    # 2 distinct invoices with same last 4 digits matching Tally
    ws.append(["ABCD1234", "01-04-2025", "Supplier XYZ", "27AAAFK9103A1Z1", 1000, 0, 90, 90])
    ws.append(["WXYZ1234", "01-04-2025", "Supplier XYZ", "27AAAFK9103A1Z1", 1000, 0, 90, 90])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def test_stable_api_ids_and_manual_match(ambiguous_excel, ambiguous_gstr_excel):
    # 1. Upload
    res_inspect = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xlsx", ambiguous_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR.xlsx", ambiguous_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    session_id = res_inspect.json()["session_id"]
    
    # 2. Reconcile
    res_recon = client.post(f"/api/reconcile?session_id={session_id}", json={})
    recon_data = res_recon.json()
    
    result = client.get(f"/api/session/{session_id}/results?category=needs_review").json()["items"][0]
    result_id = result["result_id"]
    candidate_id = result["candidates"][0]["candidate_id"]
    
    # Verify ID is a UUID (not memory address)
    try:
        uuid.UUID(result_id)
        uuid.UUID(candidate_id)
    except ValueError:
        pytest.fail("IDs are not valid UUIDs")
        
    # Verify identical IDs on repeat call
    res_recon2 = client.post(f"/api/reconcile?session_id={session_id}", json={})
    recon_data2 = res_recon2.json()
    result2 = client.get(f"/api/session/{session_id}/results?category=needs_review").json()["items"][0]
    assert result2["result_id"] == result_id
    assert result2["candidates"][0]["candidate_id"] == candidate_id
    
    # Wrong result_id + valid candidate_id
    res_mm_wrong = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": str(uuid.uuid4()), "candidate_id": candidate_id})
    assert res_mm_wrong.status_code in [400, 404]
    
    # Wrong candidate_id + valid result_id
    res_mm_wrong2 = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": result_id, "candidate_id": str(uuid.uuid4())})
    assert res_mm_wrong2.status_code in [400, 404]
    
    # Another session trying to use this candidate
    res_inspect_new = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook2.xlsx", ambiguous_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR2.xlsx", ambiguous_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    session_id_new = res_inspect_new.json()["session_id"]
    res_mm_wrong_session = client.post(f"/api/manual-match?session_id={session_id_new}", json={"result_id": result_id, "candidate_id": candidate_id})
    assert res_mm_wrong_session.status_code in [400, 404]
    
    # Valid manual match
    res_mm = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": result_id, "candidate_id": candidate_id})
    assert res_mm.status_code == 200
    assert res_mm.json()["counts"]["perfectly_matched"] == 1
    assert res_mm.json()["counts"]["needs_review_ambiguous"] == 0
    perfect = client.get(f"/api/session/{session_id}/results?category=perfect").json()
    assert any(r["match_method"] == "Manual Match" for r in perfect["items"])
    
    # Consumed candidate rejected
    res_mm_consumed = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": result_id, "candidate_id": candidate_id})
    assert res_mm_consumed.status_code == 409
    
    # Export reflects it
    res_json = client.get(f"/api/export/{session_id}/json")
    data = res_json.json()
    assert data["counts"]["perfectly_matched"] == 1
    assert data["counts"]["needs_review_ambiguous"] == 0
    manual = next(r for r in data["reconciliation_summary"] if r["match_method"] == "Manual Match")
    assert manual["date_mismatch"] is False
    assert manual["invoice_mismatch"] is False
    assert not any(r["invoice_number"] == manual["invoice_number_gst"] for r in data["not_in_tally"])
