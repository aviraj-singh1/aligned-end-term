from uuid import UUID
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlmodel import Field
from .base import TimestampedModel


class RunOperation(TimestampedModel, table=True):
    __tablename__ = 'run_operations'
    __table_args__ = (
        ForeignKeyConstraint(['workspace_id', 'client_id', 'run_id'],
            ['reconciliation_runs.workspace_id', 'reconciliation_runs.client_id', 'reconciliation_runs.id'], ondelete='RESTRICT'),
        UniqueConstraint('workspace_id', 'operation_key'),
        CheckConstraint("kind IN ('INSPECT', 'RECONCILE', 'MANUAL_MATCH', 'DELETE')", name='kind'),
        CheckConstraint("state IN ('PENDING', 'FINALIZED', 'FAILED', 'UNRESOLVED')", name='state'),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name='fingerprint'),
        CheckConstraint('expected_version >= 0', name='version'),
        Index('uq_run_operations_unfinished', 'run_id', unique=True,
              postgresql_where=text("state IN ('PENDING', 'UNRESOLVED')")),
    )
    workspace_id: UUID
    client_id: UUID
    run_id: UUID
    actor_user_id: UUID = Field(foreign_key='users.id', ondelete='RESTRICT')
    operation_key: UUID
    kind: str = Field(max_length=20)
    fingerprint: str = Field(max_length=64)
    expected_version: int
    state: str = Field(default='PENDING', max_length=20, sa_column_kwargs={'server_default': 'PENDING'})
