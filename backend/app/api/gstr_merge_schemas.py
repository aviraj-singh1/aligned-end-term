"""Strict JSON control-plane schemas for private GSTR consolidation uploads."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.api.auth_schemas import StrictRequest


class GstrMergeFileInput(StrictRequest):
    period_code: str = Field(min_length=2, max_length=3)
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int

    @field_validator("period_code", mode="before")
    @classmethod
    def normalize_period(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class GstrMergeCreateRequest(StrictRequest):
    client_gstin_id: UUID
    financial_year: str = Field(min_length=1, max_length=20)
    mode: str = Field(min_length=1, max_length=20)
    files: list[GstrMergeFileInput] = Field(min_length=1, max_length=13)

    @field_validator("financial_year", "mode", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class GstrMergeSubmitRequest(StrictRequest):
    pass


class GstrMergeUploadTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_code: str
    provider: Literal["s3", "local"]
    method: Literal["PUT"]
    url: str
    headers: dict[str, str]
    expires_at: datetime


class GstrMergeInputFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_code: str
    filename: str
    expected_size_bytes: int
    actual_size_bytes: int | None = None
    upload_state: Literal["PENDING", "UPLOADED", "DELETED"]


class GstrMergeRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    mode: Literal["QUARTERLY", "MONTHLY"]
    financial_year: str
    status: Literal[
        "UPLOADING", "QUEUED", "PROCESSING", "COMPLETED", "FAILED", "EXPIRED", "DELETED"
    ]
    engine_version: str
    version: int
    attempt_count: int
    files: list[GstrMergeInputFile]
    declared_total_size_bytes: int
    uploaded_total_size_bytes: int | None
    result_summary: dict[str, Any] | None
    artifact_available: bool
    error_code: str | None
    safe_error_message: str | None
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GstrMergeCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: GstrMergeRunResponse
    upload_targets: list[GstrMergeUploadTarget]


class GstrMergeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GstrMergeRunResponse]


class GstrMergeDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    run: GstrMergeRunResponse


class GstrMergeLocalUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_code: str
    size_bytes: int
