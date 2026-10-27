from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from .base import TimestampedModel


class Client(TimestampedModel, table=True):
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="status"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'", name="pan_format"),
        Index("ix_clients_workspace_id_status", "workspace_id", "status"),
        Index("ix_clients_created_by", "created_by"),
    )
    workspace_id: UUID = Field(foreign_key="workspaces.id", ondelete="RESTRICT")
    name: str = Field(max_length=200)
    legal_name: str | None = Field(default=None, max_length=255)
    pan: str | None = Field(default=None, max_length=10)
    status: str = Field(default="ACTIVE", max_length=20, sa_column_kwargs={"server_default": "ACTIVE"})
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")


class ClientGSTIN(TimestampedModel, table=True):
    __tablename__ = "client_gstins"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"),
        Index("uq_client_gstins_active_workspace_gstin", "workspace_id", "gstin", unique=True,
              postgresql_where=text("status = 'ACTIVE'")),
        UniqueConstraint("workspace_id", "client_id", "id"),
        CheckConstraint("gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$'", name="gstin_format"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="status"),
    )
    workspace_id: UUID
    client_id: UUID
    gstin: str = Field(max_length=15)
    status: str = Field(default="ACTIVE", max_length=20, sa_column_kwargs={"server_default": "ACTIVE"})


class ClientAssignment(TimestampedModel, table=True):
    __tablename__ = "client_assignments"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "client_id"], ["clients.workspace_id", "clients.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "membership_id"], ["workspace_memberships.workspace_id", "workspace_memberships.id"], ondelete="RESTRICT"),
        UniqueConstraint("workspace_id", "client_id", "membership_id"),
        Index("ix_client_assignments_workspace_id_membership_id", "workspace_id", "membership_id"),
        Index("ix_client_assignments_assigned_by", "assigned_by"),
    )
    workspace_id: UUID
    client_id: UUID
    membership_id: UUID
    assigned_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
