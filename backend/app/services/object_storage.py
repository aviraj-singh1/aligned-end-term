"""Durable private artifact storage for uploads and generated reports.

Excel readers and exporters require short-lived seekable files, so processing
uses an isolated OS scratch directory. When S3 is required, only opaque S3
references enter shared state and those scratch files are removed immediately
after inspection/export (and on failed session cleanup).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shutil
from typing import AsyncIterator, Iterator
from urllib.parse import quote
from uuid import uuid4

from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask


class ObjectStorageConfigurationError(RuntimeError):
    pass


class ObjectStorageUnavailableError(RuntimeError):
    """A sanitized storage failure safe to translate to HTTP 503."""


class ObjectStorageUploadError(ValueError):
    """A local development upload violated an advertised upload constraint."""

    def __init__(self, message: str, *, limit_exceeded: bool = False):
        super().__init__(message)
        self.limit_exceeded = limit_exceeded


class PrivateObjectMissingError(FileNotFoundError):
    """An exact private reference disappeared between HEAD and transfer."""


class PrivateObjectLimitError(ValueError):
    """The bytes received from storage exceed the worker's trusted limit."""


@dataclass(frozen=True)
class S3Settings:
    bucket: str
    region: str
    prefix: str
    endpoint_url: str | None
    encryption: str | None


@dataclass(frozen=True)
class ArtifactDeliverySettings:
    provider: str
    url_ttl_seconds: int
    cloudfront_base_url: str | None
    cloudfront_key_pair_id: str | None
    cloudfront_private_key_path: str | None


@dataclass(frozen=True)
class GstrStorageSettings:
    max_file_bytes: int
    max_batch_bytes: int
    upload_ttl_seconds: int


@dataclass(frozen=True)
class PrivateObjectStat:
    size_bytes: int
    content_type: str | None
    metadata: dict[str, str]


@dataclass(frozen=True)
class PrivateObjectDigest:
    size_bytes: int
    sha256: str


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ObjectStorageConfigurationError(f"{name} must be an integer") from None
    if value < minimum or value > maximum:
        raise ObjectStorageConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


@lru_cache(maxsize=1)
def gstr_storage_settings() -> GstrStorageSettings:
    """Limits shared by direct upload validation and the standalone merger core."""
    max_file_mb = _bounded_integer("GSTR_MAX_FILE_MB", 25, 1, 1024)
    max_batch_mb = _bounded_integer("GSTR_MAX_TOTAL_MB", 300, 1, 4096)
    if max_batch_mb < max_file_mb:
        raise ObjectStorageConfigurationError(
            "GSTR_MAX_TOTAL_MB must be at least GSTR_MAX_FILE_MB"
        )
    return GstrStorageSettings(
        max_file_bytes=max_file_mb * 1024 * 1024,
        max_batch_bytes=max_batch_mb * 1024 * 1024,
        upload_ttl_seconds=_bounded_integer(
            "GSTR_UPLOAD_URL_TTL_SECONDS", 900, 60, 3600
        ),
    )


def storage_backend() -> str:
    value = os.getenv("OBJECT_STORAGE_BACKEND", "local").strip().lower()
    if value not in {"local", "s3"}:
        raise ObjectStorageConfigurationError("OBJECT_STORAGE_BACKEND must be local or s3")
    require_s3 = os.getenv("REQUIRE_S3_OBJECT_STORAGE", "false").strip().lower() == "true"
    if require_s3 and value != "s3":
        raise ObjectStorageConfigurationError(
            "REQUIRE_S3_OBJECT_STORAGE=true requires OBJECT_STORAGE_BACKEND=s3"
        )
    return value


@lru_cache(maxsize=1)
def s3_settings() -> S3Settings:
    bucket = os.getenv("S3_BUCKET_NAME", "").strip()
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "")).strip()
    if not bucket:
        raise ObjectStorageConfigurationError("S3_BUCKET_NAME is required when OBJECT_STORAGE_BACKEND=s3")
    if not region:
        raise ObjectStorageConfigurationError("AWS_REGION is required when OBJECT_STORAGE_BACKEND=s3")
    prefix = os.getenv("S3_PREFIX", "aligned").strip().strip("/")
    if not prefix:
        raise ObjectStorageConfigurationError("S3_PREFIX cannot be empty")
    encryption = os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256").strip() or None
    return S3Settings(
        bucket=bucket,
        region=region,
        prefix=prefix,
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "").strip() or None,
        encryption=encryption,
    )


@lru_cache(maxsize=1)
def s3_client():
    settings = s3_settings()
    try:
        import boto3
    except ImportError as exc:
        raise ObjectStorageConfigurationError("boto3 is required when OBJECT_STORAGE_BACKEND=s3") from exc
    # boto3 reads AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (or an attached IAM
    # role) through its normal credential chain; credentials never enter URLs.
    return boto3.client("s3", region_name=settings.region, endpoint_url=settings.endpoint_url)


@lru_cache(maxsize=1)
def artifact_delivery_settings() -> ArtifactDeliverySettings:
    provider = os.getenv("ARTIFACT_DELIVERY_PROVIDER", "backend").strip().lower()
    if provider not in {"backend", "s3", "cloudfront"}:
        raise ObjectStorageConfigurationError(
            "ARTIFACT_DELIVERY_PROVIDER must be backend, s3, or cloudfront"
        )
    try:
        ttl = int(os.getenv("ARTIFACT_DOWNLOAD_URL_TTL_SECONDS", "300"))
    except ValueError:
        raise ObjectStorageConfigurationError(
            "ARTIFACT_DOWNLOAD_URL_TTL_SECONDS must be an integer"
        ) from None
    if ttl < 30 or ttl > 3600:
        raise ObjectStorageConfigurationError(
            "ARTIFACT_DOWNLOAD_URL_TTL_SECONDS must be between 30 and 3600"
        )
    base_url = os.getenv("CLOUDFRONT_BASE_URL", "").strip().rstrip("/") or None
    key_pair_id = os.getenv("CLOUDFRONT_KEY_PAIR_ID", "").strip() or None
    private_key_path = os.getenv("CLOUDFRONT_PRIVATE_KEY_PATH", "").strip() or None
    if provider == "cloudfront":
        if storage_backend() != "s3":
            raise ObjectStorageConfigurationError(
                "CloudFront delivery requires OBJECT_STORAGE_BACKEND=s3"
            )
        if not (base_url and base_url.startswith("https://")):
            raise ObjectStorageConfigurationError(
                "CLOUDFRONT_BASE_URL must be an HTTPS URL for CloudFront delivery"
            )
        if not key_pair_id or not private_key_path:
            raise ObjectStorageConfigurationError(
                "CLOUDFRONT_KEY_PAIR_ID and CLOUDFRONT_PRIVATE_KEY_PATH are required for CloudFront delivery"
            )
        if not Path(private_key_path).is_file():
            raise ObjectStorageConfigurationError("CLOUDFRONT_PRIVATE_KEY_PATH does not exist")
    if provider == "s3" and storage_backend() != "s3":
        raise ObjectStorageConfigurationError(
            "Direct S3 delivery requires OBJECT_STORAGE_BACKEND=s3"
        )
    return ArtifactDeliverySettings(
        provider=provider,
        url_ttl_seconds=ttl,
        cloudfront_base_url=base_url,
        cloudfront_key_pair_id=key_pair_id,
        cloudfront_private_key_path=private_key_path,
    )


def is_s3_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith("s3://")


def _reference(key: str) -> str:
    return f"s3://{s3_settings().bucket}/{key}"


def _key(reference: str) -> str:
    settings = s3_settings()
    expected = f"s3://{settings.bucket}/"
    if not reference.startswith(expected):
        raise ValueError("Artifact does not belong to the configured S3 bucket")
    key = reference[len(expected):]
    if not key or ".." in key.split("/"):
        raise ValueError("Invalid S3 artifact reference")
    return key


def _session_key(session_id: str, category: str, filename: str) -> str:
    safe_filename = Path(filename).name
    return f"{s3_settings().prefix}/sessions/{session_id}/{category}/{uuid4().hex}/{safe_filename}"


def local_storage_root() -> Path:
    """Resolve local merger objects beneath scratch; never accept an arbitrary root."""
    project_root = Path(__file__).resolve().parents[3]
    scratch_root = (project_root / "scratch").resolve()
    configured = os.getenv("GSTR_LOCAL_STORAGE_ROOT", "").strip()
    candidate = Path(configured) if configured else scratch_root / "gstr-objects"
    if not candidate.is_absolute():
        candidate = project_root / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(scratch_root):
        raise ObjectStorageConfigurationError(
            "GSTR_LOCAL_STORAGE_ROOT must be beneath the project scratch directory"
        )
    return candidate


def _gstr_relative_prefix(
    workspace_id: object, client_id: object, run_id: object
) -> str:
    return (
        f"workspaces/{workspace_id}/clients/{client_id}/"
        f"gstr-merges/{run_id}/"
    )


def _gstr_local_relative_prefix(
    workspace_id: object, client_id: object, run_id: object
) -> str:
    """Keep development paths below Windows MAX_PATH while retaining all UUIDs."""
    compact = lambda value: str(value).replace("-", "")
    return f"ws/{compact(workspace_id)}/cl/{compact(client_id)}/runs/{compact(run_id)}/"


def gstr_run_prefix(
    workspace_id: object, client_id: object, run_id: object
) -> str:
    relative = _gstr_relative_prefix(workspace_id, client_id, run_id)
    if storage_backend() == "s3":
        return _reference(f"{s3_settings().prefix}/{relative}")
    local = _gstr_local_relative_prefix(workspace_id, client_id, run_id)
    return str((local_storage_root() / Path(local)).resolve()) + os.sep


def gstr_input_reference(
    workspace_id: object, client_id: object, run_id: object, period_code: str
) -> str:
    if storage_backend() == "s3":
        relative = (
            _gstr_relative_prefix(workspace_id, client_id, run_id)
            + f"inputs/{period_code}/{uuid4().hex}.xlsx"
        )
        return _reference(f"{s3_settings().prefix}/{relative}")
    relative = (
        _gstr_local_relative_prefix(workspace_id, client_id, run_id)
        + f"in/{period_code}/{uuid4().hex}.xlsx"
    )
    return str((local_storage_root() / Path(relative)).resolve())


def gstr_output_reference(
    workspace_id: object, client_id: object, run_id: object, engine_version: str
) -> str:
    """Reserve a server-owned output reference for the future worker."""
    if storage_backend() == "s3":
        relative = (
            _gstr_relative_prefix(workspace_id, client_id, run_id)
            + f"outputs/{engine_version}/{uuid4().hex}.xlsx"
        )
        return _reference(f"{s3_settings().prefix}/{relative}")
    relative = (
        _gstr_local_relative_prefix(workspace_id, client_id, run_id)
        + f"out/{engine_version}/{uuid4().hex}.xlsx"
    )
    return str((local_storage_root() / Path(relative)).resolve())


def gstr_reference_belongs_to_run(
    reference: str,
    workspace_id: object,
    client_id: object,
    run_id: object,
    *,
    category: str,
    period_code: str | None = None,
    engine_version: str | None = None,
) -> bool:
    """Validate an exact opaque input/output object without touching storage."""
    if category not in {"inputs", "outputs"}:
        return False
    if (storage_backend() == "s3") != is_s3_reference(reference):
        return False
    if category == "inputs" and not period_code:
        return False
    if category == "outputs" and not engine_version:
        return False
    branch = (
        f"inputs/{period_code}/"
        if category == "inputs"
        else f"outputs/{engine_version}/"
    )
    relative_prefix = _gstr_relative_prefix(workspace_id, client_id, run_id) + branch
    try:
        if is_s3_reference(reference):
            key = _key(reference)
            expected = f"{s3_settings().prefix}/{relative_prefix}"
            tail = key.removeprefix(expected) if key.startswith(expected) else ""
        else:
            path = Path(reference).resolve()
            local_branch = (
                f"in/{period_code}/"
                if category == "inputs"
                else f"out/{engine_version}/"
            )
            local_prefix = (
                _gstr_local_relative_prefix(workspace_id, client_id, run_id)
                + local_branch
            )
            expected_path = (local_storage_root() / Path(local_prefix)).resolve()
            if not path.is_relative_to(expected_path):
                return False
            tail = path.relative_to(expected_path).as_posix()
    except (OSError, ValueError, ObjectStorageConfigurationError):
        return False
    return (
        bool(tail)
        and "/" not in tail
        and len(tail) == 37
        and tail.endswith(".xlsx")
        and all(character in "0123456789abcdef" for character in tail[:-5])
    )


def gstr_upload_metadata(run_id: object, period_code: str, expected_size: int) -> dict[str, str]:
    return {
        "aligned-run-id": str(run_id),
        "aligned-period": period_code,
        "aligned-expected-size": str(expected_size),
    }


def create_private_upload_target(
    reference: str,
    *,
    run_id: object,
    period_code: str,
    expected_size: int,
    local_url: str,
) -> dict[str, object]:
    """Issue an exact-key private PUT target without returning credentials."""
    settings = gstr_storage_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.upload_ttl_seconds)
    if not is_s3_reference(reference):
        if os.getenv("APP_ENV", "development").strip().lower() != "development":
            raise ObjectStorageConfigurationError(
                "Local GSTR upload targets are available only in development"
            )
        return {
            "provider": "local",
            "method": "PUT",
            "url": local_url,
            "headers": {"Content-Type": XLSX_MEDIA_TYPE},
            "expires_at": expires_at,
        }

    s3 = s3_settings()
    metadata = gstr_upload_metadata(run_id, period_code, expected_size)
    params: dict[str, object] = {
        "Bucket": s3.bucket,
        "Key": _key(reference),
        "ContentType": XLSX_MEDIA_TYPE,
        "Metadata": metadata,
    }
    headers = {
        "Content-Type": XLSX_MEDIA_TYPE,
        **{f"x-amz-meta-{key}": value for key, value in metadata.items()},
    }
    if s3.encryption:
        params["ServerSideEncryption"] = s3.encryption
        headers["x-amz-server-side-encryption"] = s3.encryption
    try:
        url = s3_client().generate_presigned_url(
            "put_object", Params=params, ExpiresIn=settings.upload_ttl_seconds
        )
    except Exception as exc:
        raise ObjectStorageUnavailableError("Private upload storage is unavailable") from exc
    return {
        "provider": "s3",
        "method": "PUT",
        "url": url,
        "headers": headers,
        "expires_at": expires_at,
    }


def private_object_stat(reference: str) -> PrivateObjectStat | None:
    """HEAD/stat one exact private object; missing is distinct from unavailable."""
    if not is_s3_reference(reference):
        try:
            path = Path(reference)
            stat = path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ObjectStorageUnavailableError("Private object storage is unavailable") from exc
        return PrivateObjectStat(stat.st_size, XLSX_MEDIA_TYPE, {})
    try:
        result = s3_client().head_object(
            Bucket=s3_settings().bucket, Key=_key(reference)
        )
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise ObjectStorageUnavailableError("Private object storage is unavailable") from exc
    return PrivateObjectStat(
        int(result["ContentLength"]),
        result.get("ContentType"),
        {str(key).lower(): str(value) for key, value in result.get("Metadata", {}).items()},
    )


def delete_private_object(reference: str) -> None:
    """Idempotently delete one already-authorized exact object reference."""
    if not is_s3_reference(reference):
        try:
            Path(reference).unlink(missing_ok=True)
        except OSError as exc:
            raise ObjectStorageUnavailableError("Private object cleanup is unavailable") from exc
        return
    try:
        s3_client().delete_object(Bucket=s3_settings().bucket, Key=_key(reference))
    except Exception as exc:
        raise ObjectStorageUnavailableError("Private object cleanup is unavailable") from exc


def _validated_local_reference(reference: str) -> Path:
    path = Path(reference).resolve()
    try:
        if not path.is_relative_to(local_storage_root()):
            raise ValueError("Private object reference is outside local storage")
    except OSError as exc:
        raise ObjectStorageUnavailableError("Private object storage is unavailable") from exc
    return path


def download_private_object(
    reference: str,
    destination: str | Path,
    *,
    maximum_bytes: int,
) -> PrivateObjectDigest:
    """Stream one exact private object to a new local file while hashing it."""
    target = Path(destination)
    digest = hashlib.sha256()
    total = 0
    body = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            if is_s3_reference(reference):
                try:
                    result = s3_client().get_object(
                        Bucket=s3_settings().bucket, Key=_key(reference)
                    )
                    body = result["Body"]
                except Exception as exc:
                    response = getattr(exc, "response", {})
                    code = str(response.get("Error", {}).get("Code", ""))
                    if code in {"404", "NoSuchKey", "NotFound"}:
                        raise PrivateObjectMissingError("Private object is missing") from exc
                    raise ObjectStorageUnavailableError(
                        "Private object download is unavailable"
                    ) from exc
                chunks = iter(lambda: body.read(1024 * 1024), b"")
            else:
                source = _validated_local_reference(reference)
                try:
                    input_handle = source.open("rb")
                except FileNotFoundError as exc:
                    raise PrivateObjectMissingError("Private object is missing") from exc
                except OSError as exc:
                    raise ObjectStorageUnavailableError(
                        "Private object download is unavailable"
                    ) from exc
                chunks = iter(lambda: input_handle.read(1024 * 1024), b"")

            try:
                for chunk in chunks:
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise PrivateObjectLimitError(
                            "Private object exceeds the configured byte limit"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            finally:
                if not is_s3_reference(reference):
                    input_handle.close()
        if total == 0:
            raise PrivateObjectLimitError("Private object is empty")
        return PrivateObjectDigest(total, digest.hexdigest())
    except (PrivateObjectMissingError, PrivateObjectLimitError):
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise ObjectStorageUnavailableError("Private object download is unavailable") from exc
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, ObjectStorageUnavailableError):
            raise
        raise ObjectStorageUnavailableError("Private object download is unavailable") from exc
    finally:
        if body is not None:
            body.close()


def upload_private_file(
    reference: str,
    local_path: str | Path,
    *,
    content_type: str,
) -> PrivateObjectDigest:
    """Stream a verified local artifact to a fresh server-owned reference."""
    source = Path(local_path)
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
    result = PrivateObjectDigest(total, digest.hexdigest())
    if is_s3_reference(reference):
        settings = s3_settings()
        arguments: dict[str, str] = {"ContentType": content_type}
        if settings.encryption:
            arguments["ServerSideEncryption"] = settings.encryption
        try:
            s3_client().upload_file(
                str(source), settings.bucket, _key(reference), ExtraArgs=arguments
            )
        except Exception as exc:
            raise ObjectStorageUnavailableError("Private object upload is unavailable") from exc
    else:
        target = _validated_local_reference(reference)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep the staging name near the short storage root. A sibling name can
        # exceed legacy Windows MAX_PATH once all tenant UUIDs are present.
        temporary = local_storage_root() / f".output-{uuid4().hex}.part"
        try:
            with source.open("rb") as input_handle, temporary.open("xb") as output:
                shutil.copyfileobj(input_handle, output, length=1024 * 1024)
            os.replace(temporary, target)
        except OSError as exc:
            raise ObjectStorageUnavailableError("Private object upload is unavailable") from exc
        finally:
            temporary.unlink(missing_ok=True)
    stat = private_object_stat(reference)
    if stat is None or stat.size_bytes != result.size_bytes:
        try:
            delete_private_object(reference)
        finally:
            raise ObjectStorageUnavailableError("Uploaded private object could not be verified")
    return result


async def write_local_private_upload(
    reference: str,
    chunks: AsyncIterator[bytes],
    *,
    maximum_bytes: int,
) -> int:
    """Development-only streamed PUT adapter; never used by the S3 backend."""
    if storage_backend() != "local":
        raise ObjectStorageConfigurationError("Local upload adapter is disabled")
    path = Path(reference).resolve()
    if not path.is_relative_to(local_storage_root()):
        raise ObjectStorageUploadError("Invalid local private object reference")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = local_storage_root() / f".upload-{uuid4().hex}.part"
    total = 0
    try:
        with temporary.open("wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum_bytes:
                    raise ObjectStorageUploadError(
                        "Upload exceeds the configured per-file size limit",
                        limit_exceeded=True,
                    )
                handle.write(chunk)
        if total == 0:
            raise ObjectStorageUploadError("Uploaded workbook must not be empty")
        os.replace(temporary, path)
        return total
    finally:
        temporary.unlink(missing_ok=True)


def persist_artifact(
    session_id: str,
    local_path: str | Path,
    *,
    category: str,
    filename: str,
    content_type: str,
) -> str:
    path = Path(local_path)
    if storage_backend() == "local":
        return str(path)
    settings = s3_settings()
    key = _session_key(session_id, category, filename)
    extra = {
        "ContentType": content_type,
        # CloudFront preserves this object metadata, while direct S3 delivery
        # also overrides it in the presigned URL for older stored artifacts.
        "ContentDisposition": _content_disposition(filename),
    }
    if settings.encryption:
        extra["ServerSideEncryption"] = settings.encryption
    s3_client().upload_file(str(path), settings.bucket, key, ExtraArgs=extra)
    return _reference(key)


def artifact_available(reference: object, *, verify_remote: bool = False) -> bool:
    if is_s3_reference(reference):
        # Avoid a remote HEAD request every time shared session state is read.
        if verify_remote:
            try:
                s3_client().head_object(Bucket=s3_settings().bucket, Key=_key(str(reference)))
            except Exception as exc:
                response = getattr(exc, "response", {})
                code = str(response.get("Error", {}).get("Code", ""))
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise
        return True
    return bool(reference) and Path(str(reference)).is_file()


def artifact_size(reference: str) -> int:
    if not is_s3_reference(reference):
        return Path(reference).stat().st_size
    result = s3_client().head_object(Bucket=s3_settings().bucket, Key=_key(reference))
    return int(result["ContentLength"])


def delete_session_artifacts(session_id: str) -> None:
    if storage_backend() != "s3":
        return
    settings = s3_settings()
    prefix = f"{settings.prefix}/sessions/{session_id}/"
    paginator = s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.bucket, Prefix=prefix):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        for offset in range(0, len(objects), 1000):
            s3_client().delete_objects(
                Bucket=settings.bucket,
                Delete={"Objects": objects[offset:offset + 1000], "Quiet": True},
            )


def _s3_chunks(reference: str) -> Iterator[bytes]:
    body = s3_client().get_object(Bucket=s3_settings().bucket, Key=_key(reference))["Body"]
    try:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            if chunk:
                yield chunk
    finally:
        body.close()


def _content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(Path(filename).name)}"


def _s3_download_url(reference: str, *, filename: str, media_type: str, ttl: int) -> str:
    return s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": s3_settings().bucket,
            "Key": _key(reference),
            "ResponseContentDisposition": _content_disposition(filename),
            "ResponseContentType": media_type,
        },
        ExpiresIn=ttl,
    )


def _cloudfront_download_url(reference: str, *, ttl: int) -> str:
    settings = artifact_delivery_settings()
    resource_url = f"{settings.cloudfront_base_url}/{quote(_key(reference), safe='/')}"

    from botocore.signers import CloudFrontSigner
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(
        Path(str(settings.cloudfront_private_key_path)).read_bytes(), password=None
    )

    def rsa_signer(message: bytes) -> bytes:
        return private_key.sign(message, padding.PKCS1v15(), hashes.SHA1())

    signer = CloudFrontSigner(str(settings.cloudfront_key_pair_id), rsa_signer)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    return signer.generate_presigned_url(resource_url, date_less_than=expires_at)


def artifact_download_url(reference: str, *, filename: str, media_type: str) -> str | None:
    """Return a short-lived direct-delivery URL, or None for backend streaming."""
    if not is_s3_reference(reference):
        return None
    settings = artifact_delivery_settings()
    if settings.provider == "backend":
        return None
    if settings.provider == "s3":
        return _s3_download_url(
            reference,
            filename=filename,
            media_type=media_type,
            ttl=settings.url_ttl_seconds,
        )
    return _cloudfront_download_url(reference, ttl=settings.url_ttl_seconds)


def artifact_response(reference: str, *, filename: str, media_type: str):
    headers = {"Cache-Control": "no-store, private"}
    if not is_s3_reference(reference):
        path = Path(reference)
        return FileResponse(
            path=path,
            filename=filename,
            media_type=media_type,
            headers=headers,
            background=BackgroundTask(path.unlink, missing_ok=True),
        )
    direct_url = artifact_download_url(reference, filename=filename, media_type=media_type)
    if direct_url:
        return RedirectResponse(direct_url, status_code=307, headers=headers)
    headers["Content-Disposition"] = _content_disposition(filename)
    headers["Content-Length"] = str(artifact_size(reference))
    return StreamingResponse(_s3_chunks(reference), media_type=media_type, headers=headers)


def validate_object_storage_configuration() -> None:
    if storage_backend() == "s3":
        s3_settings()
    else:
        local_storage_root()
    gstr_storage_settings()
    artifact_delivery_settings()
