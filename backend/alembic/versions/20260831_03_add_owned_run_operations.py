"""Owned session publication and recoverable operation journal.

Revision ID: 20260831_03
Revises: 20260831_02
"""
from alembic import op
import sqlalchemy as sa

revision = '20260831_03'
down_revision = '20260831_02'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('reconciliation_runs', sa.Column('storage_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('reconciliation_runs', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('run_operations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('client_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('actor_user_id', sa.Uuid(), nullable=False),
        sa.Column('operation_key', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('expected_version', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(20), server_default='PENDING', nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_run_operations')),
        sa.ForeignKeyConstraint(['workspace_id', 'client_id', 'run_id'],
            ['reconciliation_runs.workspace_id', 'reconciliation_runs.client_id', 'reconciliation_runs.id'],
            name=op.f('fk_run_operations_workspace_id_client_id_run_id_reconciliation_runs'), ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_run_operations_actor_user_id_users'), ondelete='RESTRICT'),
        sa.UniqueConstraint('workspace_id', 'operation_key', name=op.f('uq_run_operations_workspace_id_operation_key')),
        sa.CheckConstraint("kind IN ('INSPECT', 'RECONCILE', 'MANUAL_MATCH', 'DELETE')", name=op.f('ck_run_operations_kind')),
        sa.CheckConstraint("state IN ('PENDING', 'FINALIZED', 'FAILED', 'UNRESOLVED')", name=op.f('ck_run_operations_state')),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name=op.f('ck_run_operations_fingerprint')),
        sa.CheckConstraint('expected_version >= 0', name=op.f('ck_run_operations_version')),
    )
    op.create_index('uq_run_operations_unfinished', 'run_operations', ['run_id'], unique=True,
        postgresql_where=sa.text("state IN ('PENDING', 'UNRESOLVED')"))


def downgrade():
    op.drop_table('run_operations')
    op.drop_column('reconciliation_runs', 'deleted_at')
    op.drop_column('reconciliation_runs', 'storage_version')
