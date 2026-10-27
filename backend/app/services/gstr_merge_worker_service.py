"""Lease-owned PostgreSQL worker lifecycle for durable GSTR merge runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import threading
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import AuditEvent, ClientGSTIN, GstrMergeRun, GstrMergeStatus
from backend.app.db.revision import validate_gstr_database_revision
from backend.app.db.session import get_database_engine
from backend.app.services import object_storage
from backend.app.services.gstr_merge.package_safety import PackageSafetyError
from backend.app.services.gstr_merge.processor import (
    MergeContext,
    ProcessingValidationError,
    process_workbooks,
)
from backend.app.services.gstr_merge_lifecycle_service import _periods, _validate_manifest
from backend.app.services.gstr_merge_run_service import _metadata_object


class LeaseLostError(RuntimeError):
    """The attempt no longer owns the current PROCESSING state."""


class DeterministicWorkerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message[:500]


@dataclass(frozen=True)
class WorkerSettings:
    lease_seconds: int = 300
    renew_seconds: int = 60
    max_attempts: int = 2
    retry_backoff_seconds: int = 30
    result_retention_seconds: int = 3600
    poll_seconds: float = 2.0


@dataclass
class WorkerAttempt:
    run_id: UUID
    workspace_id: UUID
    client_id: UUID
    lease_token: UUID
    version: int
    attempt_number: int
    mode: str
    financial_year: str
    engine_version: str
    expected_gstin: str
    manifest: dict[str, Any]
    version_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass(frozen=True)
class ProcessResult:
    run_id: UUID
    status: str
    attempt_number: int


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def worker_settings() -> WorkerSettings:
    lease = _integer("GSTR_WORKER_LEASE_SECONDS", 300, 30, 3600)
    renew = _integer("GSTR_WORKER_RENEW_SECONDS", 60, 5, 1800)
    if renew >= lease:
        raise ValueError("GSTR_WORKER_RENEW_SECONDS must be less than the lease duration")
    try:
        poll = float(os.getenv("GSTR_WORKER_POLL_SECONDS", "2"))
    except ValueError:
        raise ValueError("GSTR_WORKER_POLL_SECONDS must be numeric") from None
    if not 0.1 <= poll <= 60:
        raise ValueError("GSTR_WORKER_POLL_SECONDS must be between 0.1 and 60")
    return WorkerSettings(
        lease_seconds=lease,
        renew_seconds=renew,
        max_attempts=_integer("GSTR_WORKER_MAX_ATTEMPTS", 2, 1, 5),
        retry_backoff_seconds=_integer(
            "GSTR_WORKER_RETRY_BACKOFF_SECONDS", 30, 1, 3600
        ),
        result_retention_seconds=_integer(
            "GSTR_RESULT_RETENTION_SECONDS", 3600, 300, 86400
        ),
        poll_seconds=poll,
    )


def _engine(engine: Engine | None) -> Engine:
    return engine or get_database_engine()


def _audit(run: GstrMergeRun, action: str, metadata: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        workspace_id=run.workspace_id,
        client_id=run.client_id,
        action=action,
        entity_type="gstr_merge_runs",
        entity_id=run.id,
        event_metadata=metadata,
    )


def _owned_run(
    db: Session, attempt: WorkerAttempt, *, expected_version: int | None = None
) -> GstrMergeRun:
    run = db.exec(
        select(GstrMergeRun).where(GstrMergeRun.id == attempt.run_id).with_for_update()
    ).first()
    wanted_version = attempt.version if expected_version is None else expected_version
    if (
        run is None
        or run.status != GstrMergeStatus.PROCESSING.value
        or run.lease_token != attempt.lease_token
        or run.version != wanted_version
        or run.deleted_at is not None
    ):
        raise LeaseLostError("GSTR merge processing lease is no longer current")
    return run


def claim_next_run(
    *, engine: Engine | None = None, settings: WorkerSettings | None = None,
    now: datetime | None = None,
) -> WorkerAttempt | None:
    """Claim and commit one due run using PostgreSQL SKIP LOCKED."""
    settings = settings or worker_settings()
    now = now or utcnow()
    with Session(_engine(engine), expire_on_commit=False) as db:
        run = db.exec(
            select(GstrMergeRun)
            .where(
                GstrMergeRun.status == GstrMergeStatus.QUEUED.value,
                GstrMergeRun.deleted_at.is_(None),
                or_(GstrMergeRun.expires_at.is_(None), GstrMergeRun.expires_at > now),
                or_(
                    GstrMergeRun.next_attempt_at.is_(None),
                    GstrMergeRun.next_attempt_at <= now,
                ),
            )
            .order_by(GstrMergeRun.next_attempt_at, GstrMergeRun.created_at, GstrMergeRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if run is None:
            return None
        gstin = db.get(ClientGSTIN, run.client_gstin_id)
        if gstin is None or gstin.workspace_id != run.workspace_id or gstin.client_id != run.client_id:
            raise RuntimeError("Queued GSTR merge has invalid GSTIN ownership")
        run.status = GstrMergeStatus.PROCESSING.value
        run.attempt_count += 1
        run.lease_token = uuid4()
        run.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
        run.started_at = now
        run.next_attempt_at = None
        run.error_code = None
        run.safe_error_message = None
        run.failed_at = None
        run.version += 1
        run.updated_at = now
        db.add(run)
        db.add(_audit(run, "GSTR_MERGE_PROCESSING_STARTED", {
            "mode": run.mode,
            "financial_year": run.financial_year,
            "attempt_number": run.attempt_count,
            "engine_version": run.engine_version,
        }))
        db.commit()
        return WorkerAttempt(
            run_id=run.id,
            workspace_id=run.workspace_id,
            client_id=run.client_id,
            lease_token=run.lease_token,
            version=run.version,
            attempt_number=run.attempt_count,
            mode=run.mode,
            financial_year=run.financial_year,
            engine_version=run.engine_version,
            expected_gstin=gstin.gstin,
            manifest=dict(run.input_manifest_json or {}),
        )


def renew_lease(
    attempt: WorkerAttempt, *, engine: Engine | None = None,
    settings: WorkerSettings | None = None, now: datetime | None = None,
) -> None:
    """Extend a lease without changing the lifecycle version."""
    settings = settings or worker_settings()
    now = now or utcnow()
    with attempt.version_lock:
        with Session(_engine(engine)) as db:
            run = _owned_run(db, attempt)
            run.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
            run.updated_at = now
            db.add(run)
            db.commit()


class LeaseHeartbeat:
    """A renewal-only helper; it never processes a second merge."""

    def __init__(self, attempt: WorkerAttempt, engine: Engine, settings: WorkerSettings):
        self.attempt = attempt
        self.engine = engine
        self.settings = settings
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, name="gstr-lease-renewal", daemon=True)

    def _run(self) -> None:
        while not self.stopped.wait(self.settings.renew_seconds):
            try:
                renew_lease(self.attempt, engine=self.engine, settings=self.settings)
            except LeaseLostError:
                self.lost.set()
                return
            except Exception:
                # A transient renewal failure does not itself publish state. The
                # final token/version guard decides whether the lease survived.
                continue

    def __enter__(self) -> "LeaseHeartbeat":
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.stopped.set()
        self.thread.join(timeout=max(1, self.settings.renew_seconds + 1))

    def require_current(self) -> None:
        if self.lost.is_set():
            raise LeaseLostError("GSTR merge processing lease was lost")


def _manifest_files(attempt: WorkerAttempt) -> list[dict[str, Any]]:
    shadow = GstrMergeRun(
        id=attempt.run_id,
        workspace_id=attempt.workspace_id,
        client_id=attempt.client_id,
        client_gstin_id=uuid4(),
        created_by=uuid4(),
        mode=attempt.mode,
        financial_year=attempt.financial_year,
        input_manifest_json=attempt.manifest,
    )
    try:
        return _validate_manifest(shadow)
    except HTTPException as exc:
        raise DeterministicWorkerError(
            "INVALID_INPUT_MANIFEST", "The stored input manifest is invalid."
        ) from exc


def _record_input_digests(
    attempt: WorkerAttempt, digests: dict[str, str], *, engine: Engine,
) -> None:
    with attempt.version_lock:
        with Session(engine, expire_on_commit=False) as db:
            run = _owned_run(db, attempt)
            manifest = dict(run.input_manifest_json or {})
            files = manifest.get("files")
            if not isinstance(files, list):
                raise DeterministicWorkerError(
                    "INVALID_INPUT_MANIFEST", "The stored input manifest is invalid."
                )
            changed = False
            updated = []
            for item in files:
                row = dict(item)
                period = str(row.get("period_code", ""))
                digest = digests.get(period)
                trusted = row.get("sha256")
                if digest is None:
                    raise DeterministicWorkerError(
                        "INVALID_INPUT_MANIFEST", "The stored input manifest is invalid."
                    )
                if trusted is not None and trusted != digest:
                    raise DeterministicWorkerError(
                        "INPUT_CHANGED", "An uploaded workbook changed after processing began."
                    )
                if trusted is None:
                    row["sha256"] = digest
                    changed = True
                updated.append(row)
            if changed:
                manifest["files"] = updated
                run.input_manifest_json = _metadata_object(manifest, "Input manifest")
                run.version += 1
                run.updated_at = utcnow()
                db.add(run)
                db.commit()
                attempt.version = run.version
                attempt.manifest = manifest


def _download_inputs(
    attempt: WorkerAttempt, directory: Path, *, engine: Engine,
) -> tuple[dict[str, str], dict[str, str]]:
    settings = object_storage.gstr_storage_settings()
    paths: dict[str, str] = {}
    names: dict[str, str] = {}
    digests: dict[str, str] = {}
    aggregate = 0
    for item in _manifest_files(attempt):
        period = item["period_code"]
        reference = item["object_ref"]
        stat = object_storage.private_object_stat(reference)
        if stat is None:
            raise DeterministicWorkerError(
                "INPUT_MISSING", f"The uploaded workbook for {period} is missing."
            )
        expected = item.get("actual_size_bytes")
        if not isinstance(expected, int) or expected <= 0 or stat.size_bytes != expected:
            raise DeterministicWorkerError(
                "INPUT_CHANGED", f"The uploaded workbook for {period} changed after submission."
            )
        if stat.size_bytes > settings.max_file_bytes:
            raise DeterministicWorkerError(
                "INPUT_TOO_LARGE", "An uploaded workbook exceeds the configured byte limit."
            )
        local = directory / f"input-{period}.xlsx"
        try:
            received = object_storage.download_private_object(
                reference, local, maximum_bytes=settings.max_file_bytes
            )
        except object_storage.PrivateObjectMissingError as exc:
            raise DeterministicWorkerError(
                "INPUT_MISSING", f"The uploaded workbook for {period} is missing."
            ) from exc
        except object_storage.PrivateObjectLimitError as exc:
            raise DeterministicWorkerError(
                "INPUT_TOO_LARGE", "An uploaded workbook exceeds the configured byte limit."
            ) from exc
        if received.size_bytes != stat.size_bytes:
            raise DeterministicWorkerError(
                "INPUT_CHANGED", f"The uploaded workbook for {period} changed during download."
            )
        aggregate += received.size_bytes
        if aggregate > settings.max_batch_bytes:
            raise DeterministicWorkerError(
                "INPUT_BATCH_TOO_LARGE", "The uploaded batch exceeds the configured byte limit."
            )
        trusted = item.get("sha256")
        if trusted is not None and trusted != received.sha256:
            raise DeterministicWorkerError(
                "INPUT_CHANGED", "An uploaded workbook changed after processing began."
            )
        paths[period] = str(local)
        names[period] = str(item.get("filename", f"{period}.xlsx"))
        digests[period] = received.sha256
    _record_input_digests(attempt, digests, engine=engine)
    return paths, names


def _safe_summary(raw: dict[str, Any]) -> dict[str, Any]:
    rows = raw.get("rows_merged_per_category", {})
    safe_rows = {
        str(key)[:100]: int(value)
        for key, value in rows.items()
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
    } if isinstance(rows, dict) else {}
    verification = raw.get("metadata_verification", {})
    states = {
        str(key)[:50]: str(value.get("status", ""))[:50]
        for key, value in verification.items()
        if isinstance(key, str) and isinstance(value, dict)
    } if isinstance(verification, dict) else {}
    warnings = raw.get("duplicate_warnings", [])
    return _metadata_object({
        "mode": str(raw.get("mode", ""))[:20],
        "files_processed": int(raw.get("files_processed", 0)),
        "categories_merged": list(safe_rows),
        "rows_merged_per_category": safe_rows,
        "total_rows_merged": int(raw.get("total_rows_merged", 0)),
        "duplicate_warning_count": len(warnings) if isinstance(warnings, list) else 0,
        "metadata_verification_states": states,
        "integrity_passed": raw.get("integrity_status") == "passed",
    }, "Result summary") or {}


def _processing_error(exc: Exception) -> DeterministicWorkerError:
    message = str(exc).lower()
    if "gstin" in message:
        return DeterministicWorkerError("GSTIN_MISMATCH", "Workbook GSTIN validation failed.")
    if "financial year" in message:
        return DeterministicWorkerError("FINANCIAL_YEAR_MISMATCH", "Workbook financial-year validation failed.")
    if "period" in message or "monthly" in message or "quarterly" in message:
        return DeterministicWorkerError("PERIOD_MISMATCH", "Workbook return-period validation failed.")
    if "row" in message and "limit" in message:
        return DeterministicWorkerError("ROW_LIMIT_EXCEEDED", "Workbook row limits were exceeded.")
    if "integrity" in message:
        return DeterministicWorkerError("INTEGRITY_FAILED", "Merged workbook integrity verification failed.")
    if "zip" in message or "package" in message or "encrypted" in message or "corrupt" in message:
        return DeterministicWorkerError("CORRUPT_WORKBOOK", "A workbook is corrupt or unsafe.")
    return DeterministicWorkerError("WORKBOOK_VALIDATION_FAILED", "Workbook validation failed.")


def complete_attempt(
    attempt: WorkerAttempt, *, output_reference: str, output_size: int,
    output_sha256: str, summary: dict[str, Any], engine: Engine | None = None,
    settings: WorkerSettings | None = None, now: datetime | None = None,
) -> int:
    settings = settings or worker_settings()
    now = now or utcnow()
    with Session(_engine(engine), expire_on_commit=False) as db:
        run = _owned_run(db, attempt)
        if not object_storage.gstr_reference_belongs_to_run(
            output_reference, run.workspace_id, run.client_id, run.id,
            category="outputs", engine_version=run.engine_version,
        ):
            raise ValueError("Output reference does not belong to the claimed run")
        run.output_object_ref = output_reference
        run.output_size_bytes = output_size
        run.output_sha256 = output_sha256
        run.result_summary_json = _metadata_object(summary, "Result summary")
        run.status = GstrMergeStatus.COMPLETED.value
        run.completed_at = now
        run.expires_at = now + timedelta(seconds=settings.result_retention_seconds)
        run.lease_token = None
        run.lease_expires_at = None
        run.next_attempt_at = None
        run.version += 1
        run.updated_at = now
        db.add(run)
        db.add(_audit(run, "GSTR_MERGE_COMPLETED", {
            "mode": run.mode,
            "financial_year": run.financial_year,
            "attempt_number": run.attempt_count,
            "category_count": len(summary.get("categories_merged", [])),
            "output_size_bytes": output_size,
            "engine_version": run.engine_version,
        }))
        db.commit()
        return run.version


def _finish_failure(
    attempt: WorkerAttempt, error: DeterministicWorkerError, *, engine: Engine,
    settings: WorkerSettings, retryable: bool, now: datetime | None = None,
) -> tuple[str, int]:
    now = now or utcnow()
    with Session(engine, expire_on_commit=False) as db:
        run = _owned_run(db, attempt)
        retry = retryable and run.attempt_count < settings.max_attempts
        run.error_code = error.code[:100]
        run.safe_error_message = error.safe_message
        run.lease_token = None
        run.lease_expires_at = None
        run.version += 1
        run.updated_at = now
        if retry:
            run.status = GstrMergeStatus.QUEUED.value
            run.next_attempt_at = now + timedelta(seconds=settings.retry_backoff_seconds)
            action = "GSTR_MERGE_RETRY_SCHEDULED"
        else:
            run.status = GstrMergeStatus.FAILED.value
            run.failed_at = now
            run.next_attempt_at = None
            action = "GSTR_MERGE_FAILED"
        db.add(run)
        db.add(_audit(run, action, {
            "mode": run.mode,
            "financial_year": run.financial_year,
            "attempt_number": run.attempt_count,
            "error_code": error.code[:100],
            "engine_version": run.engine_version,
        }))
        db.commit()
        return run.status, run.version


def _cleanup_terminal_inputs(
    run_id: UUID, status: str, expected_version: int, *, engine: Engine,
) -> int:
    with Session(engine, expire_on_commit=False) as db:
        run = db.exec(
            select(GstrMergeRun).where(GstrMergeRun.id == run_id).with_for_update()
        ).first()
        if run is None or run.status != status or run.version != expected_version:
            return expected_version
        try:
            files = _validate_manifest(run)
        except HTTPException:
            return expected_version
        for item in files:
            object_storage.delete_private_object(item["object_ref"])
        manifest = dict(run.input_manifest_json or {})
        cleaned = []
        for item in manifest.get("files", []):
            row = {key: value for key, value in item.items() if key != "object_ref"}
            row["upload_state"] = "DELETED"
            cleaned.append(row)
        manifest["files"] = cleaned
        run.input_manifest_json = _metadata_object(manifest, "Input manifest")
        run.version += 1
        run.updated_at = utcnow()
        db.add(run)
        db.add(_audit(run, "GSTR_MERGE_ARTIFACTS_DELETED", {
            "input_count": len(files),
            "output_count": 0,
            "prior_status": status,
            "engine_version": run.engine_version,
        }))
        db.commit()
        return run.version


def _publication_committed(
    attempt: WorkerAttempt, reference: str, digest: object_storage.PrivateObjectDigest,
    *, engine: Engine,
) -> int | None:
    try:
        with Session(engine) as db:
            run = db.get(GstrMergeRun, attempt.run_id)
            if (
                run
                and run.status == GstrMergeStatus.COMPLETED.value
                and run.output_object_ref == reference
                and run.output_size_bytes == digest.size_bytes
                and run.output_sha256 == digest.sha256
            ):
                return run.version
            return None
    except Exception:
        return None


def process_attempt(
    attempt: WorkerAttempt, *, engine: Engine | None = None,
    settings: WorkerSettings | None = None,
) -> ProcessResult:
    """Download, validate, merge, verify, upload, and lease-publish one attempt."""
    engine = _engine(engine)
    settings = settings or worker_settings()
    output_reference: str | None = None
    published = False
    terminal_version: int | None = None
    terminal_status: str | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"aligned-gstr-{attempt.run_id.hex}-{attempt.lease_token.hex}-"
        ) as raw_directory:
            directory = Path(raw_directory)
            with LeaseHeartbeat(attempt, engine, settings) as heartbeat:
                paths, names = _download_inputs(attempt, directory, engine=engine)
                heartbeat.require_current()
                output = directory / "verified-output.xlsx"
                try:
                    raw_summary = process_workbooks(
                        paths,
                        names,
                        MergeContext(
                            mode=attempt.mode.lower(),
                            expected_gstin=attempt.expected_gstin,
                            expected_financial_year=attempt.financial_year,
                            expected_periods={period: period for period in _periods(attempt.mode)},
                        ),
                        output,
                    )
                except (ProcessingValidationError, PackageSafetyError, ValueError) as exc:
                    raise _processing_error(exc) from exc
                except OSError:
                    raise
                except Exception as exc:
                    raise DeterministicWorkerError(
                        "PROCESSING_FAILED", "Workbook processing failed safely."
                    ) from exc
                heartbeat.require_current()
                summary = _safe_summary(raw_summary)
                output_reference = object_storage.gstr_output_reference(
                    attempt.workspace_id, attempt.client_id, attempt.run_id,
                    attempt.engine_version,
                )
                try:
                    digest = object_storage.upload_private_file(
                        output_reference, output, content_type=object_storage.XLSX_MEDIA_TYPE
                    )
                except Exception:
                    try:
                        object_storage.delete_private_object(output_reference)
                    except Exception:
                        pass
                    raise
                heartbeat.require_current()
                try:
                    terminal_version = complete_attempt(
                        attempt,
                        output_reference=output_reference,
                        output_size=digest.size_bytes,
                        output_sha256=digest.sha256,
                        summary=summary,
                        engine=engine,
                        settings=settings,
                    )
                    published = True
                    terminal_status = GstrMergeStatus.COMPLETED.value
                except Exception:
                    committed_version = _publication_committed(
                        attempt, output_reference, digest, engine=engine
                    )
                    if committed_version is not None:
                        published = True
                        terminal_status = GstrMergeStatus.COMPLETED.value
                        terminal_version = committed_version
                    else:
                        object_storage.delete_private_object(output_reference)
                        raise
    except LeaseLostError:
        if output_reference and not published:
            object_storage.delete_private_object(output_reference)
        return ProcessResult(attempt.run_id, "LEASE_LOST", attempt.attempt_number)
    except DeterministicWorkerError as exc:
        try:
            terminal_status, terminal_version = _finish_failure(
                attempt, exc, engine=engine, settings=settings, retryable=False
            )
        except LeaseLostError:
            return ProcessResult(attempt.run_id, "LEASE_LOST", attempt.attempt_number)
    except (object_storage.ObjectStorageUnavailableError, OSError, SQLAlchemyError):
        safe = DeterministicWorkerError(
            "INFRASTRUCTURE_UNAVAILABLE", "Merge infrastructure is temporarily unavailable."
        )
        try:
            terminal_status, terminal_version = _finish_failure(
                attempt, safe, engine=engine, settings=settings, retryable=True
            )
        except LeaseLostError:
            return ProcessResult(attempt.run_id, "LEASE_LOST", attempt.attempt_number)
    if (
        terminal_status in {GstrMergeStatus.COMPLETED.value, GstrMergeStatus.FAILED.value}
        and terminal_version is not None
    ):
        try:
            _cleanup_terminal_inputs(
                attempt.run_id, terminal_status, terminal_version, engine=engine
            )
        except object_storage.ObjectStorageUnavailableError:
            pass
    return ProcessResult(attempt.run_id, terminal_status or "LEASE_LOST", attempt.attempt_number)


def recover_expired_runs(
    *, engine: Engine | None = None, settings: WorkerSettings | None = None,
    now: datetime | None = None, limit: int = 25,
) -> int:
    """Requeue or terminally fail abandoned attempts under row locks."""
    engine = _engine(engine)
    settings = settings or worker_settings()
    now = now or utcnow()
    terminal: list[tuple[UUID, int]] = []
    with Session(engine, expire_on_commit=False) as db:
        runs = db.exec(
            select(GstrMergeRun)
            .where(
                GstrMergeRun.status == GstrMergeStatus.PROCESSING.value,
                GstrMergeRun.deleted_at.is_(None),
                GstrMergeRun.lease_expires_at <= now,
            )
            .order_by(GstrMergeRun.lease_expires_at, GstrMergeRun.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        for run in runs:
            run.lease_token = None
            run.lease_expires_at = None
            run.version += 1
            run.updated_at = now
            run.error_code = "LEASE_EXPIRED"
            run.safe_error_message = "The processing worker stopped before completing the merge."
            if run.attempt_count < settings.max_attempts:
                run.status = GstrMergeStatus.QUEUED.value
                run.next_attempt_at = now + timedelta(seconds=settings.retry_backoff_seconds)
                action = "GSTR_MERGE_RETRY_SCHEDULED"
            else:
                run.status = GstrMergeStatus.FAILED.value
                run.failed_at = now
                run.next_attempt_at = None
                terminal.append((run.id, run.version))
                action = "GSTR_MERGE_FAILED"
            db.add(run)
            db.add(_audit(run, action, {
                "mode": run.mode,
                "financial_year": run.financial_year,
                "attempt_number": run.attempt_count,
                "error_code": "LEASE_EXPIRED",
                "engine_version": run.engine_version,
            }))
        db.commit()
    for run_id, version in terminal:
        try:
            _cleanup_terminal_inputs(
                run_id, GstrMergeStatus.FAILED.value, version, engine=engine
            )
        except object_storage.ObjectStorageUnavailableError:
            pass
    return len(runs)


def run_worker_loop(stop_event: threading.Event, *, engine: Engine | None = None) -> None:
    """Run one merge at a time until graceful shutdown is requested."""
    engine = _engine(engine)
    validate_gstr_database_revision(engine)
    object_storage.validate_object_storage_configuration()
    settings = worker_settings()
    while not stop_event.is_set():
        try:
            recover_expired_runs(engine=engine, settings=settings)
        except SQLAlchemyError:
            stop_event.wait(settings.poll_seconds)
            continue
        if stop_event.is_set():
            break
        try:
            attempt = claim_next_run(engine=engine, settings=settings)
        except SQLAlchemyError:
            stop_event.wait(settings.poll_seconds)
            continue
        if attempt is None:
            stop_event.wait(settings.poll_seconds)
            continue
        try:
            process_attempt(attempt, engine=engine, settings=settings)
        except Exception:
            # No publication occurs without the final lease/version guard. If
            # infrastructure is still unavailable, expiry recovery owns the
            # next durable decision rather than terminating the worker role.
            stop_event.wait(settings.poll_seconds)
