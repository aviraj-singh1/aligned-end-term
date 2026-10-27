import asyncio
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.main import app, configured_cors_origins
from backend.app.auth.middleware import is_financial_request
from backend.app.models.reconciliation import (
    ReconciliationResult, ReconciliationStatus, ReconciliationSummary,
)
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary
from backend.app.services.session_manager import (
    SessionManager, create_session, delete_session, get_session, update_session,
)
from backend.app.services.session_store import InMemorySessionStore


client = TestClient(app)


def test_notes_excel_export_is_classified_as_an_authorized_financial_route():
    assert is_financial_request("GET", "/api/export/run-id/notes/excel")
    assert is_financial_request("GET", "/api/export/run-id/excel")
    assert is_financial_request("GET", "/api/export/run-id/json")


def _xlsx_bytes(sheet="Purchase Register"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(["Voucher No.", "Date", "Particulars", "GSTIN/UIN", "Value"])
    worksheet.append(["INV-1", "2026-04-01", "Supplier", "06DEMOX1234A1Z5", 100])
    payload = io.BytesIO()
    workbook.save(payload)
    return payload.getvalue()


def test_oversized_upload_is_bounded_and_partial_file_is_removed(tmp_path, monkeypatch):
    from backend.app.api.routes import inspection

    monkeypatch.setattr(inspection, "MAX_COMBINED_UPLOAD_BYTES", 5)
    monkeypatch.setattr(inspection, "UPLOAD_CHUNK_SIZE", 4)
    upload = UploadFile(filename="large.json", file=io.BytesIO(b"123456789"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(inspection._save_file(upload, str(tmp_path), "accounts"))

    assert exc.value.status_code == 413
    assert not (tmp_path / "accounts.json").exists()
    assert upload.file.tell() == 8


def test_uploaded_filename_is_metadata_only_and_cannot_traverse(tmp_path):
    from backend.app.api.routes.inspection import _display_filename

    upload = UploadFile(filename="../../private/client.xlsx", file=io.BytesIO(b"x"))
    assert _display_filename(upload) == "client.xlsx"

    manager = SessionManager(InMemorySessionStore(), tmp_path / "sessions")
    victim = tmp_path / "victim"
    victim.mkdir()
    manager.delete("../victim")
    assert victim.is_dir()


@pytest.mark.parametrize("total", [24_999_999, 25_000_000, 25_000_001])
def test_combined_upload_budget_counts_all_files(tmp_path, total):
    from backend.app.api.routes.inspection import _save_file
    used = [0]
    async def save_all():
        extensions = ("xls", "xlsx", "json")
        for i, size in enumerate((10_000_000, 10_000_000, total - 20_000_000)):
            await _save_file(UploadFile(filename=f"{i}.{extensions[i]}", file=io.BytesIO(b"x" * size)),
                             str(tmp_path), str(i), used)
    if total > 25_000_000:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(save_all())
        assert exc.value.status_code == 413
        assert exc.value.detail == "Combined upload exceeds the 25 MB combined upload limit."
        assert not (tmp_path / "2.json").exists()
    else:
        asyncio.run(save_all())
        assert sum(p.stat().st_size for p in tmp_path.iterdir()) == total


def test_combined_oversize_endpoint_rejects_before_parsing_and_cleans_session(monkeypatch, tmp_path):
    from backend.app.services import session_manager
    from backend.app.api.routes import inspection
    manager = SessionManager(InMemorySessionStore(), tmp_path)
    monkeypatch.setattr(session_manager, "_MANAGER", manager)
    monkeypatch.setattr(inspection, "inspect_session_files", lambda _: pytest.fail("Oversized files reached parser"))
    response = client.post('/api/inspect', files={
        'accounts_file': ('a.xls', b'x' * 12_500_000),
        'gstr_file': ('g.xlsx', b'x' * 12_500_000),
        'debit_notes_file': ('n.json', b'x'),
    })
    assert response.status_code == 413
    assert response.json() == {'detail': 'Combined upload exceeds the 25 MB combined upload limit.'}
    assert manager.store.ids() == []
    assert list(tmp_path.iterdir()) == []


def test_expired_local_artifacts_are_cleaned_without_store_key_scan(tmp_path):
    class NoScanStore(InMemorySessionStore):
        def ids(self):
            return []

    root = tmp_path / "sessions"
    manager = SessionManager(NoScanStore(), root, timeout_minutes=60)
    stale = root / "b27cc4c0-5ee8-4d15-bb89-2fa870d73d22"
    stale.mkdir(parents=True)
    old = (datetime.now() - timedelta(hours=2)).timestamp()
    os.utime(stale, (old, old))

    manager.cleanup_expired()

    assert not stale.exists()


@pytest.mark.parametrize(
    "payload",
    [b"not-json", json.dumps(["not-an-object"]).encode(), json.dumps({"meta": 1}).encode()],
)
def test_malformed_or_unexpected_json_is_controlled(payload, monkeypatch):
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("accounts.json", payload, "application/json"),
            "gstr_file": ("gstr.xlsx", _xlsx_bytes("B2B"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unable to read the Accounts file. Upload a valid .xls, .xlsx or .json file and try again."
    )
    assert "Traceback" not in response.text


def test_unexpected_inspection_error_is_sanitized_and_session_is_removed(monkeypatch):
    from backend.app.api.routes import inspection

    created = []
    real_create = inspection.create_session

    def tracked_create():
        session_id = real_create()
        created.append(session_id)
        return session_id

    monkeypatch.setattr(inspection, "create_session", tracked_create)
    monkeypatch.setattr(
        inspection,
        "inspect_session_files",
        lambda _: (_ for _ in ()).throw(RuntimeError("C:\\private\\client.xlsx secret")),
    )
    response = client.post(
        "/api/inspect",
        files={
            "accounts_file": ("accounts.xlsx", b"valid-enough-for-save", "application/octet-stream"),
            "gstr_file": ("gstr.xlsx", b"valid-enough-for-save", "application/octet-stream"),
        },
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to inspect files"}
    assert "private" not in response.text
    assert created
    assert inspection.get_session(created[0]) is None


def test_sensitive_api_headers_and_safe_cors(monkeypatch):
    response = client.get("/api/health")
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        configured_cors_origins()
    monkeypatch.setenv("CORS_ORIGINS", "https://pilot.example.com")
    assert configured_cors_origins() == ["https://pilot.example.com"]
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="unsafe origin"):
        configured_cors_origins()


def test_export_failure_removes_partial_files(tmp_path, monkeypatch):
    from backend.app.services import reconciliation_orchestrator

    def fail_after_write(path, *_):
        Path(path).write_bytes(b"partial")
        raise RuntimeError("export failed")

    monkeypatch.setattr(reconciliation_orchestrator, "export_purchase_to_excel", fail_after_write)
    session = {"directory": tmp_path, "reports": {}}
    with pytest.raises(RuntimeError, match="export failed"):
        reconciliation_orchestrator._write_exports(
            session, ReconciliationSummary(), NotesReconciliationSummary()
        )
    assert list(tmp_path.iterdir()) == []


def test_deleted_or_invalid_session_cannot_export():
    response = client.get("/api/export/not-a-valid-session/excel")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"


def test_export_download_uses_server_filename_and_private_cache_policy():
    session_id = create_session()
    try:
        session = get_session(session_id)
        report = session["directory"] / "GSTR2B_Reconciliation.xlsx"
        report.write_bytes(_xlsx_bytes())
        update_session(session_id, "reconciliation_summary", ReconciliationSummary())
        update_session(session_id, "notes_summary", NotesReconciliationSummary())
        update_session(session_id, "reports", {"excel": str(report)})

        response = client.get(f"/api/export/{session_id}/excel")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert "GSTR2B_Reconciliation.xlsx" in disposition
        assert ".." not in disposition
    finally:
        delete_session(session_id)


def test_notes_export_endpoint_is_separate_and_requires_notes_results():
    session_id = create_session()
    try:
        session = get_session(session_id)
        notes_report = session["directory"] / "GSTR2B_Notes_Reconciliation.xlsx"
        notes_report.write_bytes(_xlsx_bytes("Summary"))
        update_session(session_id, "reconciliation_summary", ReconciliationSummary())
        update_session(session_id, "notes_summary", NotesReconciliationSummary())
        empty = client.get(f"/api/export/{session_id}/notes/excel")
        assert empty.status_code == 409

        notes = NotesReconciliationSummary(results=[
            ReconciliationResult(status=ReconciliationStatus.PERFECTLY_MATCHED)
        ])
        update_session(session_id, "notes_summary", notes)
        update_session(session_id, "reports", {"notes_excel": str(notes_report)})
        response = client.get(f"/api/export/{session_id}/notes/excel")
        assert response.status_code == 200 and response.content.startswith(b"PK")
        assert "GSTR2B_Notes_Reconciliation.xlsx" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store, private"
    finally:
        delete_session(session_id)
