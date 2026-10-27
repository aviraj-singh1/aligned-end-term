from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import TimestampedModel


class GstrMergeMode(str, Enum):
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"


class GstrMergeStatus(str, Enum):
    UPLOADING = "UPLOADING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"


class GstrMergeRun(TimestampedModel, table=True):
    """Tenant-owned metadata for one strict GSTR consolidation lifecycle."""

    __tablename__ = "gstr_merge_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "client_gstin_id"],
            ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "client_id", "id"),
        CheckConstraint("mode IN ('QUARTERLY', 'MONTHLY')", name="mode"),
        CheckConstraint(
            "status IN ('UPLOADING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'EXPIRED', 'DELETED')",
            name="status",
        ),
        CheckConstraint(
            "financial_year ~ '^20[0-9]{2}-[0-9]{2}$' "
            "AND ((substring(financial_year, 1, 4)::integer + 1) % 100) "
            "= substring(financial_year, 6, 2)::integer",
            name="financial_year",
        ),
        CheckConstraint("version >= 1 AND attempt_count >= 0", name="versions"),
        CheckConstraint(
            "input_manifest_json IS NULL OR "
            "(jsonb_typeof(input_manifest_json) = 'object' AND octet_length(input_manifest_json::text) <= 65536)",
            name="input_manifest_bounded_object",
        ),
        CheckConstraint(
            "result_summary_json IS NULL OR "
            "(jsonb_typeof(result_summary_json) = 'object' AND octet_length(result_summary_json::text) <= 65536)",
            name="result_summary_bounded_object",
        ),
        CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name="output_sha256",
        ),
        CheckConstraint(
            "(output_object_ref IS NULL AND output_size_bytes IS NULL AND output_sha256 IS NULL) OR "
            "(output_object_ref IS NOT NULL AND length(btrim(output_object_ref)) > 0 "
            "AND output_size_bytes IS NOT NULL AND output_size_bytes >= 0 AND output_sha256 IS NOT NULL)",
            name="output_metadata",
        ),
        CheckConstraint("length(btrim(engine_version)) > 0", name="engine_version"),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="lease_pair",
        ),
        CheckConstraint(
            "status <> 'PROCESSING' OR started_at IS NOT NULL",
            name="processing_state",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR completed_at IS NOT NULL",
            name="completed_state",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR "
            "(failed_at IS NOT NULL AND error_code IS NOT NULL AND length(btrim(error_code)) > 0)",
            name="failed_state",
        ),
        CheckConstraint(
            "status <> 'EXPIRED' OR expires_at IS NOT NULL",
            name="expired_state",
        ),
        CheckConstraint(
            "(status = 'DELETED') = (deleted_at IS NOT NULL)",
            name="deleted_state",
        ),
        Index(
            "ix_gstr_merge_runs_workspace_client_created",
            "workspace_id", "client_id", "created_at", "id",
        ),
        Index(
            "ix_gstr_merge_runs_queue",
            "status", "next_attempt_at", "created_at", "id",
            postgresql_where=text("status = 'QUEUED'"),
        ),
        Index("ix_gstr_merge_runs_created_by", "created_by"),
    )

    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    mode: str = Field(max_length=20)
    financial_year: str = Field(max_length=7)
    status: str = Field(
        default=GstrMergeStatus.UPLOADING.value,
        max_length=20,
        sa_column_kwargs={"server_default": GstrMergeStatus.UPLOADING.value},
    )
    input_manifest_json: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    result_summary_json: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    output_object_ref: str | None = Field(default=None, max_length=1024)
    output_size_bytes: int | None = Field(default=None, sa_type=BigInteger)
    output_sha256: str | None = Field(default=None, max_length=64)
    engine_version: str = Field(
        default="1.0.0",
        max_length=100,
        sa_column_kwargs={"server_default": "1.0.0"},
    )
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    attempt_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    lease_token: UUID | None = Field(default=None)
    lease_expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    next_attempt_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    error_code: str | None = Field(default=None, max_length=100)
    safe_error_message: str | None = Field(default=None, max_length=500)
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    failed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    deleted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
