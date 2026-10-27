"""Durable, tenant-scoped snapshots of authoritative Not-in-Tally purchases."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

from sqlmodel import Session, select

from backend.app.db.models import (
    ClientGSTIN,
    ReconciliationRun,
    TallyExportCandidateSnapshot,
)
from backend.app.db.session import get_database_engine
from backend.app.models.records import FinancialParseIssue, RecordSource, SheetCategory
from backend.app.models.tally_export import (
    ReverseChargeStatus,
    TallyExportCandidate,
    TallyExportSourceIdentity,
    TallyExportTransactionType,
    TallyRateAllocation,
)
from backend.app.services.tally_export_configuration_service import _authorize_target
from backend.app.services.tally_export_contract import purchase_candidate
from backend.app.services.tally_export_validation_service import (
    deterministic_remote_id,
    transaction_fingerprint,
)
from backend.app.services.workspace_service import missing


class TallyCandidateSnapshotError(RuntimeError):
    pass


class TallyCandidateSnapshotUnavailable(TallyCandidateSnapshotError):
    pass


def _bounded_json(value, label: str, maximum: int):
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise TallyCandidateSnapshotError(f"{label} is not JSON serializable") from None
    if len(encoded) > maximum:
        raise TallyCandidateSnapshotError(f"{label} exceeds its durable snapshot limit")
    return value


def _bounded_text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    text = str(value or "")
    if required and not text.strip():
        raise TallyCandidateSnapshotError(f"{label} is required for a durable Tally candidate")
    if len(text) > maximum:
        raise TallyCandidateSnapshotError(f"{label} exceeds {maximum} characters")
    return text


def _rate_allocations(candidate: TallyExportCandidate) -> list[dict[str, str]]:
    return [
        {
            "gst_rate": str(row.gst_rate),
            "taxable_value": str(row.taxable_value),
            "igst": str(row.igst),
            "cgst": str(row.cgst),
            "sgst": str(row.sgst),
            "cess": str(row.cess),
        }
        for row in candidate.rate_allocations
    ]


def _financial_issues(candidate: TallyExportCandidate) -> list[dict[str, str]]:
    return [
        {
            "field_name": _bounded_text(issue.field_name, "Financial issue field", 100),
            "raw_value": _bounded_text(issue.raw_value, "Financial issue value", 500),
            "reason": _bounded_text(issue.reason, "Financial issue reason", 500),
        }
        for issue in candidate.financial_parse_issues
    ]


def _source_identity(candidate: TallyExportCandidate) -> list[dict[str, object]]:
    return [
        {
            "source": row.source.value,
            "source_sheet": _bounded_text(row.source_sheet, "Source sheet", 255),
            "source_row": row.source_row,
            "sheet_category": row.sheet_category.value,
        }
        for row in candidate.source_rows
    ]


def _snapshot_row(
    run: ReconciliationRun,
    client_gstin: str,
    candidate: TallyExportCandidate,
) -> TallyExportCandidateSnapshot:
    remote_id = deterministic_remote_id(candidate, client_gstin)
    candidate_id = f"{candidate.transaction_type.value}:{remote_id}"
    gst_rates = _bounded_json([str(value) for value in candidate.gst_rates], "GST rates", 4096)
    allocations = _bounded_json(_rate_allocations(candidate), "Rate allocations", 16384)
    place_values = _bounded_json(list(candidate.place_of_supply_values), "Place-of-supply values", 4096)
    reverse_values = _bounded_json(list(candidate.reverse_charge_values), "Reverse-charge values", 4096)
    cancellation = _bounded_json(list(candidate.cancellation_evidence), "Cancellation evidence", 8192)
    issues = _bounded_json(_financial_issues(candidate), "Financial parse issues", 16384)
    sources = _bounded_json(_source_identity(candidate), "Source identity", 65536)
    return TallyExportCandidateSnapshot(
        workspace_id=run.workspace_id,
        client_id=run.client_id,
        client_gstin_id=run.client_gstin_id,
        reconciliation_run_id=run.id,
        result_version=run.result_version,
        candidate_id=candidate_id,
        remote_id=remote_id,
        transaction_fingerprint=transaction_fingerprint(candidate, client_gstin),
        transaction_type=candidate.transaction_type.value,
        tax_structure=candidate.tax_structure.value,
        supplier_gstin=_bounded_text(candidate.supplier_gstin, "Supplier GSTIN", 15),
        supplier_gstin_valid=candidate.supplier_gstin_valid,
        supplier_name=_bounded_text(candidate.supplier_name, "Supplier name", 1024, required=True),
        document_number=_bounded_text(candidate.document_number, "Document number", 1024, required=True),
        document_date=candidate.document_date,
        original_invoice_number=_bounded_text(candidate.original_invoice_number, "Original invoice number", 1024),
        taxable_value=candidate.taxable_value,
        igst=candidate.igst,
        cgst=candidate.cgst,
        sgst=candidate.sgst,
        cess=candidate.cess,
        cess_present=candidate.cess_present,
        invoice_total=candidate.invoice_total,
        gst_rates_json=gst_rates,
        rate_allocations_json=allocations,
        gst_rate_missing=candidate.gst_rate_missing,
        place_of_supply=_bounded_text(candidate.place_of_supply, "Place of supply", 255),
        place_of_supply_values_json=place_values,
        place_of_supply_missing=candidate.place_of_supply_missing,
        place_of_supply_conflicting=candidate.place_of_supply_conflicting,
        reverse_charge_status=candidate.reverse_charge_status.value,
        reverse_charge_values_json=reverse_values,
        is_amendment=candidate.is_amendment,
        amendment_original_number=_bounded_text(
            candidate.amendment_original_number, "Amendment original number", 1024
        ),
        amendment_original_date=candidate.amendment_original_date,
        is_cancelled=candidate.is_cancelled,
        cancellation_evidence_json=cancellation,
        financial_parse_issues_json=issues,
        source_identity_json=sources,
    )


def persist_authoritative_purchase_candidates(
    db: Session,
    run: ReconciliationRun,
    reconciliation_summary,
) -> int:
    """Persist one immutable candidate set for the run's current result version."""
    if run.status != "COMPLETED" or run.client_gstin_id is None or run.result_version < 1:
        raise TallyCandidateSnapshotError("A completed, GSTIN-scoped run version is required")
    gstin = db.exec(select(ClientGSTIN).where(
        ClientGSTIN.workspace_id == run.workspace_id,
        ClientGSTIN.client_id == run.client_id,
        ClientGSTIN.id == run.client_gstin_id,
    )).first()
    if not gstin:
        raise TallyCandidateSnapshotError("The reconciliation run GSTIN no longer exists")

    candidates = [purchase_candidate(record) for record in reconciliation_summary.not_in_tally_records]
    rows = [_snapshot_row(run, gstin.gstin, candidate) for candidate in candidates]
    existing = db.exec(select(TallyExportCandidateSnapshot).where(
        TallyExportCandidateSnapshot.reconciliation_run_id == run.id,
        TallyExportCandidateSnapshot.result_version == run.result_version,
    )).all()
    existing_by_id = {row.candidate_id: row for row in existing}
    incoming_ids = {row.candidate_id for row in rows}
    if len(incoming_ids) != len(rows):
        raise TallyCandidateSnapshotError("The authoritative Tally candidate set has duplicate identities")
    if existing and set(existing_by_id) != incoming_ids:
        raise TallyCandidateSnapshotError("The persisted Tally candidate set conflicts with this run version")
    for row in rows:
        prior = existing_by_id.get(row.candidate_id)
        if prior:
            column_names = (
                column.name for column in TallyExportCandidateSnapshot.__table__.columns
                if column.name not in {"id", "created_at", "updated_at"}
            )
            if any(getattr(prior, name) != getattr(row, name) for name in column_names):
                raise TallyCandidateSnapshotError("A durable Tally candidate changed within one result version")
            continue
        db.add(row)
    run.tally_candidate_snapshot_version = run.result_version
    db.add(run)
    return len(rows)


def reconstruct_candidate(row: TallyExportCandidateSnapshot) -> TallyExportCandidate:
    candidate = TallyExportCandidate(
        transaction_type=TallyExportTransactionType(row.transaction_type),
        supplier_gstin=row.supplier_gstin,
        supplier_gstin_valid=row.supplier_gstin_valid,
        supplier_name=row.supplier_name,
        document_number=row.document_number,
        document_date=row.document_date,
        original_invoice_number=row.original_invoice_number,
        taxable_value=Decimal(row.taxable_value),
        igst=Decimal(row.igst),
        cgst=Decimal(row.cgst),
        sgst=Decimal(row.sgst),
        cess=Decimal(row.cess),
        cess_present=row.cess_present,
        invoice_total=Decimal(row.invoice_total) if row.invoice_total is not None else None,
        gst_rates=tuple(Decimal(value) for value in row.gst_rates_json),
        rate_allocations=tuple(TallyRateAllocation(
            gst_rate=Decimal(value["gst_rate"]),
            taxable_value=Decimal(value["taxable_value"]),
            igst=Decimal(value["igst"]),
            cgst=Decimal(value["cgst"]),
            sgst=Decimal(value["sgst"]),
            cess=Decimal(value["cess"]),
        ) for value in row.rate_allocations_json),
        gst_rate_missing=row.gst_rate_missing,
        place_of_supply=row.place_of_supply,
        place_of_supply_values=tuple(row.place_of_supply_values_json),
        place_of_supply_missing=row.place_of_supply_missing,
        place_of_supply_conflicting=row.place_of_supply_conflicting,
        reverse_charge_status=ReverseChargeStatus(row.reverse_charge_status),
        reverse_charge_values=tuple(row.reverse_charge_values_json),
        is_amendment=row.is_amendment,
        amendment_original_number=row.amendment_original_number,
        amendment_original_date=row.amendment_original_date,
        is_cancelled=row.is_cancelled,
        cancellation_evidence=tuple(row.cancellation_evidence_json),
        source_rows=tuple(TallyExportSourceIdentity(
            source=RecordSource(value["source"]),
            source_sheet=value["source_sheet"],
            source_row=int(value["source_row"]),
            sheet_category=SheetCategory(value["sheet_category"]),
        ) for value in row.source_identity_json),
        financial_parse_issues=tuple(FinancialParseIssue(
            field_name=value["field_name"],
            raw_value=value["raw_value"],
            reason=value["reason"],
        ) for value in row.financial_parse_issues_json),
    )
    if candidate.tax_structure.value != row.tax_structure:
        raise TallyCandidateSnapshotError("A durable Tally candidate has inconsistent tax structure")
    return candidate


def load_durable_purchase_candidate_rows(
    db: Session,
    user,
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    *,
    reconciliation_run_id: UUID | None = None,
    session_id: UUID | None = None,
) -> tuple[ReconciliationRun, ClientGSTIN, tuple[TallyExportCandidateSnapshot, ...], tuple[TallyExportCandidate, ...]]:
    """Load and integrity-check one completed run's current durable snapshot."""
    if (reconciliation_run_id is None) == (session_id is None):
        raise ValueError("Exactly one run identifier is required")

    _, gstin = _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
    query = select(ReconciliationRun).where(
        ReconciliationRun.workspace_id == workspace_id,
        ReconciliationRun.client_id == client_id,
        ReconciliationRun.client_gstin_id == client_gstin_id,
        ReconciliationRun.status == "COMPLETED",
        ReconciliationRun.deleted_at.is_(None),
    )
    query = query.where(
        ReconciliationRun.id == reconciliation_run_id
        if reconciliation_run_id is not None
        else ReconciliationRun.redis_session_id == session_id
    )
    run = db.exec(query).first()
    if not run:
        missing()

    rows = tuple(db.exec(
        select(TallyExportCandidateSnapshot).where(
            TallyExportCandidateSnapshot.workspace_id == workspace_id,
            TallyExportCandidateSnapshot.client_id == client_id,
            TallyExportCandidateSnapshot.client_gstin_id == client_gstin_id,
            TallyExportCandidateSnapshot.reconciliation_run_id == run.id,
            TallyExportCandidateSnapshot.result_version == run.result_version,
        ).order_by(TallyExportCandidateSnapshot.candidate_id)
    ).all())
    if run.tally_candidate_snapshot_version != run.result_version:
        raise TallyCandidateSnapshotUnavailable(
            "This completed run has no durable Tally candidate snapshot; rerun reconciliation to export"
        )
    candidates = tuple(reconstruct_candidate(row) for row in rows)
    for row, candidate in zip(rows, candidates):
        if transaction_fingerprint(candidate, gstin.gstin) != row.transaction_fingerprint:
            raise TallyCandidateSnapshotError("A durable Tally candidate failed its integrity check")
    return run, gstin, rows, candidates


def load_durable_purchase_candidates(
    user,
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    reconciliation_run_id: UUID,
) -> tuple[int, tuple[TallyExportCandidate, ...]]:
    """Load the current durable candidate version without using SessionStore."""
    with Session(get_database_engine()) as db:
        run, _, _, candidates = load_durable_purchase_candidate_rows(
            db, user, workspace_id, client_id, client_gstin_id,
            reconciliation_run_id=reconciliation_run_id,
        )
        return run.result_version, candidates
