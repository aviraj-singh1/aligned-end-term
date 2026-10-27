import pytest
import os
import io
import json
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.session_manager import get_session

client = TestClient(app)

@pytest.fixture
def dummy_excel():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Register"
    ws.append(["Voucher No.", "Date", "Particulars", "GSTIN/UIN", "Value", "Igst", "CGST", "SGST"])
    ws.append(["INV001", "2025-04-01", "Test Supplier", "06DEMOX1234A1Z5", 1000, 0, 90, 90])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

@pytest.fixture
def dummy_gstr_excel():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "B2B"
    ws.append(["Invoice No.", "Invoice Date", "Party Name", "GSTIN/UIN", "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"])
    ws.append(["INV001", "01-04-2025", "Test Supplier", "06DEMOX1234A1Z5", 1000, 0, 90, 90])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

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

@pytest.fixture
def dummy_json():
    data = [
        {
            "Voucher No.": "JINV001",
            "Date": "2025-04-01",
            "Particulars": "Supplier A",
            "GSTIN/UIN": "06DEMOX1234A1Z5",
            "Value": 1000,
            "Igst": 0, "CGST": 90, "SGST": 90
        }
    ]
    return json.dumps(data).encode("utf-8")

def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Aligned GST Reconciliation"}


@pytest.mark.parametrize("filename,mime,valid_bytes,expected_status", [
    ("4a.xlsx", "", True, 200),
    ("4a.xlsx", "application/octet-stream", True, 200),
    ("4a.XLSX", "application/octet-stream", True, 200),
    ("4a", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True, 200),
    ("Account Source", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True, 200),
    ("Account Source", "", True, 200),
    ("Account Source", "application/octet-stream", True, 200),
    ("4a.exe", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True, 415),
    # Reader selection follows the workbook container, not a misleading suffix.
    ("4a.xls", "application/vnd.ms-excel", True, 200),
    ("4a.xlsx", "application/octet-stream", False, 400),
    ("4a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False, 400),
])
def test_mobile_upload_metadata_does_not_bypass_content_validation(
    filename, mime, valid_bytes, expected_status, dummy_excel, dummy_gstr_excel, monkeypatch, tmp_path,
):
    from backend.app.services import session_manager
    from backend.app.services.session_store import InMemorySessionStore
    monkeypatch.setattr(session_manager, "_MANAGER", session_manager.SessionManager(
        InMemorySessionStore(), base_temp_dir=tmp_path,
    ))
    response = client.post("/api/inspect", files={
        "accounts_file": (filename, dummy_excel if valid_bytes else b"not a workbook", mime),
        "gstr_file": ("gstr.xlsx", dummy_gstr_excel, "application/octet-stream"),
    })
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["files"]["accounts"]["row_count"] == 1
        assert len(response.json()["preview"]["gstr"]) == 1
    elif not valid_bytes:
        assert response.json()["detail"] == "Unable to read the Accounts file. Upload a valid .xls, .xlsx or .json file and try again."

@pytest.mark.parametrize("filename,mime,payload_kind,expected_status", [
    ("Account Source", "application/json", "json", 200),
    ("Account Source", "application/octet-stream", "json", 200),
    ("Account Source", "", "json", 200),
    ("sample.json", "application/json", "json", 200),
    ("Account Source", "application/json", "malformed_json", 400),
    ("sample.json", "application/json", "malformed_json", 400),
    ("fake.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf", 400),
    ("corrupted.xlsx", "application/octet-stream", "corrupt_zip", 400),
    ("Account Source", "application/octet-stream", "binary", 400),
    ("Account Source", "application/zip", "ordinary_zip", 400),
    ("Account Source", "application/zip", "fake_workbook_zip", 400),
])
def test_extensionless_content_detection(
    filename, mime, payload_kind, expected_status, dummy_gstr_excel, dummy_json, monkeypatch, tmp_path,
):
    import zipfile
    from backend.app.services import session_manager
    from backend.app.services.session_store import InMemorySessionStore
    store = InMemorySessionStore()
    monkeypatch.setattr(session_manager, "_MANAGER", session_manager.SessionManager(store, base_temp_dir=tmp_path))
    payloads = {"json": dummy_json, "malformed_json": b'{"broken":', "pdf": b'%PDF-1.7 fake',
                "corrupt_zip": b'PK\x03\x04truncated', "binary": b'\x00\xff\x01\xfe'}
    if payload_kind.endswith("_zip") and payload_kind != "corrupt_zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "not an Excel workbook")
            if payload_kind == "fake_workbook_zip":
                archive.writestr("[Content_Types].xml", "not XML")
                archive.writestr("xl/workbook.xml", "not XML")
        payload = buffer.getvalue()
    else:
        payload = payloads[payload_kind]
    response = client.post("/api/inspect", files={
        "accounts_file": (filename, payload, mime),
        "gstr_file": ("GSTR Source", dummy_gstr_excel, "application/octet-stream"),
    })
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["files"]["accounts"]["original_filename"] == filename
        assert response.json()["files"]["gstr"]["row_count"] == 1
        assert len(response.json()["preview"]["gstr"]) == 1
    else:
        assert "valid" in response.json()["detail"]
        assert list(store.ids()) == []
        assert list(tmp_path.iterdir()) == []


def test_inspection_valid(dummy_excel, dummy_gstr_excel):
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xlsx", dummy_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR.xlsx", dummy_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert "preview" in data
    assert data["files"]["accounts"]["original_filename"] == "DayBook.xlsx"
    assert data["files"]["gstr"]["original_filename"] == "GSTR.xlsx"
    # Verify no filesystem paths are exposed
    assert "\\" not in data["files"]["accounts"]["original_filename"]
    
def test_inspection_json(dummy_json):
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.json", dummy_json, "application/json"),
            "gstr_file": ("GSTR.json", dummy_json, "application/json")
        }
    )
    assert response.status_code == 200

def test_inspection_unsupported_file():
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.txt", b"hello", "text/plain"),
            "gstr_file": ("GSTR.txt", b"world", "text/plain")
        }
    )
    assert response.status_code == 415

def test_inspection_malformed_xls_returns_controlled_error(dummy_gstr_excel):
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xls", b"legacy workbook", "application/vnd.ms-excel"),
            "gstr_file": ("GSTR.xlsx", dummy_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unable to read the Accounts file. "
        "Upload a valid .xls, .xlsx or .json file and try again."
    )

def test_inspection_malformed_xlsx_returns_controlled_error(dummy_gstr_excel):
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xlsx", b"not an xlsx workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR.xlsx", dummy_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unable to read the Accounts file. "
        "Upload a valid .xls, .xlsx or .json file and try again."
    )

def test_inspection_empty_file():
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xlsx", b"", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR.xlsx", b"", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]

def test_reconciliation_workflow(dummy_excel, dummy_gstr_excel):
    res_inspect = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xlsx", dummy_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR.xlsx", dummy_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    session_id = res_inspect.json()["session_id"]
    
    res_recon = client.post(f"/api/reconcile?session_id={session_id}", json={})
    assert res_recon.status_code == 200
    recon_data = res_recon.json()
    assert recon_data["counts"]["perfectly_matched"] == 1
    
    # Export JSON
    res_json = client.get(f"/api/export/{session_id}/json")
    assert res_json.status_code == 200
    assert "perfectly_matched" in res_json.json()["counts"]
    
    client.delete(f"/api/session/{session_id}")

def test_manual_match_workflow(ambiguous_excel, ambiguous_gstr_excel):
    res_inspect = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("DayBook.xlsx", ambiguous_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("GSTR.xlsx", ambiguous_gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    session_id = res_inspect.json()["session_id"]
    
    res_recon = client.post(f"/api/reconcile?session_id={session_id}", json={})
    recon_data = res_recon.json()
    
    assert recon_data["counts"]["needs_review_ambiguous"] == 1
    
    page = client.get(f"/api/session/{session_id}/results?category=needs_review").json()
    result = page["items"][0]
    assert len(result["candidates"]) == 2
    
    result_id = result["result_id"]
    candidate_id = result["candidates"][0]["candidate_id"]
    
    # Valid manual match
    res_mm = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": result_id, "candidate_id": candidate_id})
    assert res_mm.status_code == 200
    updated = res_mm.json()
    assert updated["counts"]["perfectly_matched"] == 1
    assert updated["counts"]["needs_review_ambiguous"] == 0
    assert updated["counts"]["not_in_tally"] == 1
    perfect_page = client.get(f"/api/session/{session_id}/results?category=perfect").json()
    assert any(r["match_method"] == "Manual Match" for r in perfect_page["items"])
    
    # Check that candidate cannot be consumed again
    res_mm2 = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": result_id, "candidate_id": result["candidates"][1]["candidate_id"]})
    assert res_mm2.status_code == 400
    
    client.delete(f"/api/session/{session_id}")

def test_missing_session_reconciliation():
    res_recon = client.post(f"/api/reconcile?session_id=missing-id-123", json={})
    assert res_recon.status_code == 404


@pytest.mark.parametrize("size", [100, 3_499_999, 3_500_000, 3_500_001])
def test_json_transport_boundary_keeps_reconciliation_and_excel_available(
    size, dummy_excel, dummy_gstr_excel, monkeypatch, tmp_path,
):
    from backend.app.services import reconciliation_orchestrator, session_manager
    from backend.app.services.session_store import InMemorySessionStore
    manager = session_manager.SessionManager(InMemorySessionStore(), tmp_path)
    monkeypatch.setattr(session_manager, "_MANAGER", manager)
    payload = '{"transport_test":true}'
    payload += ' ' * (size - len(payload.encode('utf-8')))
    monkeypatch.setattr(reconciliation_orchestrator, "generate_json_report", lambda *args: payload)
    inspected = client.post('/api/inspect', files={
        'accounts_file': ('a.xlsx', dummy_excel), 'gstr_file': ('g.xlsx', dummy_gstr_excel),
    })
    assert inspected.status_code == 200
    sid = inspected.json()['session_id']
    reconciled = client.post('/api/reconcile', params={'session_id': sid}, json={})
    assert reconciled.status_code == 200
    assert reconciled.json()['counts']['perfectly_matched'] == 1
    response = client.get(f'/api/export/{sid}/json')
    if size <= 3_500_000:
        assert response.status_code == 200
        assert response.content == payload.encode('utf-8')
    else:
        assert response.status_code == 413
        assert response.json() == {'detail': 'JSON export is too large for the current pilot deployment. Download the Excel report instead.'}
        assert len(response.content) < 500
    assert client.get(f'/api/session/{sid}').status_code == 200
    assert client.get(f'/api/session/{sid}/results').json()['total'] == 1
    excel = client.get(f'/api/export/{sid}/excel')
    assert excel.status_code == 200 and excel.content.startswith(b'PK')
    assert manager.get(sid)['reconciliation_summary'].perfectly_matched == 1
