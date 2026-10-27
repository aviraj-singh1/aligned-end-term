from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.records import ParsedInvoiceRecord, RecordSource, SheetCategory
from backend.app.models.reconciliation import (
    AggregatedGSTInvoice, AuditTrail, MatchMethod, ReconciliationResult,
    ReconciliationStatus, ReconciliationSummary,
)
from backend.app.services import session_manager
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary
from backend.app.services.session_manager import SessionManager
from backend.app.services.session_store import (
    JsonFileSessionStore, SessionConfigurationError, configured_store,
)


def managers(tmp_path, timeout=60):
    store_path = tmp_path / "store"
    return (
        SessionManager(JsonFileSessionStore(store_path), tmp_path / "runtime-a", timeout),
        SessionManager(JsonFileSessionStore(store_path), tmp_path / "runtime-b", timeout),
    )


def durable_reconciliation(manager):
    session_id = manager.create()
    tally = ParsedInvoiceRecord(
        source=RecordSource.TALLY, sheet_category=SheetCategory.PURCHASE_REGISTER,
        supplier_name="Tally Supplier", gstin_normalized="06AAAAA0000A1Z5",
        invoice_number=normalize_invoice_number("TALLY-1"), invoice_date=date(2026, 3, 1),
        taxable_value=Decimal("10940.94"), cgst=Decimal("984.68"), sgst=Decimal("984.68"),
    )
    gst_row = ParsedInvoiceRecord(
        source=RecordSource.GST, sheet_category=SheetCategory.B2B,
        supplier_name="GST Supplier", gstin_normalized="06AAAAA0000A1Z5",
        invoice_number=normalize_invoice_number("GST-1"), invoice_date=date(2026, 2, 28),
        taxable_value=Decimal("10940.94"), cgst=Decimal("984.68"), sgst=Decimal("984.68"),
    )
    candidate = AggregatedGSTInvoice(
        gstin=gst_row.gstin_normalized, supplier_name=gst_row.supplier_name,
        invoice_number_original="GST-1", invoice_number_normalized="GST1",
        invoice_date=gst_row.invoice_date, taxable_value=gst_row.taxable_value,
        cgst=gst_row.cgst, sgst=gst_row.sgst, source_rows=[gst_row],
    )
    candidate.api_candidate_id = "candidate-1"
    review = ReconciliationResult(
        supplier_tally=tally.supplier_name, supplier_gstin=tally.gstin_normalized,
        invoice_number_tally="TALLY-1", invoice_date=tally.invoice_date,
        taxable_value=tally.taxable_value, status=ReconciliationStatus.NEEDS_REVIEW,
        match_method=MatchMethod.AMBIGUOUS,
        audit=AuditTrail(tally_rows=[tally], tally_taxable=tally.taxable_value, candidates=[candidate]),
    )
    review.api_result_id = "result-1"
    summary = ReconciliationSummary(
        total_tally_invoices=1, total_gst_invoices=1, results=[review],
        not_in_tally_records=[candidate],
    )
    summary.compute_counts()
    manager.update(session_id, "reconciliation_summary", summary)
    manager.update(session_id, "notes_summary", NotesReconciliationSummary())
    manager.update(session_id, "manual_match_map", {"result-1": {"candidate-1": candidate}})
    return session_id


def test_create_update_retrieve_delete_and_isolation(tmp_path):
    first, second = managers(tmp_path)
    session_a = first.create()
    session_b = first.create()
    first.update(session_a, "marker", "A")
    first.update(session_b, "marker", "B")
    assert second.get(session_a)["marker"] == "A"
    assert second.get(session_b)["marker"] == "B"
    second.delete(session_a)
    assert first.get(session_a) is None
    assert first.get(session_b)["marker"] == "B"


def test_expired_session_is_unavailable(tmp_path):
    first, second = managers(tmp_path, timeout=1)
    session_id = first.create()
    stored = first.store.get(session_id)
    stored["last_accessed"] = datetime.now() - timedelta(minutes=2)
    first.store.save(session_id, stored)
    assert second.get(session_id) is None


def test_json_round_trip_preserves_financial_and_domain_types(tmp_path):
    first, second = managers(tmp_path)
    session_id = durable_reconciliation(first)
    loaded = second.get(session_id)["reconciliation_summary"]
    row = loaded.results[0].audit.tally_rows[0]
    assert row.taxable_value == Decimal("10940.94")
    assert isinstance(row.taxable_value, Decimal)
    assert row.invoice_date == date(2026, 3, 1)
    assert row.source == RecordSource.TALLY
    assert row.sheet_category == SheetCategory.PURCHASE_REGISTER
    assert loaded.results[0].api_result_id == "result-1"


def test_reconciliation_and_candidate_state_survive_recreation(tmp_path):
    first, second = managers(tmp_path)
    session_id = durable_reconciliation(first)
    loaded = second.get(session_id)
    assert loaded["reconciliation_summary"].needs_review == 1
    assert loaded["manual_match_map"]["result-1"]["candidate-1"].consumed is False
    assert loaded["reconciliation_summary"].not_in_tally == 1


def test_manual_match_and_duplicate_rejection_survive_recreation(tmp_path, monkeypatch):
    first, second = managers(tmp_path)
    session_id = durable_reconciliation(first)
    monkeypatch.setattr(session_manager, "_MANAGER", second)
    client = TestClient(app)
    response = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": "result-1", "candidate_id": "candidate-1"})
    assert response.status_code == 200
    assert response.json()["counts"]["needs_review_ambiguous"] == 0
    assert response.json()["counts"]["not_in_tally"] == 0
    assert response.json()["counts"]["date_mismatches"] == 1
    reloaded = first.get(session_id)
    result = reloaded["reconciliation_summary"].results[0]
    assert result.match_method == MatchMethod.MANUAL_MATCH
    assert result.date_difference_days == 1
    assert reloaded["manual_match_map"]["result-1"]["candidate-1"].consumed is True
    duplicate = client.post(f"/api/manual-match?session_id={session_id}", json={"result_id": "result-1", "candidate_id": "candidate-1"})
    assert duplicate.status_code == 409


def test_exports_rebuild_after_runtime_recreation(tmp_path, monkeypatch):
    first, second = managers(tmp_path)
    session_id = durable_reconciliation(first)
    monkeypatch.setattr(session_manager, "_MANAGER", second)
    client = TestClient(app)
    excel = client.get(f"/api/export/{session_id}/excel")
    exported_json = client.get(f"/api/export/{session_id}/json")
    assert excel.status_code == 200
    assert excel.content.startswith(b"PK")
    assert exported_json.status_code == 200
    assert exported_json.json()["counts"]["needs_review_ambiguous"] == 1


def test_production_never_silently_uses_local_store(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("SESSION_STORE_BACKEND", "memory")
    with pytest.raises(SessionConfigurationError, match="SESSION_STORE_BACKEND=postgres"):
        configured_store()


def test_unknown_persisted_type_is_rejected(tmp_path):
    store = JsonFileSessionStore(tmp_path)
    path = tmp_path / "unsafe.json"
    path.write_text('{"__type__":"dataclass","class":"builtins.object","fields":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported type"):
        store.get("unsafe")
