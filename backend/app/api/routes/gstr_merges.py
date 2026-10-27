"""Authorized JSON control routes for private GSTR consolidation uploads."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.api.gstr_merge_schemas import (
    GstrMergeCreateRequest,
    GstrMergeCreateResponse,
    GstrMergeDeleteResponse,
    GstrMergeListResponse,
    GstrMergeLocalUploadResponse,
    GstrMergeRunResponse,
    GstrMergeSubmitRequest,
)
from backend.app.auth.dependencies import current_user
from backend.app.auth.security import validate_csrf
from backend.app.db.models import User
from backend.app.services import gstr_merge_lifecycle_service as service
from backend.app.services import object_storage
from backend.app.services.owned_reconciliation import operation_key, requested_version


router = APIRouter()
mutations = APIRouter(dependencies=[Depends(validate_csrf)])


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges",
    status_code=201,
    response_model=GstrMergeCreateResponse,
)
def create_gstr_merge(
    workspace_id: UUID,
    client_id: UUID,
    payload: GstrMergeCreateRequest,
    request: Request,
    user: User = Depends(current_user),
):
    return service.create_upload_run(
        user,
        workspace_id,
        client_id,
        payload.client_gstin_id,
        payload.mode,
        payload.financial_year,
        payload.files,
        operation_key(request),
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges",
    response_model=GstrMergeListResponse,
)
def list_gstr_merges(
    workspace_id: UUID,
    client_id: UUID,
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(current_user),
):
    return service.list_upload_runs(user, workspace_id, client_id, limit=limit)


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges/{run_id}",
    response_model=GstrMergeRunResponse,
)
def get_gstr_merge(
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    user: User = Depends(current_user),
):
    return service.get_upload_run(user, workspace_id, client_id, run_id)


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges/{run_id}/submit",
    response_model=GstrMergeRunResponse,
)
def submit_gstr_merge(
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    payload: GstrMergeSubmitRequest,
    request: Request,
    user: User = Depends(current_user),
):
    del payload
    return service.submit_upload_run(
        user,
        workspace_id,
        client_id,
        run_id,
        requested_version(request),
        operation_key(request),
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges/{run_id}/download"
)
def download_gstr_merge(
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    user: User = Depends(current_user),
):
    return service.download_upload_result(user, workspace_id, client_id, run_id)


@mutations.delete(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges/{run_id}",
    response_model=GstrMergeDeleteResponse,
)
def delete_gstr_merge(
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    request: Request,
    user: User = Depends(current_user),
):
    return service.delete_upload_run(
        user, workspace_id, client_id, run_id, requested_version(request)
    )


@mutations.put(
    "/workspaces/{workspace_id}/clients/{client_id}/gstr-merges/{run_id}/uploads/{period_code}",
    response_model=GstrMergeLocalUploadResponse,
    include_in_schema=False,
)
async def local_development_upload(
    workspace_id: UUID,
    client_id: UUID,
    run_id: UUID,
    period_code: str,
    request: Request,
    user: User = Depends(current_user),
):
    item = service.authorize_local_upload(
        user, workspace_id, client_id, run_id, period_code
    )
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != object_storage.XLSX_MEDIA_TYPE:
        raise HTTPException(415, "Local uploads must use the .xlsx content type")
    try:
        settings = object_storage.gstr_storage_settings()
        length = int(request.headers.get("content-length", ""))
    except object_storage.ObjectStorageConfigurationError:
        raise HTTPException(503, "Local private upload storage is unavailable") from None
    except ValueError:
        raise HTTPException(400, "Local uploads require a valid Content-Length header") from None
    if length <= 0:
        raise HTTPException(400, "Uploaded workbook must not be empty")
    if length > settings.max_file_bytes:
        raise HTTPException(413, "Upload exceeds the configured per-file size limit")
    try:
        size = await object_storage.write_local_private_upload(
            item["object_ref"], request.stream(), maximum_bytes=settings.max_file_bytes
        )
    except object_storage.ObjectStorageUploadError as exc:
        raise HTTPException(413 if exc.limit_exceeded else 400, str(exc)) from None
    except (object_storage.ObjectStorageConfigurationError, object_storage.ObjectStorageUnavailableError):
        raise HTTPException(503, "Local private upload storage is unavailable") from None
    return {"period_code": item["period_code"], "size_bytes": size}


router.include_router(mutations)
