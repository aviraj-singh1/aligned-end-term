"""Firm login namespaces and pre-created accounts awaiting password activation."""
from alembic import op
import sqlalchemy as sa

revision = '20260901_04'
down_revision = '20260831_03'
branch_labels = depends_on = None


def upgrade():
    op.add_column('workspaces', sa.Column('login_namespace', sa.String(63), nullable=True))
    op.create_unique_constraint(op.f('uq_workspaces_login_namespace'), 'workspaces', ['login_namespace'])
    op.create_check_constraint(op.f('ck_workspaces_login_namespace'), 'workspaces', "login_namespace IS NULL OR login_namespace ~ '^[a-z0-9][a-z0-9-]{0,62}$'")
    op.add_column('workspace_memberships', sa.Column('login_id', sa.String(254), nullable=True))
    op.add_column('workspace_memberships', sa.Column('activation_pending', sa.Boolean(), server_default='false', nullable=False))
    op.create_unique_constraint(op.f('uq_workspace_memberships_login_id'), 'workspace_memberships', ['login_id'])
    op.create_check_constraint(op.f('ck_workspace_memberships_login_id_lowercase'), 'workspace_memberships', 'login_id IS NULL OR login_id = lower(login_id)')
    op.drop_constraint(op.f('ck_auth_action_tokens_purpose'), 'auth_action_tokens', type_='check')
    op.create_check_constraint(op.f('ck_auth_action_tokens_purpose'), 'auth_action_tokens', "purpose IN ('VERIFY_EMAIL', 'RESET_PASSWORD', 'ACTIVATE_ACCOUNT')")
    # Stable, collision-safe labels for existing firms; never rewrite a person's contact email.
    op.execute("""UPDATE workspaces SET login_namespace =
        COALESCE(NULLIF(left(trim(both '-' from regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')), 45), ''), 'firm')
        || '-' || left(replace(id::text, '-', ''), 12) WHERE type = 'FIRM'""")
    op.execute("""UPDATE workspace_memberships m SET login_id =
        (CASE WHEN m.user_id = w.created_by THEN 'admin' ELSE 'user-' || left(replace(m.id::text, '-', ''), 12) END)
        || '@' || w.login_namespace || '.aligned.com'
        FROM workspaces w WHERE m.workspace_id = w.id AND w.type = 'FIRM'""")


def downgrade():
    op.execute("DELETE FROM auth_action_tokens WHERE purpose = 'ACTIVATE_ACCOUNT'")
    op.drop_constraint(op.f('ck_auth_action_tokens_purpose'), 'auth_action_tokens', type_='check')
    op.create_check_constraint(op.f('ck_auth_action_tokens_purpose'), 'auth_action_tokens', "purpose IN ('VERIFY_EMAIL', 'RESET_PASSWORD')")
    op.drop_constraint(op.f('ck_workspace_memberships_login_id_lowercase'), 'workspace_memberships', type_='check')
    op.drop_constraint(op.f('uq_workspace_memberships_login_id'), 'workspace_memberships', type_='unique')
    op.drop_column('workspace_memberships', 'activation_pending')
    op.drop_column('workspace_memberships', 'login_id')
    op.drop_constraint(op.f('ck_workspaces_login_namespace'), 'workspaces', type_='check')
    op.drop_constraint(op.f('uq_workspaces_login_namespace'), 'workspaces', type_='unique')
    op.drop_column('workspaces', 'login_namespace')
