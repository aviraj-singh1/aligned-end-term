"""Tenant-scoped Tally Export V1 configuration routes."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from backend.app.api.tally_export_schemas import (
    PartyMappingCreateRequest,
    PartyMappingListResponse,
    PartyMappingResponse,
    PartyMappingUpdateRequest,
    PurchaseLedgerMappingCreateRequest,
    PurchaseLedgerMappingListResponse,
    PurchaseLedgerMappingResponse,
    PurchaseLedgerMappingUpdateRequest,
    TallyProfileCreateRequest,
    TallyProfileResponse,
    TallyProfileUpdateRequest,
    TallyBulkValidationRequest,
    TallyBulkValidationResponse,
    TallyCandidateListResponse,
    TallyBatchCreateRequest,
    TallyBatchCreateResponse,
    TallyBatchDetailResponse,
    TallyBatchListResponse,
)
from backend.app.auth.dependencies import current_user
from backend.app.auth.security import validate_csrf
from backend.app.db.models import User
from backend.app.services import tally_export_configuration_service as service
from backend.app.services import tally_export_workflow_service as workflow
from backend.app.services.result_pagination import MAX_PAGE_SIZE


router = APIRouter()
mutations = APIRouter(dependencies=[Depends(validate_csrf)])


def _root(workspace_id: UUID, client_id: UUID, client_gstin_id: UUID):
    return workspace_id, client_id, client_gstin_id


def _public(model, data):
    """Build a response only from fields in the public API contract."""

    return model.model_validate({name: data[name] for name in model.model_fields})


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/runs/{session_id}/candidates",
    response_model=TallyCandidateListResponse,
)
def list_candidates(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, session_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    search: str = Query("", max_length=200),
    user: User = Depends(current_user),
):
    return workflow.list_candidates(
        user, *_root(workspace_id, client_id, client_gstin_id), session_id,
        page=page, page_size=page_size, search=search,
    )


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/runs/{session_id}/validate",
    response_model=TallyBulkValidationResponse,
)
def validate_candidates(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, session_id: UUID,
    payload: TallyBulkValidationRequest,
    user: User = Depends(current_user),
):
    return workflow.validate_candidates(
        user, *_root(workspace_id, client_id, client_gstin_id), session_id,
        **payload.model_dump(),
    )


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/runs/{session_id}/batches",
    response_model=TallyBatchCreateResponse,
    status_code=201,
)
def generate_batch(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, session_id: UUID,
    payload: TallyBatchCreateRequest,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    user: User = Depends(current_user),
):
    return workflow.generate_batch(
        user, *_root(workspace_id, client_id, client_gstin_id), session_id,
        operation_key=idempotency_key, **payload.model_dump(),
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/batches",
    response_model=TallyBatchListResponse,
)
def list_batches(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
    session_id: UUID | None = Query(None),
    user: User = Depends(current_user),
):
    return workflow.list_batches(
        user, *_root(workspace_id, client_id, client_gstin_id),
        page=page, page_size=page_size, session_id=session_id,
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/batches/{batch_id}",
    response_model=TallyBatchDetailResponse,
)
def get_batch(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, batch_id: UUID,
    user: User = Depends(current_user),
):
    return workflow.get_batch(
        user, *_root(workspace_id, client_id, client_gstin_id), batch_id,
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/batches/{batch_id}/download",
)
def download_batch(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, batch_id: UUID,
    user: User = Depends(current_user),
):
    return workflow.download_batch(
        user, *_root(workspace_id, client_id, client_gstin_id), batch_id,
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/profile",
    response_model=TallyProfileResponse,
)
def get_profile(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    user: User = Depends(current_user),
):
    return _public(
        TallyProfileResponse,
        service.get_profile(user, *_root(workspace_id, client_id, client_gstin_id)),
    )


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/profile",
    response_model=TallyProfileResponse,
    status_code=201,
)
def create_profile(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    payload: TallyProfileCreateRequest,
    user: User = Depends(current_user),
):
    return _public(
        TallyProfileResponse,
        service.create_profile(
            user, *_root(workspace_id, client_id, client_gstin_id), **payload.model_dump()
        ),
    )


@mutations.put(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/profile",
    response_model=TallyProfileResponse,
)
def update_profile(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    payload: TallyProfileUpdateRequest,
    user: User = Depends(current_user),
):
    return _public(
        TallyProfileResponse,
        service.update_profile(
            user, *_root(workspace_id, client_id, client_gstin_id), **payload.model_dump()
        ),
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/party-mappings",
    response_model=PartyMappingListResponse,
)
def list_party_mappings(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    user: User = Depends(current_user),
):
    rows = service.list_party_mappings(user, *_root(workspace_id, client_id, client_gstin_id))
    return {"items": [_public(PartyMappingResponse, row) for row in rows]}


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/party-mappings",
    response_model=PartyMappingResponse,
    status_code=201,
)
def create_party_mapping(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    payload: PartyMappingCreateRequest,
    user: User = Depends(current_user),
):
    return _public(
        PartyMappingResponse,
        service.create_party_mapping(
            user, *_root(workspace_id, client_id, client_gstin_id), **payload.model_dump()
        ),
    )


@mutations.put(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/party-mappings/{supplier_gstin}",
    response_model=PartyMappingResponse,
)
def update_party_mapping(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, supplier_gstin: str,
    payload: PartyMappingUpdateRequest,
    user: User = Depends(current_user),
):
    return _public(
        PartyMappingResponse,
        service.update_party_mapping(
            user, *_root(workspace_id, client_id, client_gstin_id),
            supplier_gstin=supplier_gstin, **payload.model_dump(),
        ),
    )


@mutations.delete(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/party-mappings/{supplier_gstin}",
    response_model=PartyMappingResponse,
)
def deactivate_party_mapping(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, supplier_gstin: str,
    expected_version: int = Query(ge=1), user: User = Depends(current_user),
):
    return _public(
        PartyMappingResponse,
        service.deactivate_party_mapping(
            user, *_root(workspace_id, client_id, client_gstin_id),
            supplier_gstin=supplier_gstin, expected_version=expected_version,
        ),
    )


@router.get(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/purchase-ledger-mappings",
    response_model=PurchaseLedgerMappingListResponse,
)
def list_purchase_ledger_mappings(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    user: User = Depends(current_user),
):
    rows = service.list_purchase_ledger_mappings(
        user, *_root(workspace_id, client_id, client_gstin_id)
    )
    return {"items": [_public(PurchaseLedgerMappingResponse, row) for row in rows]}


@mutations.post(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/purchase-ledger-mappings",
    response_model=PurchaseLedgerMappingResponse,
    status_code=201,
)
def create_purchase_ledger_mapping(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    payload: PurchaseLedgerMappingCreateRequest,
    user: User = Depends(current_user),
):
    return _public(
        PurchaseLedgerMappingResponse,
        service.create_purchase_ledger_mapping(
            user, *_root(workspace_id, client_id, client_gstin_id), **payload.model_dump()
        ),
    )


@mutations.put(
    "/workspaces/{workspace_id}/clients/{client_id}/gstins/{client_gstin_id}/tally-export/purchase-ledger-mappings/{transaction_kind}/{gst_rate}",
    response_model=PurchaseLedgerMappingResponse,
)
def update_purchase_ledger_mapping(
    workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
    transaction_kind: Literal["purchase_invoice"], gst_rate: Decimal,
    payload: PurchaseLedgerMappingUpdateRequest,
    user: User = Depends(current_user),
):
    return _public(
        PurchaseLedgerMappingResponse,
        service.update_purchase_ledger_mapping(
            user, *_root(workspace_id, client_id, client_gstin_id),
            transaction_kind=transaction_kind, gst_rate=gst_rate, **payload.model_dump(),
        ),
    )


router.include_router(mutations)
