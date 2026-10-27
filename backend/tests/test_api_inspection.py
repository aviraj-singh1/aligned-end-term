import pytest
import io
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


@pytest.mark.parametrize("accounts_count,gstr_count", [(146, 46), (993, 1084), (3, 2), (3, 0)])
def test_selected_gstr_preview_is_sample_limited(accounts_count, gstr_count, monkeypatch, tmp_path):
    from backend.app.services import session_manager
    from backend.app.services.session_store import InMemorySessionStore

    monkeypatch.setattr(session_manager, "_MANAGER", session_manager.SessionManager(
        InMemorySessionStore(), base_temp_dir=tmp_path,
    ))
    accounts_headers = ["Voucher No.", "Date", "Particulars", "GSTIN/UIN", "Value", "Igst", "CGST", "SGST"]
    gstr_headers = ["Invoice No.", "Invoice Date", "Party Name", "GSTIN/UIN", "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"]
    note_headers = ["Note No", "Note Date", *gstr_headers[2:], "Note Type"]

    def rows(count, prefix="DEMO"):
        return [[f"{prefix}-{i}", "2025-04-01", "Synthetic Supplier", "06ABCDE1234F1Z5", 1000, 0, 90, 90] for i in range(count)]

    response = client.post("/api/inspect", files={
        "accounts_file": ("demo-accounts.xlsx", create_multi_sheet_excel({
            "Purchase Register": [accounts_headers, *rows(accounts_count)],
        }), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "gstr_file": ("demo-gstr.xlsx", create_multi_sheet_excel({
            "Party": [["Party ID", "Name"], ["1", "Synthetic Supplier"]],
            "B2B": [gstr_headers, *rows(gstr_count)],
            "CDNR": [note_headers, *[row + ["Credit Note"] for row in rows(2, "NOTE")]],
        }), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    })
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"session_id", "files", "mappings", "available_columns", "preview", "warnings"}
    assert set(data["preview"]) == {"accounts", "gstr", "debit_notes", "credit_notes"}
    assert data["files"]["accounts"]["selected_sheet"] == "Purchase Register"
    assert data["files"]["gstr"]["selected_sheet"] == "B2B"
    assert data["files"]["accounts"]["row_count"] == accounts_count
    assert data["files"]["gstr"]["row_count"] == gstr_count
    assert data["available_columns"]["gstr"] == gstr_headers

    def expected_preview(count, prefix="DEMO"):
        return [{"supplier": "Synthetic Supplier", "gstin": "06ABCDE1234F1Z5",
                 "invoice_number": f"{prefix}-{i}", "date": "2025-04-01",
                 "taxable_value": "1000.00", "igst": "0.00", "cgst": "90.00", "sgst": "90.00"}
                for i in range(min(count, 10))]

    assert data["preview"]["accounts"] == expected_preview(accounts_count)
    assert data["preview"]["gstr"] == expected_preview(gstr_count)
    assert data["preview"]["credit_notes"] == expected_preview(2, "NOTE")
    assert data["preview"]["debit_notes"] == []

    # Inspection samples must not truncate or otherwise alter reconciliation inputs.
    reconciled = client.post(f"/api/reconcile?session_id={data['session_id']}", json={"mapping_overrides": {}})
    assert reconciled.status_code == 200
    counts = reconciled.json()["counts"]
    assert counts["accounts_invoices"] == accounts_count
    assert counts["gstr_invoices"] == gstr_count
    assert counts["perfectly_matched"] == min(accounts_count, gstr_count)
    assert counts["missing_in_gstr_2b"] == max(accounts_count - gstr_count, 0)
    assert counts["not_in_tally"] == max(gstr_count - accounts_count, 0)
    assert counts["major_difference"] == counts["minor_rounding_difference"] == 0

def create_multi_sheet_excel(sheets_data):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active) # remove default sheet
    for title, rows in sheets_data.items():
        ws = wb.create_sheet(title=title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_accounts_preview_excludes_summary_only_without_changing_persisted_rows(monkeypatch, tmp_path):
    from backend.app.models.records import RecordSource
    from backend.app.services import session_manager
    from backend.app.services.file_parser import reparse_persisted_file
    from backend.app.services.session_store import InMemorySessionStore

    monkeypatch.setattr(session_manager, "_MANAGER", session_manager.SessionManager(
        InMemorySessionStore(), base_temp_dir=tmp_path,
    ))
    headers = ["Voucher No.", "Date", "Particulars", "GSTIN/UIN", "Value", "Igst", "CGST", "SGST"]
    # Put the summary first so the test proves removal from the sample too.
    rows = [
        [None, None, "Grand Total", None, 2000, 0, 180, 180],
        ["A1", "2025-04-01", "Supplier", "06ABCDE1234F1Z5", 1000, 0, 90, 90],
        ["A2", None, "Optional fields absent", None, None, None, None, None],
        ["A3", "2025-04-01", "Cancelled Supplier", "06ABCDE1234F1Z5", 1000, 0, 90, 90],
    ]
    response = client.post("/api/inspect", files={
        "accounts_file": ("accounts.xlsx", create_multi_sheet_excel({"Purchase Register": [headers, *rows]})),
        "gstr_file": ("gst.xlsx", create_multi_sheet_excel({"B2B": [
            ["Invoice No.", "Invoice Date", "Party Name", "GSTIN/UIN", "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"],
            rows[1],
        ]})),
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["files"]["accounts"]["row_count"] == 3
    assert [r["invoice_number"] for r in data["preview"]["accounts"]] == ["A1", "A2", "A3"]
    assert all(r["supplier"] != "Grand Total" for r in data["preview"]["accounts"])
    session = session_manager.get_session(data["session_id"])
    reparsed = reparse_persisted_file(session["parsed_tally"], RecordSource.TALLY)
    assert len(reparsed.records) == 4
    assert reparsed.records[0].supplier_name == "Grand Total"
    reconciled = client.post(f"/api/reconcile?session_id={data['session_id']}", json={"mapping_overrides": {}})
    assert reconciled.status_code == 200, reconciled.text
    summary = session_manager.get_session(data["session_id"])["reconciliation_summary"]
    assert summary.total_tally_invoices == 3
    assert summary.active_tally_invoices == 2
    assert summary.cancelled_accounts_vouchers == 1

def test_available_columns_uses_detected_sheets():
    # GSTR file with an unrelated first sheet (Party) and second sheet (B2B)
    gstr_data = {
        "Party": [
            ["Party ID", "Name", "Address"],
            ["1", "Supplier A", "Mumbai"]
        ],
        "B2B": [
            ["Invoice No.", "Invoice Date", "Party Name", "GSTIN/UIN", "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"],
            ["INV001", "01-04-2025", "Test Supplier", "06DEMOX1234A1Z5", 1000, 0, 90, 90]
        ],
        "CDNR": [
            ["Note No", "Note Date", "Invoice No.", "Party Name", "GSTIN/UIN", "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"],
            ["CN001", "01-04-2025", "INV001", "Test Supplier", "06DEMOX1234A1Z5", 100, 0, 9, 9]
        ]
    }
    
    # Tally file with Summary first, then Purchase, then Debit Notes
    tally_data = {
        "Summary": [
            ["Voucher Type", "Count"],
            ["Purchase", 10]
        ],
        "Purchase Register": [
            ["Voucher No.", "Date", "Particulars", "GSTIN/UIN", "Value", "Igst", "CGST", "SGST"],
            ["INV001", "2025-04-01", "Test Supplier", "06DEMOX1234A1Z5", 1000, 0, 90, 90]
        ],
        "Debit Note Register": [
            ["Voucher No.", "Date", "Original Invoice", "Particulars", "GSTIN/UIN", "Value", "Igst", "CGST", "SGST"],
            ["DN001", "2025-04-01", "INV001", "Test Supplier", "06DEMOX1234A1Z5", 100, 0, 9, 9]
        ]
    }
    
    gstr_excel = create_multi_sheet_excel(gstr_data)
    tally_excel = create_multi_sheet_excel(tally_data)
    
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("tally.xlsx", tally_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("gstr.xlsx", gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    
    assert response.status_code == 200, response.text
    data = response.json()
    
    available = data["available_columns"]
    
    # Tally - should be Purchase Register (Sheet 2), not Summary
    assert "Voucher No." in available["accounts"]
    assert "Voucher Type" not in available["accounts"]
    
    # GSTR - should be B2B (Sheet 2), not Party
    assert "Invoice No." in available["gstr"]
    assert "Party ID" not in available["gstr"]
    
    # Credit notes - should be CDNR (Sheet 3)
    assert "Note No" in available["credit_notes"]
    assert "Invoice No." in available["credit_notes"]
    assert "Party ID" not in available["credit_notes"]
    
    # Debit notes - since we passed it implicitly via accounts file
    assert "Original Invoice" in available["debit_notes"]
    assert "Voucher Type" not in available["debit_notes"]


def test_synthetic_large_register_headers_are_exposed_as_available_columns():
    headers = [
        "Date", "Vch/Bill No", "Account", "TIN/GSTIN No.", "Type",
        "Total Amount", "Purc. Amount", "Taxable Amt.", "IGST", "CGST",
        "SGST", "Other Amount.", "Notes",
    ]
    tally_excel = create_multi_sheet_excel({
        "Sheet1": [
            ["Company"], ["Purchase Register"], ["FY 2025-26"], ["Report"],
            headers,
            ["01-04-2025", "QA-1", "Alpha Traders", "06ABCDE1234F1Z5",
             "Purchase", 11800, 10000, 10000, 0, 900, 900, 0, ""],
        ],
    })
    gstr_excel = create_multi_sheet_excel({
        "B2B": [
            ["Invoice No.", "Invoice Date", "Party Name", "GSTIN/UIN",
             "Taxable Value", "IGST Amount", "CGST Amount", "SGST Amount"],
            ["QA-1", "01-04-2025", "Alpha Traders", "06ABCDE1234F1Z5",
             10000, 0, 900, 900],
        ],
    })

    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("tally.xlsx", tally_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("gstr.xlsx", gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["files"]["accounts"]["selected_sheet"] == "Sheet1"
    assert data["files"]["accounts"]["row_count"] == 1
    assert data["available_columns"]["accounts"] == headers
    assert data["mappings"]["accounts"]["supplier_name"] == "Account"

def test_missing_required_semantic_sheets_returns_empty_and_warnings():
    # GSTR file with NO B2B sheet
    gstr_data = {
        "Party": [
            ["Party ID", "Name", "Address"],
            ["1", "Supplier A", "Mumbai"]
        ]
    }
    
    # Tally file with NO Purchase Register
    tally_data = {
        "Summary": [
            ["Voucher Type", "Count"],
            ["Purchase", 10]
        ]
    }
    
    gstr_excel = create_multi_sheet_excel(gstr_data)
    tally_excel = create_multi_sheet_excel(tally_data)
    
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("tally.xlsx", tally_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "gstr_file": ("gstr.xlsx", gstr_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["available_columns"]["accounts"] == []
    assert data["available_columns"]["gstr"] == []
    
    assert any("Unable to detect a Tally Purchase Register sheet" in w for w in data["warnings"])
    assert any("Unable to detect a GSTR B2B sheet" in w for w in data["warnings"])

    # Selected sheets should be null or similar based on our logic (we return None which becomes null in JSON)
    assert data["files"]["accounts"]["selected_sheet"] is None
    assert data["files"]["gstr"]["selected_sheet"] is None
    
    # No fatal error on missing optional sheets
    assert data["available_columns"]["debit_notes"] == []
    assert data["available_columns"]["credit_notes"] == []
