"""Protect original workspace creators and add two-party ownership consent."""
from alembic import op
import sqlalchemy as sa

revision = '20260901_05'
down_revision = '20260901_04'
branch_labels = depends_on = None


def upgrade():
    # Stop rather than guess an owner or reactivate a disabled identity.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM workspaces w JOIN users u ON u.id=w.created_by
        LEFT JOIN workspace_memberships m ON m.workspace_id=w.id AND m.user_id=w.created_by
        WHERE m.id IS NULL OR u.status!='ACTIVE' OR m.activation_pending) THEN
        RAISE EXCEPTION 'Ownership migration requires review of missing, disabled, or pending creators';
      END IF;
    END $$""")
    op.drop_constraint(op.f('ck_workspace_memberships_role'), 'workspace_memberships', type_='check')
    op.create_check_constraint(op.f('ck_workspace_memberships_role'), 'workspace_memberships', "role IN ('OWNER', 'ADMIN', 'ACCOUNTANT')")
    op.execute("""INSERT INTO audit_events(id,workspace_id,action,entity_type,entity_id,event_metadata)
      SELECT gen_random_uuid(),w.id,'OWNER_BACKFILLED','workspace_memberships',m.id,
        jsonb_build_object('previous_role',m.role,'previous_status',m.status)
      FROM workspaces w JOIN workspace_memberships m ON m.workspace_id=w.id AND m.user_id=w.created_by""")
    op.execute("""UPDATE workspace_memberships m SET role='OWNER',status='ACTIVE',updated_at=now()
      FROM workspaces w WHERE m.workspace_id=w.id AND m.user_id=w.created_by""")
    op.create_check_constraint(op.f('ck_workspace_memberships_owner_active'), 'workspace_memberships', "role != 'OWNER' OR (status = 'ACTIVE' AND NOT activation_pending)")
    op.create_index('uq_workspace_memberships_owner', 'workspace_memberships', ['workspace_id'], unique=True, postgresql_where=sa.text("role='OWNER'"))
    op.create_table('ownership_transfers',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.text('now()'),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.text('now()'),nullable=False),
        sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('owner_user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('recipient_user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('owner_email',sa.String(320),nullable=False),
        sa.Column('recipient_email',sa.String(320),nullable=False),
        sa.Column('owner_token_hash',sa.String(64),unique=True,nullable=False),
        sa.Column('recipient_token_hash',sa.String(64),unique=True),
        sa.Column('status',sa.String(24),server_default='PENDING_OWNER',nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('owner_confirmed_at',sa.DateTime(timezone=True)),
        sa.Column('completed_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('PENDING_OWNER', 'PENDING_RECIPIENT', 'COMPLETED', 'CANCELLED', 'EXPIRED')",name=op.f('ck_ownership_transfers_status')),
        sa.CheckConstraint('owner_user_id != recipient_user_id',name=op.f('ck_ownership_transfers_different_users')),
        sa.CheckConstraint("owner_token_hash ~ '^[0-9a-f]{64}$' AND (recipient_token_hash IS NULL OR recipient_token_hash ~ '^[0-9a-f]{64}$')",name=op.f('ck_ownership_transfers_token_digest')),
        sa.CheckConstraint('expires_at > created_at',name=op.f('ck_ownership_transfers_expiry')))
    op.create_index('uq_ownership_transfers_pending','ownership_transfers',['workspace_id'],unique=True,postgresql_where=sa.text("status IN ('PENDING_OWNER', 'PENDING_RECIPIENT')"))


def downgrade():
    op.drop_table('ownership_transfers')
    op.drop_index('uq_workspace_memberships_owner',table_name='workspace_memberships')
    op.drop_constraint(op.f('ck_workspace_memberships_owner_active'),'workspace_memberships',type_='check')
    op.execute("UPDATE workspace_memberships SET role='ADMIN' WHERE role='OWNER'")
    op.drop_constraint(op.f('ck_workspace_memberships_role'),'workspace_memberships',type_='check')
    op.create_check_constraint(op.f('ck_workspace_memberships_role'),'workspace_memberships',"role IN ('ADMIN', 'ACCOUNTANT')")
