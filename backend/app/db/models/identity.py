from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field

from .base import TimestampedModel


class User(TimestampedModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email_normalized"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint(
            "email_normalized = lower(btrim(email)) AND length(email_normalized) > 0",
            name="email_normalized",
        ),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="status"),
        Index("uq_users_verified_phone", "phone_number", unique=True,
              postgresql_where=text("phone_verified_at IS NOT NULL")),
    )
    name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    email_normalized: str = Field(max_length=320)
    status: str = Field(default="ACTIVE", max_length=20, sa_column_kwargs={"server_default": "ACTIVE"})
    email_verified_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    phone_number: str | None = Field(default=None, max_length=32)
    phone_verified_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AuthIdentity(TimestampedModel, table=True):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject"),
        Index("ix_auth_identities_user_id", "user_id"),
        CheckConstraint("length(btrim(issuer)) > 0 AND length(btrim(subject)) > 0", name="identity_not_blank"),
    )
    user_id: UUID = Field(foreign_key="users.id", ondelete="RESTRICT")
    issuer: str = Field(max_length=512)
    subject: str = Field(max_length=255)
    provider: str = Field(default="EXTERNAL", max_length=40, sa_column_kwargs={"server_default": "EXTERNAL"})
