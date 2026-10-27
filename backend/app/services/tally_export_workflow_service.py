"""Tenant-safe candidate browsing and bulk validation for Tally Export V1."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import desc
from sqlmodel import Session, select

from backend.app.db.models import (
    AuditEvent,
    ReconciliationRun,
    TallyExportBatch,
    TallyExportItem,
    TallyExportProfile,
    TallyPartyLedgerMapping,
    TallyPurchaseLedgerMapping,
)
from backend.app.db.session import get_database_engine
from backend.app.models.tally_export_validation import TallyPriorExportSnapshot
from backend.app.services import object_storage
from backend.app.services import tally_export_batch_service as batch_service
from backend.app.services.tally_export_candidate_service import (
    TallyCandidateSnapshotError,
    TallyCandidateSnapshotUnavailable,
    load_durable_purchase_candidate_rows,
)
from backend.app.services.tally_export_validation_service import (
    _party_snapshot,
    _profile_snapshot,
    _purchase_snapshots,
    validate_tally_candidate,
)
from backend.app.services.tally_export_configuration_service import _authorize_target
from backend.app.services.workspace_service import missing


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(Decimal(value).quantize(Decimal("0.01")), "f")


def _rate(value: Decimal) -> str:
    return format(Decimal(value).quantize(Decimal("0.001")), "f")


def _load_context(db, user, workspace_id, client_id, client_gstin_id, session_id):
    try:
        return load_durable_purchase_candidate_rows(
            db, user, workspace_id, client_id, client_gstin_id, session_id=session_id
        )
    except TallyCandidateSnapshotUnavailable as exc:
        raise HTTPException(409, str(exc)) from None
    except TallyCandidateSnapshotError:
        raise HTTPException(409, "The durable Tally candidate snapshot is unavailable") from None


def _configuration(db, workspace_id, client_id, client_gstin_id):
    target = (
        TallyExportProfile.workspace_id == workspace_id,
        TallyExportProfile.client_id == client_id,
        TallyExportProfile.client_gstin_id == client_gstin_id,
    )
    profile = db.exec(select(TallyExportProfile).where(*target)).first()
    parties = db.exec(select(TallyPartyLedgerMapping).where(
        TallyPartyLedgerMapping.workspace_id == workspace_id,
        TallyPartyLedgerMapping.client_id == client_id,
        TallyPartyLedgerMapping.client_gstin_id == client_gstin_id,
    )).all()
    purchases = db.exec(select(TallyPurchaseLedgerMapping).where(
        TallyPurchaseLedgerMapping.workspace_id == workspace_id,
        TallyPurchaseLedgerMapping.client_id == client_id,
        TallyPurchaseLedgerMapping.client_gstin_id == client_gstin_id,
    )).all()
    previous = db.exec(select(TallyExportItem).where(
        TallyExportItem.workspace_id == workspace_id,
        TallyExportItem.client_id == client_id,
        TallyExportItem.client_gstin_id == client_gstin_id,
        TallyExportItem.outcome == "EXPORTED",
    )).all()
    return profile, parties, purchases, previous


def _search(rows_and_candidates, search: str):
    needle = str(search or "").strip().casefold()
    if not needle:
        return rows_and_candidates
    return [
        pair for pair in rows_and_candidates
        if any(needle in str(value or "").casefold() for value in (
            pair[1].supplier_gstin,
            pair[1].supplier_name,
            pair[1].document_number,
        ))
    ]


def list_candidates(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                    session_id: UUID, *, page: int, page_size: int, search: str = ""):
    with Session(get_database_engine()) as db:
        run, _, rows, candidates = _load_context(
            db, user, workspace_id, client_id, client_gstin_id, session_id
        )
        profile, parties, purchases, previous = _configuration(
            db, workspace_id, client_id, client_gstin_id
        )
        del profile  # Candidate browsing needs mapping state, not profile contents.
        party_by_gstin = {row.supplier_gstin: row for row in parties}
        purchase_by_rate = {
            (row.transaction_kind, Decimal(row.gst_rate).quantize(Decimal("0.001"))): row
            for row in purchases
        }
        exported = {(row.remote_id, row.transaction_fingerprint) for row in previous}

        filtered = _search(list(zip(rows, candidates)), search)
        supplier_counts = Counter(candidate.supplier_gstin for _, candidate in filtered)
        rate_counts = Counter(
            (candidate.transaction_type.value, Decimal(rate).quantize(Decimal("0.001")))
            for _, candidate in filtered for rate in candidate.gst_rates
        )
        supplier_names = {}
        for _, candidate in filtered:
            supplier_names.setdefault(candidate.supplier_gstin, candidate.supplier_name)

        unresolved_suppliers = [
            {
                "supplier_gstin": gstin,
                "supplier_name": supplier_names[gstin],
                "candidate_count": count,
            }
            for gstin, count in sorted(supplier_counts.items())
            if not party_by_gstin.get(gstin) or party_by_gstin[gstin].status != "ACTIVE"
        ]
        unresolved_rates = [
            {
                "transaction_kind": kind,
                "gst_rate": _rate(rate),
                "candidate_count": count,
            }
            for (kind, rate), count in sorted(rate_counts.items(), key=lambda item: item[0])
            if not purchase_by_rate.get((kind, rate))
            or purchase_by_rate[(kind, rate)].status != "ACTIVE"
        ]

        total = len(filtered)
        start = (page - 1) * page_size
        items = []
        for row, candidate in filtered[start:start + page_size]:
            mapping = party_by_gstin.get(candidate.supplier_gstin)
            items.append({
                "candidate_id": row.candidate_id,
                "supplier_gstin": candidate.supplier_gstin,
                "supplier_name": candidate.supplier_name,
                "document_number": candidate.document_number,
                "document_date": candidate.document_date,
                "taxable_value": _money(candidate.taxable_value),
                "igst": _money(candidate.igst),
                "cgst": _money(candidate.cgst),
                "sgst": _money(candidate.sgst),
                "cess": _money(candidate.cess),
                "invoice_total": _money(candidate.invoice_total),
                "gst_rates": [_rate(rate) for rate in candidate.gst_rates],
                "tax_structure": candidate.tax_structure.value,
                "party_mapping_status": (
                    "MAPPED" if mapping and mapping.status == "ACTIVE" else "UNMAPPED"
                ),
                "required_purchase_rates": [_rate(rate) for rate in candidate.gst_rates],
                "previously_exported": (row.remote_id, row.transaction_fingerprint) in exported,
                "financial_parse_issues": [
                    {"field": issue.field_name, "reason": issue.reason}
                    for issue in candidate.financial_parse_issues
                ],
            })
        return {
            "session_id": session_id,
            "reconciliation_run_id": run.id,
            "run_version": run.result_version,
            "items": items,
            "requirements": {
                "unresolved_suppliers": unresolved_suppliers,
                "unresolved_purchase_rates": unresolved_rates,
            },
            "page": page,
            "page_size": page_size,
            "total": total,
        }


def validate_candidates(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                        session_id: UUID, *, candidate_ids: list[str], expected_run_version: int):
    with Session(get_database_engine()) as db:
        _, selected = _validated_selection(
            db, user, workspace_id, client_id, client_gstin_id, session_id,
            candidate_ids=candidate_ids, expected_run_version=expected_run_version,
        )
        results = []
        for _, result in selected:
            results.append({
                "candidate_id": result.candidate_id,
                "eligible": result.eligible,
                "blockers": [issue.__dict__ for issue in result.blockers],
                "warnings": [issue.__dict__ for issue in result.warnings],
                "resolved": {
                    "profile_id": result.resolved.profile_id,
                    "profile_version": result.resolved.profile_version,
                    "tally_version": result.resolved.tally_version,
                    "party_ledger_name": result.resolved.party_ledger_name,
                    "purchase_ledgers": [
                        {"gst_rate": _rate(rate), "ledger_name": name}
                        for rate, name in result.resolved.purchase_ledgers
                    ],
                },
            })
        return {
            "exportable_count": sum(item["eligible"] for item in results),
            "blocked_count": sum(not item["eligible"] for item in results),
            "warning_count": sum(len(item["warnings"]) for item in results),
            "items": results,
        }


def _validated_selection(db, user, workspace_id, client_id, client_gstin_id, session_id,
                         *, candidate_ids, expected_run_version):
    run, gstin, rows, candidates = _load_context(
        db, user, workspace_id, client_id, client_gstin_id, session_id
    )
    if run.result_version != expected_run_version:
        raise HTTPException(409, "The reconciliation run changed; refresh candidates and retry")
    by_id = {row.candidate_id: candidate for row, candidate in zip(rows, candidates)}
    if any(candidate_id not in by_id for candidate_id in candidate_ids):
        raise HTTPException(409, "One or more Tally candidates are unknown or stale")
    profile, parties, purchases, previous = _configuration(
        db, workspace_id, client_id, client_gstin_id
    )
    party_by_gstin = {row.supplier_gstin: row for row in parties}
    prior = tuple(
        TallyPriorExportSnapshot(row.remote_id, row.transaction_fingerprint)
        for row in previous
    )
    selected = []
    for candidate_id in candidate_ids:
        candidate = by_id[candidate_id]
        validation = validate_tally_candidate(
            candidate,
            client_gstin=gstin.gstin,
            profile=_profile_snapshot(profile),
            party_mapping=_party_snapshot(party_by_gstin.get(candidate.supplier_gstin)),
            purchase_mappings=_purchase_snapshots(purchases),
            prior_exports=prior,
        )
        selected.append((candidate, validation))
    return run, selected


def _download_url(workspace_id, client_id, client_gstin_id, batch_id):
    return (
        f"/api/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/"
        f"tally-export/batches/{batch_id}/download"
    )


def _batch_summary(batch, session_id):
    artifact_ready = False
    if batch.status == "GENERATED" and batch.artifact_object_ref and batch.content_sha256:
        try:
            artifact_ready = object_storage.artifact_available(batch.artifact_object_ref)
        except Exception:
            artifact_ready = False
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "created_at": batch.created_at,
        "reconciliation_run_id": batch.reconciliation_run_id,
        "session_id": session_id,
        "candidate_count": batch.selected_count,
        "generated_count": batch.exported_count,
        "failed_count": (
            max(batch.selected_count - batch.exported_count, 0)
            if batch.status == "FAILED" else batch.blocked_count
        ),
        "checksum_sha256": batch.content_sha256,
        "artifact_available": artifact_ready,
        "download_url": _download_url(
            batch.workspace_id, batch.client_id, batch.client_gstin_id, batch.id
        ) if artifact_ready else None,
    }


def _scoped_batch(db, user, workspace_id, client_id, client_gstin_id, batch_id):
    _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
    batch = db.exec(select(TallyExportBatch).where(
        TallyExportBatch.workspace_id == workspace_id,
        TallyExportBatch.client_id == client_id,
        TallyExportBatch.client_gstin_id == client_gstin_id,
        TallyExportBatch.id == batch_id,
    )).first()
    if not batch:
        missing()
    return batch


def generate_batch(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                   session_id: UUID, *, candidate_ids: list[str], expected_run_version: int,
                   allow_reexport: bool, operation_key: UUID):
    with Session(get_database_engine()) as db:
        run, selected = _validated_selection(
            db, user, workspace_id, client_id, client_gstin_id, session_id,
            candidate_ids=candidate_ids, expected_run_version=expected_run_version,
        )
        if any(not validation.eligible for _, validation in selected):
            raise HTTPException(409, "One or more selected Tally candidates are blocked")
        run_id = run.id
    try:
        generated = batch_service.generate_and_persist_tally_batch(
            user, workspace_id, client_id, client_gstin_id, run_id, selected,
            operation_key=operation_key, allow_reexport=allow_reexport,
        )
    except batch_service.TallyExportIdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except batch_service.TallyVoucherExportError as exc:
        raise HTTPException(409, str(exc)) from None
    except batch_service.TallyExportPersistenceError as exc:
        if "artifact persistence" in str(exc).casefold():
            raise HTTPException(503, "Private XML artifact persistence failed") from None
        raise HTTPException(409, "The Tally export changed; refresh and retry") from None

    with Session(get_database_engine()) as db:
        batch = _scoped_batch(
            db, user, workspace_id, client_id, client_gstin_id, generated.batch_id
        )
        response = _batch_summary(batch, session_id)
        response["reused"] = generated.reused
        return response


def list_batches(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                 page: int, page_size: int, session_id: UUID | None = None):
    with Session(get_database_engine()) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        query = select(TallyExportBatch).where(
            TallyExportBatch.workspace_id == workspace_id,
            TallyExportBatch.client_id == client_id,
            TallyExportBatch.client_gstin_id == client_gstin_id,
        )
        if session_id is not None:
            run = db.exec(select(ReconciliationRun).where(
                ReconciliationRun.workspace_id == workspace_id,
                ReconciliationRun.client_id == client_id,
                ReconciliationRun.client_gstin_id == client_gstin_id,
                ReconciliationRun.redis_session_id == session_id,
                ReconciliationRun.deleted_at.is_(None),
            )).first()
            if not run:
                missing()
            query = query.where(TallyExportBatch.reconciliation_run_id == run.id)
        rows = db.exec(query.order_by(
            desc(TallyExportBatch.created_at), desc(TallyExportBatch.id)
        )).all()
        total = len(rows)
        start = (page - 1) * page_size
        run_ids = {row.reconciliation_run_id for row in rows[start:start + page_size]}
        runs = db.exec(select(ReconciliationRun).where(ReconciliationRun.id.in_(run_ids))).all() if run_ids else []
        sessions = {run.id: run.redis_session_id for run in runs}
        return {
            "items": [_batch_summary(row, sessions.get(row.reconciliation_run_id))
                      for row in rows[start:start + page_size]],
            "page": page, "page_size": page_size, "total": total,
        }


def get_batch(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
              batch_id: UUID):
    with Session(get_database_engine()) as db:
        batch = _scoped_batch(db, user, workspace_id, client_id, client_gstin_id, batch_id)
        run = db.get(ReconciliationRun, batch.reconciliation_run_id)
        items = db.exec(select(TallyExportItem).where(
            TallyExportItem.workspace_id == workspace_id,
            TallyExportItem.client_id == client_id,
            TallyExportItem.client_gstin_id == client_gstin_id,
            TallyExportItem.batch_id == batch.id,
        ).order_by(TallyExportItem.created_at, TallyExportItem.id)).all()
        return {
            "batch": _batch_summary(batch, run.redis_session_id if run else None),
            "failure_code": batch.failure_code,
            "failure_message": batch.failure_message,
            "items": [
                {**(item.display_snapshot_json or {}), "candidate_id": item.candidate_id,
                 "outcome": item.outcome, "warning_codes": list(item.warning_codes)}
                for item in items
            ],
        }


def download_batch(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                   batch_id: UUID):
    with Session(get_database_engine()) as db:
        batch = _scoped_batch(db, user, workspace_id, client_id, client_gstin_id, batch_id)
        if batch.status != "GENERATED":
            raise HTTPException(409, "The Tally export artifact is not ready")
        reference = batch.artifact_object_ref
        if not reference or not batch_service.tally_artifact_reference_belongs_to_batch(
            batch.id, reference
        ):
            raise HTTPException(410, "The Tally export artifact is no longer available")
        try:
            if not object_storage.artifact_available(reference, verify_remote=True):
                raise HTTPException(410, "The Tally export artifact is no longer available")
            response = object_storage.artifact_response(
                reference,
                filename=f"Tally-Purchases-{batch.id}.xml",
                media_type="application/xml",
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Tally export delivery is temporarily unavailable") from None
        db.add(AuditEvent(
            workspace_id=workspace_id, client_id=client_id,
            reconciliation_run_id=batch.reconciliation_run_id,
            actor_user_id=user.id, action="TALLY_EXPORT_BATCH_DOWNLOADED",
            entity_type=batch.__tablename__, entity_id=batch.id,
            event_metadata={"status": batch.status, "tally_version": batch.tally_version},
        ))
        db.commit()
        return response
