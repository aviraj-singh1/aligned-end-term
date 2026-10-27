from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, Index, text
from sqlmodel import Field
from .base import TimestampedModel


class OwnershipTransfer(TimestampedModel, table=True):
    __tablename__ = 'ownership_transfers'
    __table_args__ = (
        CheckConstraint("status IN ('PENDING_OWNER', 'PENDING_RECIPIENT', 'COMPLETED', 'CANCELLED', 'EXPIRED')", name='status'),
        CheckConstraint('owner_user_id != recipient_user_id', name='different_users'),
        CheckConstraint("owner_token_hash ~ '^[0-9a-f]{64}$' AND (recipient_token_hash IS NULL OR recipient_token_hash ~ '^[0-9a-f]{64}$')", name='token_digest'),
        CheckConstraint('expires_at > created_at', name='expiry'),
        Index('uq_ownership_transfers_pending', 'workspace_id', unique=True, postgresql_where=text("status IN ('PENDING_OWNER', 'PENDING_RECIPIENT')")),
    )
    workspace_id: UUID = Field(foreign_key='workspaces.id', ondelete='RESTRICT')
    owner_user_id: UUID = Field(foreign_key='users.id', ondelete='RESTRICT')
    recipient_user_id: UUID = Field(foreign_key='users.id', ondelete='RESTRICT')
    owner_email: str = Field(max_length=320)
    recipient_email: str = Field(max_length=320)
    owner_token_hash: str = Field(max_length=64, unique=True)
    recipient_token_hash: str | None = Field(default=None, max_length=64, unique=True)
    status: str = Field(default='PENDING_OWNER', max_length=24, sa_column_kwargs={'server_default':'PENDING_OWNER'})
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    owner_confirmed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
