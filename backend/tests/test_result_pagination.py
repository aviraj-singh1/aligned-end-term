from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.records import ParsedInvoiceRecord
from backend.app.models.reconciliation import (
    AccountingSourceSystem, AggregatedGSTInvoice, AuditTrail, CancelledSourceSide,
    CancelledVoucher, MatchMethod, ReconciliationResult, ReconciliationStatus,
    ReconciliationSummary,
)
from backend.app.services.notes_reconciliation_engine import NotesReconciliationSummary
from backend.app.services.result_pagination import MAX_PAGE_SIZE, paginate_results
from backend.app.services.session_store import decode_session_value, encode_session_value


def result(index, status, *, supplier=None, gstin=None, date_mismatch=False, missing_taxable=False):
    source = ParsedInvoiceRecord(
        supplier_name=supplier or f"Supplier {index:02d}", gstin_original=gstin or f"GSTIN-{index:02d}",
        source_sheet="Purchase Register", source_row=index + 2,
        invoice_date=date(2026, 4, (index % 20) + 1), taxable_value=Decimal(index + 100),
        taxable_value_present=not missing_taxable,
    )
    return ReconciliationResult(
        supplier_tally=source.supplier_name, supplier_gstin=source.gstin_original,
        invoice_number_tally=f"ACC-{index:03d}", invoice_number_gst=f"GST-{index:03d}",
        taxable_value=source.taxable_value, taxable_value_missing=missing_taxable,
        status=status, match_method=MatchMethod.EXACT_INVOICE,
        remarks=f"Reason {index}", date_mismatch=date_mismatch,
        audit=AuditTrail(tally_rows=[source], tally_taxable=source.taxable_value),
    )


@pytest.fixture
def summaries():
    statuses = [
        ReconciliationStatus.PERFECTLY_MATCHED,
        ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE,
        ReconciliationStatus.MAJOR_DIFFERENCE,
        ReconciliationStatus.TAXABLE_VALUE_MISSING,
        ReconciliationStatus.MISSING_IN_GSTR,
        ReconciliationStatus.NEEDS_REVIEW,
        ReconciliationStatus.POTENTIAL_DUPLICATE,
        ReconciliationStatus.INVOICE_NUMBER_MISMATCH,
    ]
    rows = [result(i, statuses[i % len(statuses)], date_mismatch=i in (1, 9), missing_taxable=(i % len(statuses) == 3)) for i in range(24)]
    for i, row in enumerate(rows): row.api_result_id = f"result-{i:03d}"
    not_in = [AggregatedGSTInvoice(
        gstin=f"NIT-{i}", supplier_name=f"Not Supplier {i}", invoice_number_original=f"NIT-{i}",
        invoice_number_normalized=f"NIT{i}", invoice_date=date(2026, 5, 1), taxable_value=Decimal("50"),
    ) for i in range(4)]
    cancelled = [CancelledVoucher(
        id=f"cancel-{i}", source_side=CancelledSourceSide.ACCOUNTS if i == 0 else CancelledSourceSide.GSTR,
        source_system=AccountingSourceSystem.BUSY if i == 0 else AccountingSourceSystem.GSTR,
        supplier=f"Cancelled {i}", invoice_number=f"CAN-{i}", status="Cancelled Voucher",
        cancellation_reason="Explicit marker", cancellation_evidence="Status: Cancelled",
    ) for i in range(2)]
    purchases = ReconciliationSummary(results=rows, not_in_tally_records=not_in, cancelled_vouchers=cancelled)
    purchases.compute_counts()
    notes_rows = [result(50 + i, ReconciliationStatus.PERFECTLY_MATCHED) for i in range(3)]
    for i, row in enumerate(notes_rows): row.api_result_id = f"note-{i}"
    notes = NotesReconciliationSummary(results=notes_rows)
    return purchases, notes


def test_pages_do_not_overlap_and_union_equals_category(summaries):
    purchases, notes = summaries
    pages = [paginate_results(purchases, notes, page=p, page_size=7) for p in range(1, 5)]
    ids = [[item["result_id"] for item in page["items"]] for page in pages]
    assert pages[0]["total"] == 24 and pages[0]["total_pages"] == 4
    assert not set(ids[0]) & set(ids[1])
    assert len(set().union(*(set(page) for page in ids))) == 24
    assert pages[0]["items"][0]["result_id"] == "result-000"


def test_custom_page_size_and_beyond_end(summaries):
    purchases, notes = summaries
    page = paginate_results(purchases, notes, page=2, page_size=5)
    assert len(page["items"]) == 5 and page["page_size"] == 5 and page["page"] == 2
    assert paginate_results(purchases, notes, page=99, page_size=25)["items"] == []


@pytest.mark.parametrize("query,field", [
    ("Supplier 05", "supplier"), ("GSTIN-05", "gstin"),
    ("ACC-005", "accounts_invoice"), ("GST-005", "gst_invoice"),
])
def test_search_fields_and_search_before_pagination(summaries, query, field):
    page = paginate_results(*summaries, search=query, page_size=1)
    assert page["total"] == 1 and query.casefold() in page["items"][0][field].casefold()


def test_sorting_and_deterministic_ties(summaries):
    asc = paginate_results(*summaries, sort_by="supplier", sort_order="asc", page_size=100)["items"]
    desc = paginate_results(*summaries, sort_by="supplier", sort_order="desc", page_size=100)["items"]
    assert [x["supplier"] for x in asc] == sorted(x["supplier"] for x in asc)
    assert [x["supplier"] for x in desc] == sorted((x["supplier"] for x in desc), reverse=True)
    assert paginate_results(*summaries, sort_by="supplier", page_size=100)["items"] == asc
    with pytest.raises(ValueError, match="sort field"):
        paginate_results(*summaries, sort_by="__dict__")


@pytest.mark.parametrize("category,status", [
    ("perfect", ReconciliationStatus.PERFECTLY_MATCHED),
    ("minor", ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE),
    ("major", ReconciliationStatus.MAJOR_DIFFERENCE),
    ("taxable_value_missing", ReconciliationStatus.TAXABLE_VALUE_MISSING),
    ("missing", ReconciliationStatus.MISSING_IN_GSTR),
    ("needs_review", ReconciliationStatus.NEEDS_REVIEW),
    ("potential_duplicate", ReconciliationStatus.POTENTIAL_DUPLICATE),
    ("invoice_mismatch", ReconciliationStatus.INVOICE_NUMBER_MISMATCH),
])
def test_primary_categories(summaries, category, status):
    page = paginate_results(*summaries, category=category, page_size=100)
    assert page["items"] and {item["status"] for item in page["items"]} == {status.value}


def test_cross_cutting_and_structural_categories(summaries):
    date_page = paginate_results(*summaries, category="date_mismatch")
    assert date_page["total"] == 2 and all(item["date_mismatch"] for item in date_page["items"])
    assert paginate_results(*summaries, category="not_in_tally")["total"] == 4
    cancelled = paginate_results(*summaries, category="cancelled_vouchers")
    assert cancelled["total"] == 2 and cancelled["items"][0]["cancellation_evidence"] == "Status: Cancelled"
    assert paginate_results(*summaries, category="notes")["total"] == 3
    assert paginate_results(*summaries, category="all")["total"] == 24


def test_notes_categories_remain_separate_from_purchase_categories(summaries):
    purchases, notes = summaries
    notes.results.extend([
        result(60, ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE),
        result(61, ReconciliationStatus.MAJOR_DIFFERENCE),
        result(62, ReconciliationStatus.MISSING_IN_GSTR),
        result(63, ReconciliationStatus.NOT_IN_TALLY),
        result(64, ReconciliationStatus.NEEDS_REVIEW),
        result(65, ReconciliationStatus.POTENTIAL_DUPLICATE),
    ])
    expected = {
        "Notes Perfect": ReconciliationStatus.PERFECTLY_MATCHED,
        "Notes Minor": ReconciliationStatus.MINOR_ROUNDING_DIFFERENCE,
        "Notes Tax Mismatch": ReconciliationStatus.MAJOR_DIFFERENCE,
        "Notes Missing in GSTR": ReconciliationStatus.MISSING_IN_GSTR,
        "Notes Missing in Debit Notes": ReconciliationStatus.NOT_IN_TALLY,
        "Notes Needs Review": ReconciliationStatus.NEEDS_REVIEW,
        "Notes Potential Duplicate": ReconciliationStatus.POTENTIAL_DUPLICATE,
    }
    for category, status in expected.items():
        page = paginate_results(purchases, notes, category=category, page_size=100)
        assert page["items"] and {item["status"] for item in page["items"]} == {status.value}
        assert all(item["reconciliation_type"] == "NOTE" for item in page["items"])


def test_missing_taxable_null_and_session_reload(summaries):
    restored = decode_session_value(encode_session_value({"purchases": summaries[0], "notes": summaries[1]}))
    page = paginate_results(restored["purchases"], restored["notes"], category="taxable_value_missing")
    assert page["items"] and all(item["accounts_taxable"] is None and item["taxable_value"] is None for item in page["items"])


def test_endpoint_validation_and_maximum(monkeypatch, summaries):
    import backend.app.api.routes.reconciliation as route
    monkeypatch.setattr(route, "get_session", lambda _sid: {"reconciliation_summary": summaries[0], "notes_summary": summaries[1]})
    client = TestClient(app)
    assert client.get("/api/session/test/results?page=0").status_code == 422
    assert client.get(f"/api/session/test/results?page_size={MAX_PAGE_SIZE + 1}").status_code == 422
    assert client.get("/api/session/test/results?sort_by=unsafe").status_code == 400
    assert client.get("/api/session/test/results?category=unknown").status_code == 400
    assert client.get("/api/session/test/results?page=99").json()["items"] == []


@pytest.mark.parametrize("evidence", ["Canonical identity evidence, distinct from the warning.", "Reason 3", ""])
def test_results_api_preserves_canonical_evidence_and_remarks(monkeypatch, evidence):
    import backend.app.api.routes.reconciliation as route
    from backend.app.api.schemas import ResultModel
    from backend.app.services.result_pagination import serialize_result

    row = result(3, ReconciliationStatus.TAXABLE_VALUE_MISSING, missing_taxable=True)
    row.api_result_id = "missing-taxable"
    row.audit.explanation = evidence
    restored = decode_session_value(encode_session_value(row))
    payload = serialize_result(restored)
    assert payload["evidence"] == evidence
    assert payload["remarks"] == row.remarks
    assert ResultModel(**payload).evidence == evidence
    assert payload["accounts_taxable"] is None
    monkeypatch.setattr(route, "get_session", lambda _sid: {
        "reconciliation_summary": ReconciliationSummary(results=[restored]),
        "notes_summary": NotesReconciliationSummary(),
    })
    response = TestClient(app).get("/api/session/test/results")
    assert response.status_code == 200
    assert response.json()["items"] == [payload]
    assert row.audit.explanation == evidence and row.remarks == "Reason 3"
