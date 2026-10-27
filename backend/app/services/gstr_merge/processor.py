"""Deterministic, framework-independent orchestration for GSTR consolidation."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from typing import Mapping

from backend.app.services.gstr_merge.integrity_checker import IntegrityChecker
from backend.app.services.gstr_merge.package_safety import validate_xlsx_package
from backend.app.services.gstr_merge.source_manifest import (
    SourceManifestBuilder,
    validate_excel_row_limits,
)
from backend.app.services.gstr_merge.workbook_inspector import WorkbookInspector
from backend.app.services.gstr_merge.workbook_merger import WorkbookMerger
from backend.app.services.gstr_merge.workbook_validator import WorkbookValidator


class ProcessingValidationError(ValueError):
    """Raised when inputs or the independently verified output are invalid."""


@dataclass(frozen=True)
class MergeContext:
    """Caller-supplied merge intent, independent of tenant persistence.

    Expected values are optional seams for later adapters. They validate only
    detected workbook metadata and never fabricate missing metadata.
    """

    mode: str
    expected_gstin: str | None = None
    expected_financial_year: str | None = None
    expected_periods: Mapping[str, str] = field(default_factory=dict)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_spools(manifest) -> None:
    if not manifest:
        return
    for category in manifest.get("categories", {}).values():
        spool_path = category.get("spool_path")
        if spool_path and os.path.exists(spool_path):
            os.remove(spool_path)
        category["spool_path"] = None


def process_workbooks(
    paths: Mapping[str, str | os.PathLike[str]],
    filenames: Mapping[str, str] | None,
    context: MergeContext,
    output_path: str | os.PathLike[str],
) -> dict:
    """Validate, consolidate, verify, and summarize local GSTR workbooks.

    Pipeline: package safety -> inspection -> cross-file/context validation ->
    source manifest -> Excel row-limit preflight -> write-only merge ->
    independently reopened-output verification -> summary.
    """

    mode = context.mode.strip().lower()
    expected_count = 4 if mode == "quarterly" else 12 if mode == "monthly" else None
    if expected_count is None:
        raise ProcessingValidationError("Mode must be 'quarterly' or 'monthly'.")
    if len(paths) != expected_count:
        raise ProcessingValidationError(
            f"{mode.title()} mode requires exactly {expected_count} files; received {len(paths)}."
        )

    local_paths = {source_id: os.fspath(path) for source_id, path in paths.items()}
    display_names = dict(filenames or {})
    if set(display_names) - set(local_paths):
        raise ProcessingValidationError("Filenames contain unknown source identifiers.")
    display_names = {
        source_id: display_names.get(source_id, Path(path).name)
        for source_id, path in local_paths.items()
    }

    hashes: dict[str, str] = {}
    for source_id, path in local_paths.items():
        filename = display_names[source_id]
        if Path(filename).suffix.lower() != ".xlsx":
            raise ProcessingValidationError(f"{filename}: only .xlsx files are supported.")
        validate_xlsx_package(path, filename)
        file_hash = _sha256(path)
        if file_hash in hashes:
            raise ProcessingValidationError(
                f"Duplicate source files: {hashes[file_hash]} and {filename} are identical."
            )
        hashes[file_hash] = filename

    destination = os.fspath(output_path)
    manifest = None
    try:
        inspector = WorkbookInspector(local_paths, display_names)
        metadata = inspector.inspect_all()
        validator = WorkbookValidator(
            metadata,
            mode,
            expected_gstin=context.expected_gstin,
            expected_financial_year=context.expected_financial_year,
            expected_periods=dict(context.expected_periods),
        )
        validation_errors = validator.validate()
        if validation_errors:
            raise ProcessingValidationError("\n".join(validation_errors))

        manifest_builder = SourceManifestBuilder(local_paths, metadata)
        manifest = manifest_builder.build()
        row_limit_errors = validate_excel_row_limits(manifest)
        if row_limit_errors:
            raise ProcessingValidationError("\n".join(row_limit_errors))

        merger = WorkbookMerger(local_paths, metadata, destination, manifest, mode=mode)
        merger.duplicate_warnings = manifest_builder.duplicate_warnings
        verified_manifest = merger.merge()
        checker = IntegrityChecker(verified_manifest, destination, metadata)
        integrity_errors = checker.verify()
        if integrity_errors:
            raise ProcessingValidationError(
                "Integrity check failed:\n" + "\n".join(integrity_errors)
            )

        summary = checker.get_final_summary()
        summary.update(
            {
                "mode": mode,
                "files_processed": len(local_paths),
                "duplicate_warnings": merger.duplicate_warnings,
                "period_warnings": validator.period_warnings,
                "validation_warnings": validator.warnings,
                "informational_notes": validator.informational_notes,
                "metadata_verification": validator.verification,
                "gstin_validation": validator.gstin_result(),
            }
        )
        return summary
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise
    finally:
        _cleanup_spools(manifest)


# Migration-compatible name for callers moving off the standalone route.
_process_workbooks = process_workbooks
