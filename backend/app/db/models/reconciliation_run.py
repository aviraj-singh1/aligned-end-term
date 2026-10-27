from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import TimestampedModel


class ReconciliationRun(TimestampedModel, table=True):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "client_id", "client_gstin_id"], ["client_gstins.workspace_id", "client_gstins.client_id", "client_gstins.id"], ondelete="RESTRICT"),
        UniqueConstraint("workspace_id", "client_id", "id"),
        UniqueConstraint("workspace_id", "client_id", "client_gstin_id", "id"),
        UniqueConstraint("redis_session_id"),
        CheckConstraint("status IN ('DRAFT', 'READY', 'PROCESSING', 'COMPLETED', 'FAILED')", name="status"),
        CheckConstraint("input_version >= 1 AND result_version >= 0", name="versions"),
        CheckConstraint(
            "tally_candidate_snapshot_version IS NULL OR "
            "(tally_candidate_snapshot_version >= 1 AND tally_candidate_snapshot_version <= result_version)",
            name="tally_candidate_snapshot_version",
        ),
        CheckConstraint("status <> 'COMPLETED' OR (completed_at IS NOT NULL AND result_version > 0)", name="completed_state"),
        CheckConstraint("status <> 'FAILED' OR failed_at IS NOT NULL", name="failed_state"),
        CheckConstraint("summary_json IS NULL OR (jsonb_typeof(summary_json) = 'object' AND octet_length(summary_json::text) <= 65536)", name="summary_bounded_object"),
        Index("ix_reconciliation_runs_workspace_client_created", "workspace_id", "client_id", "created_at", "id"),
        Index("ix_reconciliation_runs_workspace_client_gstin", "workspace_id", "client_id", "client_gstin_id"),
        Index("ix_reconciliation_runs_created_by", "created_by"),
    )
    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID | None = Field(default=None)
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    redis_session_id: UUID | None = Field(default=None)
    status: str = Field(default="DRAFT", max_length=20, sa_column_kwargs={"server_default": "DRAFT"})
    engine_version: str | None = Field(default=None, max_length=100)
    input_version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    result_version: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    tally_candidate_snapshot_version: int | None = Field(default=None)
    summary_json: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    failed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    session_expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    artifacts_available: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    storage_version: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    deleted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
