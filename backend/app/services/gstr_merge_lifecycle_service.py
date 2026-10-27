"""Private-upload control plane for GSTR merge runs; contains no workbook processing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import AuditEvent, GstrMergeRun, GstrMergeStatus
from backend.app.db.revision import DatabaseRevisionError, validate_gstr_database_revision
from backend.app.db.session import get_database_engine
from backend.app.services import object_storage
from backend.app.services.gstr_merge_feature import (
    GSTR_CONSOLIDATION_UNAVAILABLE_DETAIL,
    require_gstr_consolidation_enabled,
)
from backend.app.services.gstr_merge_run_service import (
    ENGINE_VERSION,
    _active_gstin,
    _authorize_client,
    _financial_year,
    _metadata_object,
    _mode,
    _scoped_run,
    _soft_delete_locked,
)
from backend.app.services.workspace_service import missing


QUARTERLY_PERIODS = ("Q1", "Q2", "Q3", "Q4")
MONTHLY_PERIODS = (
    "M04", "M05", "M06", "M07", "M08", "M09",
    "M10", "M11", "M12", "M01", "M02", "M03",
)
FILENAME_MAX_LENGTH = 255
MANIFEST_SCHEMA_VERSION = 1


def _gstr_database_engine(*, require_new_work: bool = False):
    if require_new_work:
        require_gstr_consolidation_enabled()

    engine = get_database_engine()
    try:
        validate_gstr_database_revision(engine)
    except DatabaseRevisionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=GSTR_CONSOLIDATION_UNAVAILABLE_DETAIL,
        ) from exc
    return engine


def _periods(mode: str) -> tuple[str, ...]:
    return QUARTERLY_PERIODS if mode == "QUARTERLY" else MONTHLY_PERIODS


def _display_filename(value: object) -> str:
    if not isinstance(value, str) or any(ord(character) < 32 for character in value):
        raise HTTPException(400, "Each workbook must have a valid display filename")
    filename = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename or len(filename) > FILENAME_MAX_LENGTH:
        raise HTTPException(400, "Workbook filenames must contain 1 to 255 characters")
    if Path(filename).suffix.lower() != ".xlsx":
        raise HTTPException(415, "Only .xlsx workbooks are supported")
    return filename


def _normalized_files(mode: str, files: list[Any]) -> list[dict[str, Any]]:
    required = _periods(mode)
    if len(files) != len(required):
        raise HTTPException(
            400, f"{mode.title()} mode requires exactly {len(required)} workbooks"
        )
    settings = object_storage.gstr_storage_settings()
    normalized: dict[str, dict[str, Any]] = {}
    for raw in files:
        item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        period = str(item.get("period_code", "")).strip().upper()
        if period not in required:
            raise HTTPException(400, f"Unsupported {mode.lower()} period slot: {period or '(blank)'}")
        if period in normalized:
            raise HTTPException(400, f"Duplicate period slot: {period}")
        size = item.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise HTTPException(400, "Declared workbook sizes must be positive integers")
        if size > settings.max_file_bytes:
            raise HTTPException(413, "A workbook exceeds the configured per-file size limit")
        normalized[period] = {
            "period_code": period,
            "filename": _display_filename(item.get("filename")),
            "expected_size_bytes": size,
        }
    if set(normalized) != set(required):
        raise HTTPException(400, "All required period slots must be supplied exactly once")
    ordered = [normalized[period] for period in required]
    if sum(item["expected_size_bytes"] for item in ordered) > settings.max_batch_bytes:
        raise HTTPException(413, "The declared upload batch exceeds the configured size limit")
    return ordered


def _fingerprint(
    client_gstin_id: UUID,
    mode: str,
    financial_year: str,
    files: list[dict[str, Any]],
) -> str:
    payload = {
        "client_gstin_id": str(client_gstin_id),
        "mode": mode,
        "financial_year": financial_year,
        "files": files,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_files(run: GstrMergeRun) -> list[dict[str, Any]]:
    manifest = run.input_manifest_json
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise HTTPException(409, "The stored upload manifest is invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise HTTPException(409, "The stored upload manifest is invalid")
    return files


def _validate_manifest(
    run: GstrMergeRun, *, require_object_refs: bool = True
) -> list[dict[str, Any]]:
    files = _manifest_files(run)
    required = _periods(run.mode)
    if len(files) != len(required):
        raise HTTPException(409, "The stored upload manifest is incomplete")
    by_period: dict[str, dict[str, Any]] = {}
    allowed = {
        "period_code", "filename", "object_ref", "expected_size_bytes",
        "actual_size_bytes", "content_type", "upload_state", "sha256",
    }
    for item in files:
        if not isinstance(item, dict) or set(item) - allowed:
            raise HTTPException(409, "The stored upload manifest contains unexpected metadata")
        period = item.get("period_code")
        if period not in required or period in by_period:
            raise HTTPException(409, "The stored upload manifest has invalid period slots")
        reference = item.get("object_ref")
        if reference is None and not require_object_refs and item.get("upload_state") == "DELETED":
            pass
        elif not isinstance(reference, str) or not object_storage.gstr_reference_belongs_to_run(
                reference,
                run.workspace_id,
                run.client_id,
                run.id,
                category="inputs",
                period_code=period,
            ):
            raise HTTPException(409, "The stored upload manifest has an invalid object reference")
        expected = item.get("expected_size_bytes")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
            raise HTTPException(409, "The stored upload manifest has an invalid declared size")
        by_period[period] = item
    return [by_period[period] for period in required]


def _public_run(run: GstrMergeRun) -> dict[str, Any]:
    manifest = run.input_manifest_json if isinstance(run.input_manifest_json, dict) else {}
    raw_files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    files = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        files.append({
            "period_code": str(item.get("period_code", "")),
            "filename": str(item.get("filename", "")),
            "expected_size_bytes": int(item.get("expected_size_bytes", 0)),
            "actual_size_bytes": item.get("actual_size_bytes"),
            "upload_state": item.get("upload_state", "PENDING"),
        })
    declared_total = sum(item["expected_size_bytes"] for item in files)
    actual_values = [item["actual_size_bytes"] for item in files]
    uploaded_total = (
        sum(value for value in actual_values if isinstance(value, int))
        if actual_values and all(isinstance(value, int) for value in actual_values)
        else None
    )
    now = utcnow()
    artifact_available = bool(
        run.status == GstrMergeStatus.COMPLETED.value
        and run.output_object_ref
        and run.deleted_at is None
        and (run.expires_at is None or run.expires_at > now)
    )
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "client_id": run.client_id,
        "client_gstin_id": run.client_gstin_id,
        "mode": run.mode,
        "financial_year": run.financial_year,
        "status": run.status,
        "engine_version": run.engine_version,
        "version": run.version,
        "attempt_count": run.attempt_count,
        "files": files,
        "declared_total_size_bytes": declared_total,
        "uploaded_total_size_bytes": uploaded_total,
        "result_summary": run.result_summary_json,
        "artifact_available": artifact_available,
        "error_code": run.error_code,
        "safe_error_message": run.safe_error_message,
        "queued_at": manifest.get("queued_at"),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "failed_at": run.failed_at,
        "expires_at": run.expires_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _targets(run: GstrMergeRun) -> list[dict[str, Any]]:
    if run.status != GstrMergeStatus.UPLOADING.value:
        return []
    targets = []
    for item in _validate_manifest(run):
        period = item["period_code"]
        local_url = (
            f"/api/workspaces/{run.workspace_id}/clients/{run.client_id}/"
            f"gstr-merges/{run.id}/uploads/{period}"
        )
        try:
            target = object_storage.create_private_upload_target(
                item["object_ref"],
                run_id=run.id,
                period_code=period,
                expected_size=item["expected_size_bytes"],
                local_url=local_url,
            )
        except (
            object_storage.ObjectStorageConfigurationError,
            object_storage.ObjectStorageUnavailableError,
        ):
            raise HTTPException(503, "Private upload storage is temporarily unavailable") from None
        targets.append({"period_code": period, **target})
    return targets


def create_upload_run(
    user,
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    mode: str,
    financial_year: str,
    files: list[Any],
    operation_id: UUID,
) -> dict[str, Any]:
    with Session(_gstr_database_engine(require_new_work=True), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        _active_gstin(db, workspace_id, client_id, client_gstin_id)
        normalized_mode = _mode(mode)
        normalized_fy = _financial_year(financial_year)
        try:
            normalized_files = _normalized_files(normalized_mode, files)
        except object_storage.ObjectStorageConfigurationError:
            raise HTTPException(503, "Private upload storage is temporarily unavailable") from None
        fingerprint = _fingerprint(
            client_gstin_id, normalized_mode, normalized_fy, normalized_files
        )
        prior = db.exec(select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.operation_id == operation_id,
            AuditEvent.action == "GSTR_MERGE_CREATED",
        )).first()
        if prior:
            if not prior.event_metadata or prior.event_metadata.get("fingerprint") != fingerprint:
                raise HTTPException(409, "Idempotency key was already used for a different request")
            run = _scoped_run(db, workspace_id, client_id, prior.entity_id)
        else:
            settings = object_storage.gstr_storage_settings()
            run = GstrMergeRun(
                workspace_id=workspace_id,
                client_id=client_id,
                client_gstin_id=client_gstin_id,
                created_by=user.id,
                mode=normalized_mode,
                financial_year=normalized_fy,
                engine_version=ENGINE_VERSION,
            )
            manifest_files = []
            for item in normalized_files:
                try:
                    reference = object_storage.gstr_input_reference(
                        workspace_id, client_id, run.id, item["period_code"]
                    )
                except object_storage.ObjectStorageConfigurationError:
                    raise HTTPException(503, "Private upload storage is temporarily unavailable") from None
                manifest_files.append({
                    **item,
                    "object_ref": reference,
                    "actual_size_bytes": None,
                    "content_type": None,
                    "upload_state": "PENDING",
                })
            run.input_manifest_json = _metadata_object({
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "upload_expires_at": (
                    utcnow() + timedelta(seconds=settings.upload_ttl_seconds)
                ).isoformat(),
                "queued_at": None,
                "files": manifest_files,
            }, "Input manifest")
            db.add(run)
            db.flush()
            db.add(AuditEvent(
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=user.id,
                operation_id=operation_id,
                action="GSTR_MERGE_CREATED",
                entity_type="gstr_merge_runs",
                entity_id=run.id,
                event_metadata={
                    "mode": normalized_mode,
                    "financial_year": normalized_fy,
                    "file_count": len(normalized_files),
                    "aggregate_byte_count": sum(
                        item["expected_size_bytes"] for item in normalized_files
                    ),
                    "fingerprint": fingerprint,
                },
            ))
            db.commit()
        result = {"run": _public_run(run), "upload_targets": _targets(run)}
    return result


def list_upload_runs(
    user, workspace_id: UUID, client_id: UUID, *, limit: int = 25
) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise HTTPException(422, "Limit must be between 1 and 100")
    with Session(_gstr_database_engine()) as db:
        _authorize_client(db, user, workspace_id, client_id)
        rows = db.exec(select(GstrMergeRun).where(
            GstrMergeRun.workspace_id == workspace_id,
            GstrMergeRun.client_id == client_id,
            GstrMergeRun.deleted_at.is_(None),
        ).order_by(GstrMergeRun.created_at.desc(), GstrMergeRun.id.desc()).limit(limit)).all()
        return {"items": [_public_run(run) for run in rows]}


def get_upload_run(
    user, workspace_id: UUID, client_id: UUID, run_id: UUID
) -> dict[str, Any]:
    with Session(_gstr_database_engine()) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id)
        if run.status == GstrMergeStatus.DELETED.value:
            missing()
        return _public_run(run)


def _submission_event(
    db, workspace_id: UUID, operation_id: UUID
) -> AuditEvent | None:
    return db.exec(select(AuditEvent).where(
        AuditEvent.workspace_id == workspace_id,
        AuditEvent.operation_id == operation_id,
        AuditEvent.action == "GSTR_MERGE_SUBMITTED",
    )).first()


def submit_upload_run(
    user,
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    expected_version: int,
    operation_id: UUID,
) -> dict[str, Any]:
    with Session(_gstr_database_engine(require_new_work=True), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id, lock=True)
        _active_gstin(db, workspace_id, client_id, run.client_gstin_id)
        try:
            settings = object_storage.gstr_storage_settings()
        except object_storage.ObjectStorageConfigurationError:
            raise HTTPException(503, "Private upload storage is temporarily unavailable") from None
        prior = _submission_event(db, workspace_id, operation_id)
        if prior and prior.entity_id != run.id:
            raise HTTPException(409, "Idempotency key was already used for a different request")
        if run.status == GstrMergeStatus.QUEUED.value:
            return _public_run(run)
        if run.status != GstrMergeStatus.UPLOADING.value:
            raise HTTPException(409, f"A {run.status.lower()} run cannot be submitted")
        if run.version != expected_version:
            raise HTTPException(409, "The GSTR merge run changed; refresh and retry")
        if prior:
            raise HTTPException(409, "The prior submission did not reach a queued state")

        files = _validate_manifest(run)
        aggregate = 0
        verified_files = []
        for item in files:
            try:
                stat = object_storage.private_object_stat(item["object_ref"])
            except object_storage.ObjectStorageUnavailableError:
                raise HTTPException(503, "Private upload storage is temporarily unavailable") from None
            if stat is None:
                raise HTTPException(409, f"Upload is missing for period {item['period_code']}")
            if stat.size_bytes <= 0:
                raise HTTPException(400, f"Uploaded workbook for {item['period_code']} is empty")
            if stat.size_bytes > settings.max_file_bytes:
                raise HTTPException(413, "An uploaded workbook exceeds the configured per-file size limit")
            if stat.content_type and stat.content_type != object_storage.XLSX_MEDIA_TYPE:
                raise HTTPException(415, "Uploaded objects must use the .xlsx content type")
            if object_storage.is_s3_reference(item["object_ref"]):
                expected_metadata = object_storage.gstr_upload_metadata(
                    run.id, item["period_code"], item["expected_size_bytes"]
                )
                if stat.metadata != expected_metadata:
                    raise HTTPException(409, "Uploaded object metadata does not match its issued target")
            aggregate += stat.size_bytes
            if aggregate > settings.max_batch_bytes:
                raise HTTPException(413, "The uploaded batch exceeds the configured size limit")
            verified_files.append({
                **item,
                "actual_size_bytes": stat.size_bytes,
                "content_type": stat.content_type or object_storage.XLSX_MEDIA_TYPE,
                "upload_state": "UPLOADED",
            })

        now = utcnow()
        manifest = dict(run.input_manifest_json or {})
        manifest["files"] = verified_files
        manifest["queued_at"] = now.isoformat()
        run.input_manifest_json = _metadata_object(manifest, "Input manifest")
        run.status = GstrMergeStatus.QUEUED.value
        run.next_attempt_at = now
        run.version += 1
        run.updated_at = now
        run.lease_token = run.lease_expires_at = None
        db.add(run)
        db.add(AuditEvent(
            workspace_id=workspace_id,
            client_id=client_id,
            actor_user_id=user.id,
            operation_id=operation_id,
            action="GSTR_MERGE_SUBMITTED",
            entity_type="gstr_merge_runs",
            entity_id=run.id,
            event_metadata={
                "mode": run.mode,
                "financial_year": run.financial_year,
                "file_count": len(verified_files),
                "aggregate_byte_count": aggregate,
                "prior_status": GstrMergeStatus.UPLOADING.value,
                "current_status": GstrMergeStatus.QUEUED.value,
                "engine_version": run.engine_version,
            },
        ))
        db.commit()
        return _public_run(run)


def authorize_local_upload(
    user,
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    period_code: str,
) -> dict[str, Any]:
    """Resolve one local target only after tenant/run authorization."""
    with Session(_gstr_database_engine()) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id)
        _active_gstin(db, workspace_id, client_id, run.client_gstin_id)
        if run.status != GstrMergeStatus.UPLOADING.value:
            raise HTTPException(409, "Uploads are closed for this GSTR merge run")
        files = _validate_manifest(run)
        item = next((row for row in files if row["period_code"] == period_code.upper()), None)
        if not item:
            missing()
        expiry = (run.input_manifest_json or {}).get("upload_expires_at")
        try:
            expires_at = datetime.fromisoformat(str(expiry))
        except ValueError:
            raise HTTPException(409, "The stored upload manifest is invalid") from None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utcnow():
            raise HTTPException(410, "The upload target has expired; retry run creation")
        if object_storage.storage_backend() != "local" or os.getenv("APP_ENV", "development").lower() != "development":
            raise HTTPException(404, "Resource not found")
        return item


def delete_upload_run(
    user,
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    expected_version: int,
) -> dict[str, Any]:
    with Session(_gstr_database_engine(), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id, lock=True)
        if run.status == GstrMergeStatus.DELETED.value:
            return {"message": "GSTR merge run is already deleted", "run": _public_run(run)}
        if run.version != expected_version:
            raise HTTPException(409, "The GSTR merge run changed; refresh and retry")

        references: list[str] = []
        files = _validate_manifest(run, require_object_refs=False)
        for item in files:
            if item.get("object_ref"):
                references.append(item["object_ref"])
        if run.output_object_ref:
            if not object_storage.gstr_reference_belongs_to_run(
                run.output_object_ref,
                workspace_id,
                client_id,
                run.id,
                category="outputs",
                engine_version=run.engine_version,
            ):
                raise HTTPException(409, "The stored output object reference is invalid")
            references.append(run.output_object_ref)

        try:
            for reference in references:
                object_storage.delete_private_object(reference)
        except object_storage.ObjectStorageUnavailableError:
            raise HTTPException(
                503,
                "Artifact cleanup is incomplete; retry deletion with the same run version",
            ) from None

        cleaned_files = []
        for item in files:
            cleaned = {key: value for key, value in item.items() if key != "object_ref"}
            cleaned["upload_state"] = "DELETED"
            cleaned_files.append(cleaned)
        manifest = dict(run.input_manifest_json or {})
        manifest["files"] = cleaned_files
        run.input_manifest_json = _metadata_object(manifest, "Input manifest")
        run.output_object_ref = None
        run.output_size_bytes = None
        run.output_sha256 = None
        db.add(AuditEvent(
            workspace_id=workspace_id,
            client_id=client_id,
            actor_user_id=user.id,
            action="GSTR_MERGE_ARTIFACTS_DELETED",
            entity_type="gstr_merge_runs",
            entity_id=run.id,
            event_metadata={
                "input_count": len(files),
                "output_count": 1 if len(references) > len(files) else 0,
                "prior_status": run.status,
                "engine_version": run.engine_version,
            },
        ))
        _soft_delete_locked(db, user, run)
        return {"message": "GSTR merge run and known artifacts deleted", "run": _public_run(run)}


def download_upload_result(
    user, workspace_id: UUID, client_id: UUID, run_id: UUID
):
    with Session(_gstr_database_engine(), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id)
        if run.status in {GstrMergeStatus.DELETED.value, GstrMergeStatus.EXPIRED.value}:
            raise HTTPException(410, "The GSTR merge artifact is no longer available")
        if run.expires_at is not None and run.expires_at <= utcnow():
            raise HTTPException(410, "The GSTR merge artifact has expired")
        if run.status != GstrMergeStatus.COMPLETED.value:
            raise HTTPException(409, "The GSTR merge result is not ready")
        reference = run.output_object_ref
        if not reference or not object_storage.gstr_reference_belongs_to_run(
            reference,
            workspace_id,
            client_id,
            run.id,
            category="outputs",
            engine_version=run.engine_version,
        ):
            raise HTTPException(410, "The GSTR merge artifact is no longer available")
        try:
            stat = object_storage.private_object_stat(reference)
            if stat is None or stat.size_bytes != run.output_size_bytes:
                raise HTTPException(410, "The GSTR merge artifact is no longer available")
            response = object_storage.artifact_response(
                reference,
                filename=f"GSTR-Consolidated-{run.financial_year}.xlsx",
                media_type=object_storage.XLSX_MEDIA_TYPE,
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Result delivery is temporarily unavailable") from None
        db.add(AuditEvent(
            workspace_id=workspace_id,
            client_id=client_id,
            actor_user_id=user.id,
            action="GSTR_MERGE_DOWNLOADED",
            entity_type="gstr_merge_runs",
            entity_id=run.id,
            event_metadata={
                "status": run.status,
                "aggregate_byte_count": run.output_size_bytes,
                "engine_version": run.engine_version,
            },
        ))
        db.commit()
        return response
