"""Tenant authorization and durable lifecycle management for GSTR merge runs.

This module manages metadata only. It does not read workbooks, access object
storage, enqueue work, or expose HTTP routes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import (
    AuditEvent,
    ClientGSTIN,
    GstrMergeMode,
    GstrMergeRun,
    GstrMergeStatus,
)
from backend.app.db.session import get_database_engine
from backend.app.services.workspace_service import client_scope, missing, scope


ENGINE_VERSION = "1.0.0"
MAX_METADATA_BYTES = 65_536
FINANCIAL_YEAR_RE = re.compile(r"^20\d{2}-\d{2}$")
FORBIDDEN_METADATA_KEYS = {
    "rows", "records", "invoices", "invoice_rows", "workbook_rows", "parsed_rows",
    "signed_url", "credentials", "secret", "access_token",
}

VALID_TRANSITIONS = {
    GstrMergeStatus.UPLOADING.value: {
        GstrMergeStatus.QUEUED.value,
        GstrMergeStatus.FAILED.value,
        GstrMergeStatus.EXPIRED.value,
        GstrMergeStatus.DELETED.value,
    },
    GstrMergeStatus.QUEUED.value: {
        GstrMergeStatus.PROCESSING.value,
        GstrMergeStatus.FAILED.value,
        GstrMergeStatus.EXPIRED.value,
        GstrMergeStatus.DELETED.value,
    },
    GstrMergeStatus.PROCESSING.value: {
        GstrMergeStatus.QUEUED.value,
        GstrMergeStatus.COMPLETED.value,
        GstrMergeStatus.FAILED.value,
        GstrMergeStatus.EXPIRED.value,
        GstrMergeStatus.DELETED.value,
    },
    GstrMergeStatus.COMPLETED.value: {
        GstrMergeStatus.EXPIRED.value,
        GstrMergeStatus.DELETED.value,
    },
    GstrMergeStatus.FAILED.value: {
        GstrMergeStatus.QUEUED.value,
        GstrMergeStatus.EXPIRED.value,
        GstrMergeStatus.DELETED.value,
    },
    GstrMergeStatus.EXPIRED.value: {GstrMergeStatus.DELETED.value},
    GstrMergeStatus.DELETED.value: set(),
}


def _mode(value: str) -> str:
    try:
        return GstrMergeMode(str(value).strip().upper()).value
    except ValueError:
        raise HTTPException(422, "Mode must be QUARTERLY or MONTHLY") from None


def _financial_year(value: str) -> str:
    normalized = str(value).strip()
    if not FINANCIAL_YEAR_RE.fullmatch(normalized):
        raise HTTPException(422, "Financial year must use YYYY-YY format, for example 2025-26")
    start, end = int(normalized[:4]), int(normalized[5:])
    if (start + 1) % 100 != end:
        raise HTTPException(422, "Financial year must identify consecutive years")
    return normalized


def _metadata_object(value: dict[str, Any] | None, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(422, f"{label} must be a JSON object")

    def inspect(item):
        if isinstance(item, dict):
            for key, nested in item.items():
                if str(key).strip().lower() in FORBIDDEN_METADATA_KEYS:
                    raise HTTPException(422, f"{label} contains prohibited workbook or credential data")
                inspect(nested)
        elif isinstance(item, list):
            for nested in item:
                inspect(nested)

    inspect(value)
    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label} must contain valid JSON values") from None
    if len(encoded) > MAX_METADATA_BYTES:
        raise HTTPException(422, f"{label} exceeds the 65536-byte metadata limit")
    return value


def _authorize_client(db, user, workspace_id: UUID, client_id: UUID):
    _, member = scope(db, user, workspace_id)
    client = client_scope(db, member, client_id)
    if client.status != "ACTIVE":
        missing()
    return member, client


def _active_gstin(db, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID):
    gstin = db.exec(
        select(ClientGSTIN).where(
            ClientGSTIN.workspace_id == workspace_id,
            ClientGSTIN.client_id == client_id,
            ClientGSTIN.id == client_gstin_id,
            ClientGSTIN.status == "ACTIVE",
        )
    ).first()
    if not gstin:
        missing()
    return gstin


def _scoped_run(db, workspace_id: UUID, client_id: UUID, run_id: UUID, *, lock: bool = False):
    query = select(GstrMergeRun).where(
        GstrMergeRun.workspace_id == workspace_id,
        GstrMergeRun.client_id == client_id,
        GstrMergeRun.id == run_id,
    )
    if lock:
        query = query.with_for_update()
    run = db.exec(query).first()
    if not run:
        missing()
    return run


def _run_data(run: GstrMergeRun, *, detail: bool = True) -> dict[str, Any]:
    result = {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "client_id": run.client_id,
        "client_gstin_id": run.client_gstin_id,
        "created_by": run.created_by,
        "mode": run.mode,
        "financial_year": run.financial_year,
        "status": run.status,
        "engine_version": run.engine_version,
        "version": run.version,
        "attempt_count": run.attempt_count,
        "error_code": run.error_code,
        "safe_error_message": run.safe_error_message,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "failed_at": run.failed_at,
        "expires_at": run.expires_at,
        "deleted_at": run.deleted_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
    if detail:
        result.update(
            input_manifest=run.input_manifest_json,
            result_summary=run.result_summary_json,
            output_object_ref=run.output_object_ref,
            output_size_bytes=run.output_size_bytes,
            output_sha256=run.output_sha256,
        )
    return result


def create_run(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
               mode: str, financial_year: str) -> dict[str, Any]:
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        _active_gstin(db, workspace_id, client_id, client_gstin_id)
        normalized_mode = _mode(mode)
        normalized_fy = _financial_year(financial_year)
        run = GstrMergeRun(
            workspace_id=workspace_id,
            client_id=client_id,
            client_gstin_id=client_gstin_id,
            created_by=user.id,
            mode=normalized_mode,
            financial_year=normalized_fy,
            engine_version=ENGINE_VERSION,
        )
        db.add(run)
        db.flush()
        db.add(AuditEvent(
            workspace_id=workspace_id,
            client_id=client_id,
            actor_user_id=user.id,
            action="GSTR_MERGE_CREATED",
            entity_type="gstr_merge_runs",
            entity_id=run.id,
            event_metadata={"mode": normalized_mode, "financial_year": normalized_fy},
        ))
        db.commit()
        return _run_data(run)


def get_run(user, workspace_id: UUID, client_id: UUID, run_id: UUID) -> dict[str, Any]:
    with Session(get_database_engine()) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id)
        if run.status == GstrMergeStatus.DELETED.value:
            missing()
        return _run_data(run)


def list_runs(user, workspace_id: UUID, client_id: UUID, *, limit: int = 25,
              before: datetime | None = None) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise HTTPException(422, "Limit must be between 1 and 100")
    with Session(get_database_engine()) as db:
        _authorize_client(db, user, workspace_id, client_id)
        query = select(GstrMergeRun).where(
            GstrMergeRun.workspace_id == workspace_id,
            GstrMergeRun.client_id == client_id,
            GstrMergeRun.deleted_at.is_(None),
        )
        if before:
            query = query.where(GstrMergeRun.created_at < before)
        rows = db.exec(
            query.order_by(GstrMergeRun.created_at.desc(), GstrMergeRun.id.desc()).limit(limit)
        ).all()
        return [_run_data(run, detail=False) for run in rows]


def set_input_manifest(user, workspace_id: UUID, client_id: UUID, run_id: UUID,
                       manifest: dict[str, Any], expected_version: int) -> dict[str, Any]:
    manifest = _metadata_object(manifest, "Input manifest")
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id, lock=True)
        if run.status != GstrMergeStatus.UPLOADING.value:
            raise HTTPException(409, "Input metadata can only change while a run is uploading")
        if run.version != expected_version:
            raise HTTPException(409, "The GSTR merge run changed; refresh and retry")
        run.input_manifest_json = manifest
        run.version += 1
        run.updated_at = utcnow()
        db.add(run)
        db.commit()
        return _run_data(run)


def transition_run(user, workspace_id: UUID, client_id: UUID, run_id: UUID,
                   target_status: str, expected_version: int, *,
                   result_summary: dict[str, Any] | None = None,
                   output_object_ref: str | None = None,
                   output_size_bytes: int | None = None,
                   output_sha256: str | None = None,
                   error_code: str | None = None,
                   safe_error_message: str | None = None) -> dict[str, Any]:
    try:
        target = GstrMergeStatus(str(target_status).strip().upper()).value
    except ValueError:
        raise HTTPException(422, "Unknown GSTR merge status") from None
    summary = _metadata_object(result_summary, "Result summary")
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id, lock=True)
        if run.version != expected_version:
            raise HTTPException(409, "The GSTR merge run changed; refresh and retry")
        if target not in VALID_TRANSITIONS[run.status]:
            raise HTTPException(409, f"Cannot transition a GSTR merge run from {run.status} to {target}")
        if target == GstrMergeStatus.DELETED.value:
            return _soft_delete_locked(db, user, run)

        now = utcnow()
        if target == GstrMergeStatus.PROCESSING.value:
            run.started_at = now
            run.attempt_count += 1
        elif target == GstrMergeStatus.COMPLETED.value:
            reference = (output_object_ref or "").strip()
            if (not reference or len(reference) > 1024 or output_size_bytes is None
                    or not re.fullmatch(r"[0-9a-f]{64}", output_sha256 or "")):
                raise HTTPException(422, "Completed runs require an object reference, size, and SHA-256")
            if output_size_bytes < 0:
                raise HTTPException(422, "Output size cannot be negative")
            run.completed_at = now
            run.result_summary_json = summary
            run.output_object_ref = reference
            run.output_size_bytes = output_size_bytes
            run.output_sha256 = output_sha256
            run.lease_token = run.lease_expires_at = None
        elif target == GstrMergeStatus.FAILED.value:
            code = (error_code or "").strip()
            if not code or len(code) > 100:
                raise HTTPException(422, "Failed runs require a bounded safe error code")
            message = (safe_error_message or "").strip() or None
            if message and len(message) > 500:
                raise HTTPException(422, "Safe error message exceeds 500 characters")
            run.failed_at = now
            run.error_code = code
            run.safe_error_message = message
            run.lease_token = run.lease_expires_at = None
        elif target == GstrMergeStatus.EXPIRED.value:
            run.expires_at = now
            run.lease_token = run.lease_expires_at = None
        elif target == GstrMergeStatus.QUEUED.value:
            run.lease_token = run.lease_expires_at = None

        run.status = target
        run.version += 1
        run.updated_at = now
        db.add(run)
        db.commit()
        return _run_data(run)


def _soft_delete_locked(db, user, run: GstrMergeRun) -> dict[str, Any]:
    prior_status = run.status
    now = utcnow()
    run.status = GstrMergeStatus.DELETED.value
    run.deleted_at = now
    run.expires_at = run.expires_at or now
    run.lease_token = run.lease_expires_at = None
    run.version += 1
    run.updated_at = now
    db.add(run)
    db.add(AuditEvent(
        workspace_id=run.workspace_id,
        client_id=run.client_id,
        actor_user_id=user.id,
        action="GSTR_MERGE_DELETED",
        entity_type="gstr_merge_runs",
        entity_id=run.id,
        event_metadata={"prior_status": prior_status},
    ))
    db.commit()
    return _run_data(run)


def soft_delete_run(user, workspace_id: UUID, client_id: UUID, run_id: UUID,
                    expected_version: int) -> dict[str, Any]:
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_client(db, user, workspace_id, client_id)
        run = _scoped_run(db, workspace_id, client_id, run_id, lock=True)
        if run.version != expected_version:
            raise HTTPException(409, "The GSTR merge run changed; refresh and retry")
        if GstrMergeStatus.DELETED.value not in VALID_TRANSITIONS[run.status]:
            raise HTTPException(409, "This GSTR merge run is already deleted")
        return _soft_delete_locked(db, user, run)


def expire_run(user, workspace_id: UUID, client_id: UUID, run_id: UUID,
               expected_version: int) -> dict[str, Any]:
    return transition_run(
        user, workspace_id, client_id, run_id,
        GstrMergeStatus.EXPIRED.value, expected_version,
    )
