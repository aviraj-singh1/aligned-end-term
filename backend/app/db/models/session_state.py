from datetime import datetime, timezone

from sqlalchemy import DateTime, Text, func
from sqlmodel import Field

from .base import DatabaseModel


class ReconciliationSessionState(DatabaseModel, table=True):
    __tablename__ = "reconciliation_session_state"

    session_id: str = Field(primary_key=True, max_length=36)
    payload: str = Field(sa_type=Text)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()},
    )
