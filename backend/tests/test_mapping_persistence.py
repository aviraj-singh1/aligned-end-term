from io import BytesIO
from datetime import date
from decimal import Decimal
import json
import shutil

import pytest

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.main import app
from backend.app.services import session_manager
from backend.app.services.session_manager import SessionManager
from backend.app.services.session_store import JsonFileSessionStore


def recreate_without_uploads(tmp_path, monkeypatch, manager_factory, session_id):
    """Remove runtime A entirely; B gets only the serialized SessionStore state."""
    previous = session_manager._MANAGER
    directory = previous.base_temp_dir.resolve()
    assert directory.is_relative_to(tmp_path.resolve())
    shutil.rmtree(directory)
    monkeypatch.setattr(session_manager, "_MANAGER", manager_factory("runtime-b"))
    assert not directory.exists()
    loaded = session_manager.get_session(session_id)
    assert loaded["files"] == {}


@pytest.fixture(params=["json-store", "upstash"])
def manager_factory(tmp_path, request):
    if request.param == "upstash":
        from backend.tests.test_upstash_session_store import FakeUpstashRedis, redis_store
        redis = FakeUpstashRedis()
        return lambda runtime: SessionManager(redis_store(redis), tmp_path / runtime)
    return lambda runtime: SessionManager(JsonFileSessionStore(tmp_path / "store"), tmp_path / runtime)


def forbid_source_parsing(monkeypatch):
    from backend.app.services import file_parser, reconciliation_orchestrator

    def forbidden(*args, **kwargs):
        pytest.fail("Reconcile attempted to reopen an original upload")

    for name in ("parse_file", "parse_excel_file", "parse_json_file"):
        monkeypatch.setattr(file_parser, name, forbidden)
    monkeypatch.setattr(reconciliation_orchestrator, "parse_file", forbidden)


def workbook_bytes(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def managers(tmp_path):
    root = tmp_path / "store"
    return (
        SessionManager(JsonFileSessionStore(root), tmp_path / "runtime-a"),
        SessionManager(JsonFileSessionStore(root), tmp_path / "runtime-b"),
    )


def inspect(client, accounts, gstr, debit_notes=None):
    files = {
        "accounts_file": ("accounts.xlsx", accounts, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "gstr_file": ("gstr.xlsx", gstr, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }
    if debit_notes is not None:
        files["debit_notes_file"] = ("debit.xlsx", debit_notes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response = client.post("/api/inspect", files=files)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def mapping_workbooks():
    accounts = workbook_bytes({
        "Purchase Register": [
            ["Date", "Particulars", "Voucher Type", "Voucher No.", "GSTIN/UIN", "Default Taxable", "Chosen Taxable", "I-gst", "C-gst", "S-gst"],
            ["01-04-2025", "ALPHA TRADERS", "Purchase", "INV-1", "06AAAAA0000A1Z5", 999, 1000, 0, 90, 90],
        ]
    })
    gstr = workbook_bytes({
        "b2b": [
            ["GSTIN/UIN", "Party Name", "Invoice No.", "Invoice Date", "Taxable Value", "Integrated Tax", "Central Tax", "State/UT Tax"],
            ["06AAAAA0000A1Z5", "ALPHA TRADERS", "INV-1", "01-04-2025", 1000, 0, 90, 90],
        ]
    })
    mappings = {
        "accounts": {
            "gstin": None,
            "taxable_value": "Chosen Taxable",
            "igst": "I-gst",
            "cgst": "C-gst",
            "sgst": "S-gst",
        },
        "gstr": {
            "taxable_value": "Taxable Value",
            "igst": "Integrated Tax",
            "cgst": "Central Tax",
            "sgst": "State/UT Tax",
        },
    }
    return accounts, gstr, mappings


def test_cross_manager_mapping_overrides_without_original_files(tmp_path, monkeypatch):
    first, second = managers(tmp_path)
    monkeypatch.setattr(session_manager, "_MANAGER", first)
    client = TestClient(app)
    accounts, gstr, mappings = mapping_workbooks()
    session_id = inspect(client, accounts, gstr)

    inspected_session = first.get(session_id)
    assert inspected_session["files"] == {}
    assert inspected_session["parsed_tally"].records == []
    assert inspected_session["parsed_tally"].persisted_sheets
    assert not (tmp_path / "runtime-a" / session_id / "accounts.xlsx").exists()
    assert not (tmp_path / "runtime-a" / session_id / "gstr.xlsx").exists()

    monkeypatch.setattr(session_manager, "_MANAGER", second)
    response = client.post(f"/api/reconcile?session_id={session_id}", json={"mapping_overrides": mappings})
    assert response.status_code == 200, response.text
    loaded = second.get(session_id)["reconciliation_summary"]
    result = loaded.results[0]
    assert result.audit.tally_gstin == ""
    assert result.audit.supplier_match == "name_fallback"
    assert result.audit.tally_taxable == result.audit.gst_taxable == 1000
    assert result.cgst_diff == result.sgst_diff == result.igst_diff == 0
    completed_session = second.get(session_id)
    assert completed_session["parsed_tally"] is None
    assert completed_session["inspection_results"] is None
    assert completed_session["parsed_gstr"] is None


def test_cross_manager_result_equals_equivalent_same_instance_flow(tmp_path, monkeypatch):
    first, second = managers(tmp_path)
    monkeypatch.setattr(session_manager, "_MANAGER", first)
    client = TestClient(app)
    accounts, gstr, mappings = mapping_workbooks()
    cross_id = inspect(client, accounts, gstr)
    monkeypatch.setattr(session_manager, "_MANAGER", second)
    cross = client.post(f"/api/reconcile?session_id={cross_id}", json={"mapping_overrides": mappings})

    monkeypatch.setattr(session_manager, "_MANAGER", first)
    same_id = inspect(client, accounts, gstr)
    same = client.post(f"/api/reconcile?session_id={same_id}", json={"mapping_overrides": mappings})
    assert cross.status_code == same.status_code == 200
    assert cross.json()["counts"] == same.json()["counts"]
    monkeypatch.setattr(session_manager, "_MANAGER", second)
    cross_result = client.get(f"/api/session/{cross_id}/results").json()["items"][0]
    monkeypatch.setattr(session_manager, "_MANAGER", first)
    same_result = client.get(f"/api/session/{same_id}/results").json()["items"][0]
    comparable = ("status", "match_method", "taxable_value", "igst_diff", "cgst_diff", "sgst_diff")
    assert {key: cross_result[key] for key in comparable} == {
        key: same_result[key] for key in comparable
    }


def test_optional_debit_notes_survive_recreation_and_cleanup(tmp_path, monkeypatch):
    first, second = managers(tmp_path)
    monkeypatch.setattr(session_manager, "_MANAGER", first)
    client = TestClient(app)
    accounts, gstr, _ = mapping_workbooks()
    debit = workbook_bytes({
        "Debit Note Register": [
            ["Date", "Particulars", "Voucher Type", "Voucher No.", "GSTIN/UIN", "Taxable Value", "CGST", "SGST"],
            ["02-04-2025", "ALPHA TRADERS", "Debit Note", "DN-1", "06AAAAA0000A1Z5", 100, 9, 9],
        ]
    })
    session_id = inspect(client, accounts, gstr, debit)
    assert first.get(session_id)["files"] == {}
    assert not (tmp_path / "runtime-a" / session_id / "debit_notes.xlsx").exists()

    monkeypatch.setattr(session_manager, "_MANAGER", second)
    response = client.post(
        f"/api/reconcile?session_id={session_id}",
        json={"mapping_overrides": {"debit_notes": {"taxable_value": "Taxable Value"}}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["total_notes"] >= 1


def test_json_mapping_override_uses_persisted_rows(tmp_path):
    import json
    from backend.app.models.records import RecordSource
    from backend.app.services.file_parser import parse_file, reparse_persisted_file

    path = tmp_path / "records.json"
    path.write_text(json.dumps({"b2b": [{
        "GSTIN/UIN": "06AAAAA0000A1Z5", "Party Name": "ALPHA",
        "Invoice No.": "INV-1", "Invoice Date": "01-04-2025",
        "Default Taxable": 1, "Chosen Taxable": 1000,
    }]}), encoding="utf-8")
    inspected = parse_file(str(path), RecordSource.GST)
    path.unlink()
    rebuilt = reparse_persisted_file(
        inspected, RecordSource.GST, "b2b",
        {"taxable_value": "Chosen Taxable", "gstin": "-- Not mapped --"},
    )
    assert rebuilt.records[0].taxable_value == 1000
    assert rebuilt.records[0].gstin_normalized == ""


@pytest.mark.parametrize("file_type", ["xlsx", "json"])
@pytest.mark.parametrize("unmapped", [False, None, "", "-- Not mapped --"])
def test_all_frontend_mapping_overrides_after_runtime_loss(
    tmp_path, monkeypatch, manager_factory, file_type, unmapped,
):
    labels = ["Supplier", "GSTIN", "Invoice Number", "Invoice Date", "Taxable Value", "IGST", "CGST", "SGST"]
    semantics = ["supplier_name", "gstin", "invoice_number", "invoice_date", "taxable_value", "igst", "cgst", "sgst"]
    defaults = ["Particulars", "GSTIN/UIN", "Voucher No.", "Date", "Taxable Value", "IGST", "CGST", "SGST"]
    chosen = [f"Selected {index}" for index in range(len(labels))]
    row = ["WRONG", "07BBBBB0000B1Z5", "OLD", "01-04-2025", 1, 2, 3, 4,
           "ALPHA TRADERS", "06AAAAA0000A1Z5", "CHOSEN-77", "02-05-2025", 1000, 18, 90, 90]
    files = {}
    overrides = {}
    for side, category in (("accounts", "Purchase Register"), ("gstr", "b2b")):
        rows = {category: [defaults + chosen, row]}
        content = workbook_bytes(rows) if file_type == "xlsx" else json.dumps({category: [dict(zip(defaults + chosen, row))]}).encode()
        files[f"{side}_file"] = (f"{side}.{file_type}", content)
        # This mirrors App's original detected semantics PLUS edited display labels.
        overrides[side] = dict(zip(semantics, defaults)) | dict(zip(labels, chosen))
        if unmapped is not False:
            overrides[side]["GSTIN"] = unmapped

    monkeypatch.setattr(session_manager, "_MANAGER", manager_factory("runtime-a"))
    client = TestClient(app)
    inspected = client.post("/api/inspect", files=files)
    assert inspected.status_code == 200, inspected.text
    session_id = inspected.json()["session_id"]
    recreate_without_uploads(tmp_path, monkeypatch, manager_factory, session_id)
    forbid_source_parsing(monkeypatch)
    response = client.post(f"/api/reconcile?session_id={session_id}", json={"mapping_overrides": overrides})
    assert response.status_code == 200, response.text
    summary = session_manager.get_session(session_id)["reconciliation_summary"]
    assert len(summary.results) == 1
    for record in summary.results[0].audit.tally_rows + summary.results[0].audit.gst_rows:
        assert record.supplier_name == "ALPHA TRADERS"
        assert record.gstin_normalized == ("06AAAAA0000A1Z5" if unmapped is False else "")
        assert record.invoice_number.original == "CHOSEN-77"
        assert record.invoice_date == date(2025, 5, 2)
        assert (record.taxable_value, record.igst, record.cgst, record.sgst) == tuple(map(Decimal, [1000, 18, 90, 90]))
    assert summary.results[0].audit.tally_rows
    assert summary.results[0].audit.gst_rows


@pytest.mark.parametrize("file_type", ["xlsx", "json"])
def test_amendments_categories_and_optional_notes_after_runtime_loss(
    tmp_path, monkeypatch, manager_factory, file_type,
):
    gstin = "06AAAAA0000A1Z5"
    headers = ["GSTIN/UIN", "Party Name", "Invoice No.", "Invoice Date", "Taxable Value", "CGST", "SGST"]
    gstr = {
        "b2b": [headers, [gstin, "ALPHA", "OLD", "01-04-2025", 100, 9, 9]],
        "b2ba": [["GSTIN/UIN", "Party Name", "Original Invoice No.", "Original Invoice Date", "Revised Invoice No.", "Revised Invoice Date", "Taxable Value", "CGST", "SGST", "Invoice No.", "Invoice Date"],
                 [gstin, "ALPHA", "OLD", "01-04-2025", "NEW", "02-04-2025", 120, 10.8, 10.8, "NEW", "02-04-2025"]],
        "cdnr": [["GSTIN/UIN", "Party Name", "Note Number", "Note Date", "Note Type", "Taxable Value", "CGST", "SGST", "Invoice Date"],
                 [gstin, "ALPHA", "CN-OLD", "01-04-2025", "C", 50, 4.5, 4.5, "01-04-2025"]],
        "cdnra": [["GSTIN/UIN", "Party Name", "Original Note No", "Original Note Date", "Revised Note Number", "Revised Note Date", "Note Type", "Taxable Value", "CGST", "SGST", "Note Number", "Invoice Date"],
                  [gstin, "ALPHA", "CN-OLD", "01-04-2025", "CN-NEW", "02-04-2025", "C", 60, 5.4, 5.4, "CN-NEW", "02-04-2025"]],
        "party": [headers, [gstin, "EXCLUDE", "OTHER", "02-04-2025", 9999, 900, 900]],
    }
    accounts = {"Purchase Register": [headers, [gstin, "ALPHA", "NEW", "02-04-2025", 120, 10.8, 10.8]]}
    debit = {"Debit Note Register": [headers + ["Chosen Taxable"], [gstin, "ALPHA", "CN-NEW", "02-04-2025", 999, 5.4, 5.4, 60]]}
    files = {}
    for side, sheets in (("accounts", accounts), ("gstr", gstr), ("debit_notes", debit)):
        content = workbook_bytes(sheets) if file_type == "xlsx" else json.dumps({name: [dict(zip(rows[0], row)) for row in rows[1:]] for name, rows in sheets.items()}).encode()
        files[f"{side}_file"] = (f"{side}.{file_type}", content)
    monkeypatch.setattr(session_manager, "_MANAGER", manager_factory("runtime-a"))
    client = TestClient(app)
    response = client.post("/api/inspect", files=files)
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    recreate_without_uploads(tmp_path, monkeypatch, manager_factory, session_id)
    forbid_source_parsing(monkeypatch)
    response = client.post(f"/api/reconcile?session_id={session_id}", json={"mapping_overrides": {
        "gstr": {"Taxable Value": "Taxable Value"}, "debit_notes": {"Taxable Value": "Chosen Taxable"},
    }})
    assert response.status_code == 200, response.text
    session = session_manager.get_session(session_id)
    purchases, notes = session["reconciliation_summary"], session["notes_summary"]
    assert purchases.total_gst_invoices == purchases.perfectly_matched == 1
    assert notes.total_gst_credit_notes == 1
    assert len(notes.results) == 1
    assert notes.results[0].audit.tally_taxable == notes.results[0].audit.gst_taxable == 60
    assert purchases.amendment_stats["superseded_regular_documents"] == 1
    assert notes.amendment_stats["superseded_regular_documents"] == 1
    assert purchases.results[0].audit.gst_rows[0].source_sheet == "b2ba"
    assert notes.results[0].audit.gst_rows[0].source_sheet == "cdnra"


def assert_accounting_invariants(summary):
    tally_results = [result for result in summary.results if result.audit.tally_rows]
    consumed = [result for result in tally_results if result.audit.gst_rows]
    assert len(tally_results) == summary.active_tally_invoices
    assert len(tally_results) + summary.cancelled_accounts_vouchers == summary.total_tally_invoices
    assert len(consumed) + summary.not_in_tally == summary.active_gst_invoices
    assert summary.active_gst_invoices + summary.cancelled_gstr_vouchers == summary.total_gst_invoices


