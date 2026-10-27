"""Login state is separate from temporary reconciliation sessions."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, UniqueConstraint
from sqlmodel import Field

from .base import CreatedModel, TimestampedModel


class PasswordCredential(TimestampedModel, table=True):
    __tablename__ = "password_credentials"
    __table_args__ = (UniqueConstraint("user_id"),)
    user_id: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    password_hash: str = Field(max_length=512)
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})


class AuthSession(CreatedModel, table=True):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_digest"),
        CheckConstraint("expires_at > created_at AND idle_expires_at <= expires_at", name="expiry"),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )
    user_id: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    token_hash: str = Field(max_length=64)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    idle_expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    last_seen_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AuthActionToken(CreatedModel, table=True):
    __tablename__ = "auth_action_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_digest"),
        CheckConstraint("purpose IN ('VERIFY_EMAIL', 'RESET_PASSWORD', 'ACTIVATE_ACCOUNT')", name="purpose"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        Index("ix_auth_action_tokens_user_id_purpose", "user_id", "purpose"),
        Index("ix_auth_action_tokens_expires_at", "expires_at"),
    )
    user_id: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    purpose: str = Field(max_length=30)
    email_normalized: str = Field(max_length=320)
    token_hash: str = Field(max_length=64)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    consumed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AuthSecurityEvent(CreatedModel, table=True):
    __tablename__ = "auth_security_events"
    __table_args__ = (
        CheckConstraint("action IN ('REGISTER', 'LOGIN', 'LOGOUT', 'LOGOUT_ALL', 'VERIFY_EMAIL', 'RESET_PASSWORD', 'REQUEST_EMAIL')", name="action"),
        CheckConstraint("outcome IN ('SUCCESS', 'DENIED')", name="outcome"),
        Index("ix_auth_security_events_user_id_created_at", "user_id", "created_at"),
    )
    user_id: UUID | None = Field(default=None, foreign_key="users.id", ondelete="RESTRICT")
    action: str = Field(max_length=30)
    outcome: str = Field(max_length=20)
