from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field

from .base import TimestampedModel


class Workspace(TimestampedModel, table=True):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("type IN ('INDIVIDUAL', 'FIRM')", name="type"),
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="status"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        Index("ix_workspaces_created_by", "created_by"),
        UniqueConstraint('login_namespace'),
        CheckConstraint("login_namespace IS NULL OR login_namespace ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name='login_namespace'),
    )
    name: str = Field(max_length=200)
    type: str = Field(max_length=20)
    legal_name: str | None = Field(default=None, max_length=255)
    login_namespace: str | None = Field(default=None, max_length=63)
    created_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    status: str = Field(default="ACTIVE", max_length=20, sa_column_kwargs={"server_default": "ACTIVE"})
    reset_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, max_length=2)


class WorkspaceMembership(TimestampedModel, table=True):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint("role IN ('OWNER', 'ADMIN', 'ACCOUNTANT')", name="role"),
        CheckConstraint("role != 'OWNER' OR (status = 'ACTIVE' AND NOT activation_pending)", name='owner_active'),
        Index('uq_workspace_memberships_owner', 'workspace_id', unique=True, postgresql_where=text("role = 'OWNER'")),
        CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'REMOVED')", name="status"),
        Index("ix_workspace_memberships_user_id_status", "user_id", "status"),
        UniqueConstraint('login_id'),
        CheckConstraint("login_id IS NULL OR login_id = lower(login_id)", name='login_id_lowercase'),
    )
    workspace_id: UUID = Field(foreign_key="workspaces.id", ondelete="RESTRICT")
    user_id: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    role: str = Field(max_length=20)
    status: str = Field(default="ACTIVE", max_length=20, sa_column_kwargs={"server_default": "ACTIVE"})
    joined_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    login_id: str | None = Field(default=None, max_length=254)
    activation_pending: bool = Field(default=False, sa_column_kwargs={'server_default':'false'})


class WorkspaceInvite(TimestampedModel, table=True):
    __tablename__ = "workspace_invites"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("email_normalized = lower(btrim(email_normalized)) AND length(email_normalized) > 0", name="email_normalized"),
        CheckConstraint("role IN ('ADMIN', 'ACCOUNTANT')", name="role"),
        CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'REVOKED', 'EXPIRED')", name="status"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_digest"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        CheckConstraint("(status = 'ACCEPTED') = (accepted_at IS NOT NULL AND accepted_by IS NOT NULL)", name="accepted_state"),
        CheckConstraint("(accepted_at IS NULL) = (accepted_by IS NULL)", name="accepted_actor"),
        CheckConstraint("(status = 'REVOKED') = (revoked_at IS NOT NULL)", name="revoked_state"),
        Index("uq_workspace_invites_pending_email", "workspace_id", "email_normalized", unique=True, postgresql_where=text("status = 'PENDING'")),
        Index("ix_workspace_invites_invited_by", "invited_by"),
        Index("ix_workspace_invites_accepted_by", "accepted_by"),
    )
    workspace_id: UUID = Field(foreign_key="workspaces.id", ondelete="RESTRICT")
    email_normalized: str = Field(max_length=320)
    role: str = Field(max_length=20)
    token_hash: str = Field(max_length=64)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    status: str = Field(default="PENDING", max_length=20, sa_column_kwargs={"server_default": "PENDING"})
    invited_by: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    accepted_by: UUID | None = Field(default=None, foreign_key="users.id", ondelete="RESTRICT")
    accepted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
