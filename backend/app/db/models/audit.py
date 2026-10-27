from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import CreatedModel


class AuditEvent(CreatedModel, table=True):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "client_id", "reconciliation_run_id"], ["reconciliation_runs.workspace_id", "reconciliation_runs.client_id", "reconciliation_runs.id"], ondelete="RESTRICT"),
        UniqueConstraint("workspace_id", "operation_id", "action"),
        CheckConstraint("reconciliation_run_id IS NULL OR client_id IS NOT NULL", name="run_requires_client"),
        CheckConstraint("length(btrim(action)) > 0 AND length(btrim(entity_type)) > 0", name="action_entity_not_blank"),
        CheckConstraint("event_metadata IS NULL OR (jsonb_typeof(event_metadata) = 'object' AND octet_length(event_metadata::text) <= 16384)", name="metadata_bounded_object"),
        Index("ix_audit_events_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_audit_events_workspace_run_created", "workspace_id", "reconciliation_run_id", "created_at"),
        Index("ix_audit_events_workspace_client_run", "workspace_id", "client_id", "reconciliation_run_id"),
        Index("ix_audit_events_actor_user_id", "actor_user_id"),
    )
    workspace_id: UUID = Field(foreign_key="workspaces.id", ondelete="RESTRICT")
    actor_user_id: UUID | None = Field(default=None, foreign_key="users.id", ondelete="RESTRICT")
    client_id: UUID | None = Field(default=None)
    reconciliation_run_id: UUID | None = Field(default=None)
    action: str = Field(max_length=100)
    entity_type: str = Field(max_length=100)
    entity_id: UUID | None = Field(default=None)
    event_metadata: dict[str, Any] | None = Field(default=None, sa_type=JSONB(none_as_null=True))
    operation_id: UUID | None = Field(default=None)
