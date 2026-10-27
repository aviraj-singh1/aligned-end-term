from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.records import SheetCategory
from backend.app.services.file_parser import parse_file


client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures" / "xls"
XLS_MIME = "application/vnd.ms-excel"
XLS_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@pytest.mark.parametrize(
    "filename,category,invoice_number",
    [
        ("accounts_purchase.xls", SheetCategory.PURCHASE_REGISTER, "60882"),
        ("gstr_2b.xls", SheetCategory.B2B, "60882"),
        ("debit_notes.xls", SheetCategory.DEBIT_NOTE_REGISTER, "DN001"),
    ],
)
def test_synthetic_xls_sources_normalize_to_canonical_records(filename, category, invoice_number):
    path = FIXTURES / filename
    assert path.read_bytes().startswith(XLS_SIGNATURE)

    inspected = parse_file(str(path))

    assert inspected.errors == []
    assert inspected.file_type == "xls"
    assert inspected.sheets[0].category == category
    assert len(inspected.persisted_sheets) == 1
    assert inspected.records[0].invoice_number.original == invoice_number
    assert inspected.records[0].invoice_date.isoformat() in {"2025-04-01", "2025-04-02"}
    if invoice_number == "60882":
        assert inspected.persisted_sheets[0].rows[0][0] == 60882


def test_synthetic_xls_complete_upload_to_export_flow(monkeypatch, tmp_path):
    """Exercise inspect/review/reconcile/results/export with all three .xls inputs."""
    from backend.app.services import session_manager
    from backend.app.services.session_store import InMemorySessionStore

    manager = session_manager.SessionManager(InMemorySessionStore(), base_temp_dir=tmp_path)
    monkeypatch.setattr(session_manager, "_MANAGER", manager)

    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("accounts_purchase.xls", (FIXTURES / "accounts_purchase.xls").read_bytes(), XLS_MIME),
            "gstr_file": ("gstr_2b.xls", (FIXTURES / "gstr_2b.xls").read_bytes(), XLS_MIME),
            "debit_notes_file": ("debit_notes.xls", (FIXTURES / "debit_notes.xls").read_bytes(), XLS_MIME),
        },
    )
    assert response.status_code == 200
    inspection = response.json()
    session_id = inspection["session_id"]

    # Inspect and Review Data use the same canonical mappings/previews as .xlsx.
    assert inspection["files"]["accounts"]["row_count"] == 1
    assert inspection["files"]["gstr"]["row_count"] == 1
    assert inspection["mappings"]["accounts"]["invoice_number"] == "Voucher No."
    assert inspection["mappings"]["gstr"]["invoice_number"] == "Invoice No."
    assert inspection["preview"]["accounts"][0]["invoice_number"] == "60882"
    assert inspection["preview"]["gstr"][0]["invoice_number"] == "60882"
    assert inspection["preview"]["debit_notes"][0]["invoice_number"] == "DN001"

    reconciled = client.post(
        "/api/reconcile",
        params={"session_id": session_id},
        json={"mapping_overrides": {}},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["counts"]["perfectly_matched"] == 1
    assert reconciled.json()["counts"]["total_notes"] == 1

    results = client.get(f"/api/session/{session_id}/results")
    assert results.status_code == 200
    assert results.json()["total"] == 1

    json_export = client.get(f"/api/export/{session_id}/json")
    assert json_export.status_code == 200
    assert json_export.json()["counts"]["perfectly_matched"] == 1

    excel_export = client.get(f"/api/export/{session_id}/excel")
    assert excel_export.status_code == 200
    assert excel_export.content.startswith(b"PK")


def test_extensionless_xls_is_detected_from_mime_and_signature(monkeypatch, tmp_path):
    from backend.app.services import session_manager
    from backend.app.services.session_store import InMemorySessionStore

    monkeypatch.setattr(
        session_manager,
        "_MANAGER",
        session_manager.SessionManager(InMemorySessionStore(), base_temp_dir=tmp_path),
    )
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("Accounts Purchase Data", (FIXTURES / "accounts_purchase.xls").read_bytes(), XLS_MIME),
            "gstr_file": ("GSTR-2B", (FIXTURES / "gstr_2b.xls").read_bytes(), XLS_MIME),
        },
    )
    assert response.status_code == 200
    assert response.json()["files"]["accounts"]["original_filename"] == "Accounts Purchase Data"
    assert response.json()["files"]["gstr"]["original_filename"] == "GSTR-2B"
