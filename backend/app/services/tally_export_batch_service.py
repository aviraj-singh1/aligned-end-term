"""Internal generation and persistence workflow for Tally XML batches."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
import tempfile
from typing import Callable, Sequence
from uuid import UUID, uuid4

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import (
    AuditEvent,
    ReconciliationRun,
    TallyExportBatch,
    TallyExportItem,
    TallyExportProfile,
)
from backend.app.db.session import get_database_engine
from backend.app.models.tally_export import TallyExportCandidate
from backend.app.models.tally_export_validation import TallyCandidateValidation
from backend.app.models.tally_voucher import PersistedTallyXmlArtifact
from backend.app.services.object_storage import (
    local_storage_root,
    persist_artifact,
    storage_backend,
)
from backend.app.services.tally_export_configuration_service import _authorize_target
from backend.app.services.tally_export_validation_service import (
    deterministic_remote_id,
    transaction_fingerprint,
)
from backend.app.services.tally_voucher_exporter import (
    DEFAULT_MAX_BATCH_VOUCHERS,
    TallyVoucherExportError,
    build_common_purchase_voucher,
    generate_tally_xml_artifact,
)


ArtifactWriter = Callable[[UUID, bytes], str]


class TallyExportPersistenceError(RuntimeError):
    pass


class TallyExportIdempotencyConflict(TallyExportPersistenceError):
    pass


def tally_export_batch_limit() -> int:
    raw = os.getenv("TALLY_EXPORT_MAX_BATCH_VOUCHERS", str(DEFAULT_MAX_BATCH_VOUCHERS))
    try:
        value = int(raw)
    except ValueError:
        raise TallyExportPersistenceError("TALLY_EXPORT_MAX_BATCH_VOUCHERS must be an integer") from None
    if value < 1 or value > 5000:
        raise TallyExportPersistenceError("TALLY_EXPORT_MAX_BATCH_VOUCHERS must be between 1 and 5000")
    return value


def persist_tally_xml_artifact(batch_id: UUID, xml_bytes: bytes) -> str:
    """Persist privately without exposing a public download surface."""
    filename = f"tally-purchases-{batch_id}.xml"
    if storage_backend() == "local":
        target = (local_storage_root() / "tally-exports" / filename).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
        try:
            temporary.write_bytes(xml_bytes)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(target)
    with tempfile.TemporaryDirectory(prefix="aligned-tally-export-") as directory:
        local_path = Path(directory) / filename
        local_path.write_bytes(xml_bytes)
        return persist_artifact(
            str(batch_id), local_path, category="tally-exports",
            filename=filename, content_type="application/xml",
        )


def tally_artifact_reference_belongs_to_batch(batch_id: UUID, reference: str) -> bool:
    """Validate the exact server-owned artifact location without touching storage."""
    filename = f"tally-purchases-{batch_id}.xml"
    try:
        if storage_backend() == "local":
            expected = (local_storage_root() / "tally-exports" / filename).resolve()
            return Path(reference).resolve() == expected
        from backend.app.services.object_storage import is_s3_reference, s3_settings

        if not is_s3_reference(reference):
            return False
        settings = s3_settings()
        prefix = (
            f"s3://{settings.bucket}/{settings.prefix}/sessions/{batch_id}/"
            "tally-exports/"
        )
        if not reference.startswith(prefix):
            return False
        tail = reference[len(prefix):].split("/")
        return (
            len(tail) == 2
            and len(tail[0]) == 32
            and all(character in "0123456789abcdef" for character in tail[0])
            and tail[1] == filename
        )
    except (OSError, ValueError, RuntimeError):
        return False


def _audit(db: Session, user, batch: TallyExportBatch, action: str, metadata: dict) -> None:
    db.add(AuditEvent(
        workspace_id=batch.workspace_id,
        client_id=batch.client_id,
        reconciliation_run_id=batch.reconciliation_run_id,
        actor_user_id=user.id,
        action=action,
        entity_type=batch.__tablename__,
        entity_id=batch.id,
        event_metadata=metadata,
    ))


def _request_fingerprint(
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    reconciliation_run_id: UUID,
    reconciliation_result_version: int,
    profile_id: UUID,
    profile_version: int,
    candidates: Sequence[tuple[TallyExportCandidate, TallyCandidateValidation]],
    allow_reexport: bool,
) -> str:
    selected = sorted(
        (
            validation.candidate_id,
            validation.fingerprint,
            validation.resolved.party_ledger_name,
            tuple(sorted(
                (str(rate.quantize(Decimal("0.001"))), ledger)
                for rate, ledger in validation.resolved.purchase_ledgers
            )),
        )
        for _, validation in candidates
    )
    if len(selected) != len({row[0] for row in selected}):
        raise TallyVoucherExportError("A candidate cannot be selected more than once")
    payload = {
        "version": 1,
        "workspace_id": str(workspace_id),
        "client_id": str(client_id),
        "client_gstin_id": str(client_gstin_id),
        "reconciliation_run_id": str(reconciliation_run_id),
        "reconciliation_result_version": reconciliation_result_version,
        "profile_id": str(profile_id),
        "profile_version": profile_version,
        "tally_version": candidates[0][1].resolved.tally_version,
        "company_name": candidates[0][1].resolved.company_name,
        "purchase_voucher_type": candidates[0][1].resolved.purchase_voucher_type,
        "tax_ledgers": {
            "igst": candidates[0][1].resolved.igst_ledger_name,
            "cgst": candidates[0][1].resolved.cgst_ledger_name,
            "sgst": candidates[0][1].resolved.sgst_ledger_name,
        },
        "allow_reexport": allow_reexport,
        "selected": selected,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _display_snapshot(candidate: TallyExportCandidate) -> dict[str, object]:
    values = {
        "supplier_gstin": candidate.supplier_gstin,
        "supplier_name": candidate.supplier_name,
        "document_number": candidate.document_number,
        "document_date": candidate.document_date.isoformat() if candidate.document_date else None,
        "taxable_value": str(candidate.taxable_value),
        "total_tax": str(candidate.igst + candidate.cgst + candidate.sgst + candidate.cess),
        "invoice_total": str(candidate.invoice_total) if candidate.invoice_total is not None else None,
        "gst_rates": [str(value) for value in candidate.gst_rates],
    }
    if any(len(str(values[key] or "")) > limit for key, limit in (
        ("supplier_gstin", 15), ("supplier_name", 1024), ("document_number", 1024)
    )):
        raise TallyVoucherExportError("Candidate display fields exceed their persistence limits")
    if len(json.dumps(values, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > 8192:
        raise TallyVoucherExportError("Candidate display snapshot exceeds its persistence limit")
    return values


def _persisted_result(batch: TallyExportBatch, *, reused: bool) -> PersistedTallyXmlArtifact:
    return PersistedTallyXmlArtifact(
        batch_id=batch.id,
        artifact_object_ref=batch.artifact_object_ref,
        artifact=None,
        status=batch.status,
        reused=reused,
        content_sha256=batch.content_sha256,
    )


def _matching_operation(
    db: Session,
    user,
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    operation_key: UUID,
) -> TallyExportBatch | None:
    return db.exec(select(TallyExportBatch).where(
        TallyExportBatch.workspace_id == workspace_id,
        TallyExportBatch.client_id == client_id,
        TallyExportBatch.client_gstin_id == client_gstin_id,
        TallyExportBatch.created_by == user.id,
        TallyExportBatch.operation_key == operation_key,
    )).first()


def _reuse_or_conflict(
    batch: TallyExportBatch,
    request_fingerprint: str,
) -> PersistedTallyXmlArtifact:
    if batch.request_fingerprint != request_fingerprint:
        raise TallyExportIdempotencyConflict(
            "The Idempotency-Key was already used for a different Tally export request"
        )
    return _persisted_result(batch, reused=True)


def _mark_failed(batch_id: UUID, user, code: str, message: str) -> None:
    with Session(get_database_engine()) as db:
        batch = db.exec(select(TallyExportBatch).where(
            TallyExportBatch.id == batch_id,
        ).with_for_update()).first()
        if not batch or batch.status != "PENDING":
            return
        batch.status = "FAILED"
        batch.failure_code = code[:100]
        batch.failure_message = message[:500]
        batch.updated_at = utcnow()
        db.add(batch)
        items = db.exec(select(TallyExportItem).where(TallyExportItem.batch_id == batch.id)).all()
        for item in items:
            item.outcome = "SKIPPED"
            db.add(item)
        _audit(db, user, batch, "TALLY_EXPORT_BATCH_FAILED", {
            "failure_code": batch.failure_code,
            "reason": batch.failure_message,
            "selected_count": batch.selected_count,
        })
        db.commit()


def generate_and_persist_tally_batch(
    user,
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    reconciliation_run_id: UUID,
    candidates: Sequence[tuple[TallyExportCandidate, TallyCandidateValidation]],
    *,
    operation_key: UUID,
    allow_reexport: bool = False,
    maximum_vouchers: int | None = None,
    artifact_writer: ArtifactWriter | None = None,
) -> PersistedTallyXmlArtifact:
    """Create a private GENERATED batch; never marks vouchers as imported."""
    limit = maximum_vouchers if maximum_vouchers is not None else tally_export_batch_limit()
    artifact_writer = artifact_writer or persist_tally_xml_artifact
    if operation_key is None:
        raise TallyExportPersistenceError("An Idempotency-Key is required for Tally export generation")
    if not candidates:
        raise TallyVoucherExportError("At least one eligible candidate is required")
    if len(candidates) > limit:
        raise TallyVoucherExportError(f"Tally export batch exceeds the {limit}-voucher limit")

    with Session(get_database_engine(), expire_on_commit=False) as db:
        _, gstin = _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        run = db.exec(select(ReconciliationRun).where(
            ReconciliationRun.workspace_id == workspace_id,
            ReconciliationRun.client_id == client_id,
            ReconciliationRun.client_gstin_id == client_gstin_id,
            ReconciliationRun.id == reconciliation_run_id,
            ReconciliationRun.status == "COMPLETED",
            ReconciliationRun.deleted_at.is_(None),
        )).first()
        if not run:
            raise TallyExportPersistenceError("Completed reconciliation run was not found")

        first_validation = candidates[0][1]
        resolved = first_validation.resolved
        if resolved.profile_id is None or resolved.profile_version is None:
            raise TallyVoucherExportError("Resolved Tally profile is required")
        profile = db.exec(select(TallyExportProfile).where(
            TallyExportProfile.workspace_id == workspace_id,
            TallyExportProfile.client_id == client_id,
            TallyExportProfile.client_gstin_id == client_gstin_id,
            TallyExportProfile.id == resolved.profile_id,
            TallyExportProfile.status == "ACTIVE",
        ).with_for_update()).first()
        if not profile or profile.version != resolved.profile_version:
            raise TallyExportPersistenceError("Tally export configuration changed; validate again")

        request_fingerprint = _request_fingerprint(
            workspace_id, client_id, client_gstin_id, reconciliation_run_id,
            run.result_version, profile.id, profile.version, candidates, allow_reexport,
        )
        existing_operation = _matching_operation(
            db, user, workspace_id, client_id, client_gstin_id, operation_key,
        )
        if existing_operation:
            return _reuse_or_conflict(existing_operation, request_fingerprint)

        batch = TallyExportBatch(
            workspace_id=workspace_id, client_id=client_id,
            client_gstin_id=client_gstin_id, reconciliation_run_id=reconciliation_run_id,
            profile_id=profile.id, profile_version=profile.version,
            tally_version=profile.tally_version, selected_count=len(candidates),
            warning_count=sum(bool(validation.warnings) for _, validation in candidates),
            operation_key=operation_key,
            request_fingerprint=request_fingerprint,
            created_by=user.id,
        )
        vouchers = []
        previous_items: dict[UUID, TallyExportItem | None] = {}
        for candidate, validation in candidates:
            if not validation.eligible or validation.blockers:
                raise TallyVoucherExportError("Blocked candidates cannot reach Tally XML generation")
            common_profile = (
                validation.resolved.profile_id,
                validation.resolved.profile_version,
                validation.resolved.tally_version,
                validation.resolved.company_name,
                validation.resolved.purchase_voucher_type,
                validation.resolved.igst_ledger_name,
                validation.resolved.cgst_ledger_name,
                validation.resolved.sgst_ledger_name,
            )
            expected_profile = (
                resolved.profile_id, resolved.profile_version, resolved.tally_version,
                resolved.company_name, resolved.purchase_voucher_type,
                resolved.igst_ledger_name, resolved.cgst_ledger_name,
                resolved.sgst_ledger_name,
            )
            if common_profile != expected_profile:
                raise TallyVoucherExportError("All candidates must use the same Tally profile")
            expected_remote = deterministic_remote_id(candidate, gstin.gstin)
            expected_fingerprint = transaction_fingerprint(candidate, gstin.gstin)
            if validation.remote_id != expected_remote or validation.fingerprint != expected_fingerprint:
                raise TallyVoucherExportError("Candidate validation identity is stale or inconsistent")
            previous = db.exec(select(TallyExportItem).where(
                TallyExportItem.workspace_id == workspace_id,
                TallyExportItem.client_id == client_id,
                TallyExportItem.client_gstin_id == client_gstin_id,
                TallyExportItem.remote_id == validation.remote_id,
                TallyExportItem.outcome == "EXPORTED",
            ).order_by(desc(TallyExportItem.created_at), desc(TallyExportItem.id))).first()
            if previous and previous.transaction_fingerprint != validation.fingerprint:
                raise TallyVoucherExportError("Voucher identity was previously exported with changed financial data")
            if previous and not allow_reexport:
                raise TallyVoucherExportError("Explicit confirmation is required to regenerate a previous export")
            previous_items[validation.remote_id] = previous
            vouchers.append(build_common_purchase_voucher(
                candidate, validation, client_gstin=gstin.gstin,
                reconciliation_run_id=reconciliation_run_id,
            ))

        artifact = generate_tally_xml_artifact(
            vouchers, batch_id=batch.id, profile_id=profile.id,
            profile_version=profile.version, reconciliation_run_id=reconciliation_run_id,
            maximum_vouchers=limit,
        )
        db.add(batch)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing_operation = _matching_operation(
                db, user, workspace_id, client_id, client_gstin_id, operation_key,
            )
            if not existing_operation:
                raise
            return _reuse_or_conflict(existing_operation, request_fingerprint)
        for candidate, validation in candidates:
            previous = previous_items[validation.remote_id]
            db.add(TallyExportItem(
                workspace_id=workspace_id, client_id=client_id,
                client_gstin_id=client_gstin_id, batch_id=batch.id,
                candidate_id=validation.candidate_id,
                source_identity_json={
                    "row_count": len(candidate.source_rows),
                    "rows": [
                        {
                            "source": row.source.value,
                            "sheet": row.source_sheet,
                            "row": row.source_row,
                            "category": row.sheet_category.value,
                        }
                        for row in candidate.source_rows[:100]
                    ],
                    "truncated": len(candidate.source_rows) > 100,
                },
                transaction_fingerprint=validation.fingerprint,
                remote_id=validation.remote_id,
                document_kind=candidate.transaction_type.value,
                outcome="SELECTED",
                blocker_codes=[],
                warning_codes=[issue.code for issue in validation.warnings],
                display_snapshot_json=_display_snapshot(candidate),
                previous_export_item_id=previous.id if previous else None,
            ))
        db.commit()
        batch_id = batch.id

    try:
        artifact_reference = artifact_writer(batch_id, artifact.xml_bytes)
        if not str(artifact_reference or "").strip():
            raise TallyExportPersistenceError("Private artifact storage returned no reference")
    except Exception as exc:
        _mark_failed(
            batch_id, user,
            "ARTIFACT_PERSISTENCE_FAILED",
            "Private XML artifact persistence failed",
        )
        if isinstance(exc, TallyExportPersistenceError):
            raise
        raise TallyExportPersistenceError("Private XML artifact persistence failed") from exc

    with Session(get_database_engine(), expire_on_commit=False) as db:
        batch = db.exec(select(TallyExportBatch).where(
            TallyExportBatch.workspace_id == workspace_id,
            TallyExportBatch.client_id == client_id,
            TallyExportBatch.client_gstin_id == client_gstin_id,
            TallyExportBatch.id == batch_id,
        ).with_for_update()).first()
        if not batch or batch.status != "PENDING":
            raise TallyExportPersistenceError("Tally export batch changed before publication")
        batch.status = "GENERATED"
        batch.exported_count = len(candidates)
        batch.artifact_object_ref = artifact_reference
        batch.content_sha256 = artifact.content_sha256
        batch.updated_at = utcnow()
        db.add(batch)
        items = db.exec(select(TallyExportItem).where(TallyExportItem.batch_id == batch.id)).all()
        for item in items:
            item.outcome = "EXPORTED"
            db.add(item)
        _audit(db, user, batch, "TALLY_EXPORT_BATCH_GENERATED", {
            "voucher_count": len(candidates),
            "content_sha256": artifact.content_sha256,
            "tally_version": batch.tally_version,
        })
        db.commit()
    return PersistedTallyXmlArtifact(
        batch_id=batch_id,
        artifact_object_ref=artifact_reference,
        artifact=artifact,
        status="GENERATED",
        content_sha256=artifact.content_sha256,
    )
