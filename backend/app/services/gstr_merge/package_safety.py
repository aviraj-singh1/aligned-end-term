"""Framework-independent safety checks for local XLSX/OOXML packages."""

from __future__ import annotations

import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_MAX_UNCOMPRESSED_MB = 750
DEFAULT_MAX_ZIP_MEMBERS = 10_000
DEFAULT_MAX_COMPRESSION_RATIO = 250
DEFAULT_MAX_RELATIONSHIP_BYTES = 5 * 1024 * 1024


class PackageSafetyError(ValueError):
    """Raised when an XLSX package fails a deterministic safety check."""

    def __init__(self, message: str, *, limit_exceeded: bool = False):
        super().__init__(message)
        self.limit_exceeded = limit_exceeded


def _megabyte_limit(name: str, default_mb: int) -> int:
    return int(os.getenv(name, str(default_mb))) * 1024 * 1024


def validate_xlsx_package(
    path: str | os.PathLike[str],
    filename: str | None = None,
    *,
    max_uncompressed_bytes: int | None = None,
    max_members: int | None = None,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
    max_relationship_bytes: int = DEFAULT_MAX_RELATIONSHIP_BYTES,
) -> None:
    """Reject malformed, encrypted, oversized, or externally-linked XLSX files.

    The caller supplies a local path. No HTTP, storage, authentication, or
    application state is consulted. Limits default to the standalone merger's
    environment-driven production values.
    """

    local_path = os.fspath(path)
    display_name = filename or Path(local_path).name
    max_uncompressed_bytes = (
        max_uncompressed_bytes
        if max_uncompressed_bytes is not None
        else _megabyte_limit("GSTR_MAX_UNCOMPRESSED_MB", DEFAULT_MAX_UNCOMPRESSED_MB)
    )
    max_members = (
        max_members
        if max_members is not None
        else int(os.getenv("GSTR_MAX_ZIP_MEMBERS", str(DEFAULT_MAX_ZIP_MEMBERS)))
    )

    try:
        with zipfile.ZipFile(local_path) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise PackageSafetyError(f"{display_name}: not a valid .xlsx workbook.")
            if len(members) > max_members:
                raise PackageSafetyError(
                    f"{display_name}: workbook contains too many package entries.",
                    limit_exceeded=True,
                )
            total = sum(member.file_size for member in members)
            if total > max_uncompressed_bytes:
                raise PackageSafetyError(
                    f"{display_name}: expanded workbook exceeds the configured safety limit.",
                    limit_exceeded=True,
                )
            for member in members:
                if member.flag_bits & 0x1:
                    raise PackageSafetyError(
                        f"{display_name}: password-protected workbooks are not supported."
                    )
                if (
                    member.compress_size
                    and member.file_size / member.compress_size > max_compression_ratio
                ):
                    raise PackageSafetyError(
                        f"{display_name}: unsafe workbook compression ratio."
                    )
            if any(name.startswith("xl/externalLinks/") for name in names):
                raise PackageSafetyError(
                    f"{display_name}: external workbook links are not supported."
                )
            for member in members:
                if not member.filename.endswith(".rels"):
                    continue
                if member.file_size > max_relationship_bytes:
                    raise PackageSafetyError(
                        f"{display_name}: relationship metadata is unreasonably large.",
                        limit_exceeded=True,
                    )
                try:
                    root = ET.fromstring(archive.read(member))
                except ET.ParseError as exc:
                    raise PackageSafetyError(
                        f"{display_name}: malformed workbook relationships."
                    ) from exc
                if any(
                    element.attrib.get("TargetMode", "").lower() == "external"
                    for element in root.iter()
                ):
                    raise PackageSafetyError(
                        f"{display_name}: external workbook relationships are not supported."
                    )
    except PackageSafetyError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageSafetyError(
            f"{display_name}: corrupt, malformed, or password-protected .xlsx workbook."
        ) from exc


# Retain the standalone helper name for a narrow migration-compatible import,
# while making the public framework-independent name explicit.
_validate_xlsx_package = validate_xlsx_package
