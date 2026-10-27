"""create workspace database foundation

Revision ID: 20260831_01
Revises: none
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260831_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL: do not import live application model metadata here.
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('email_normalized', sa.String(length=320), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name=op.f('ck_users_status')),
    sa.CheckConstraint('email_normalized = lower(btrim(email)) AND length(email_normalized) > 0', name=op.f('ck_users_email_normalized')),
    sa.CheckConstraint('length(btrim(name)) > 0', name=op.f('ck_users_name_not_blank')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email_normalized', name=op.f('uq_users_email_normalized'))
    )
    op.create_table('auth_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('issuer', sa.String(length=512), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.CheckConstraint('length(btrim(issuer)) > 0 AND length(btrim(subject)) > 0', name=op.f('ck_auth_identities_identity_not_blank')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_identities_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_identities')),
    sa.UniqueConstraint('issuer', 'subject', name=op.f('uq_auth_identities_issuer_subject'))
    )
    op.create_index('ix_auth_identities_user_id', 'auth_identities', ['user_id'], unique=False)
    op.create_table('workspaces',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('legal_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name=op.f('ck_workspaces_status')),
    sa.CheckConstraint("type IN ('INDIVIDUAL', 'FIRM')", name=op.f('ck_workspaces_type')),
    sa.CheckConstraint('length(btrim(name)) > 0', name=op.f('ck_workspaces_name_not_blank')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_workspaces_created_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspaces'))
    )
    op.create_index('ix_workspaces_created_by', 'workspaces', ['created_by'], unique=False)
    op.create_table('clients',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('legal_name', sa.String(length=255), nullable=True),
    sa.Column('pan', sa.String(length=10), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.CheckConstraint("pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]$'", name=op.f('ck_clients_pan_format')),
    sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name=op.f('ck_clients_status')),
    sa.CheckConstraint('length(btrim(name)) > 0', name=op.f('ck_clients_name_not_blank')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_clients_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_clients_workspace_id_workspaces'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clients')),
    sa.UniqueConstraint('workspace_id', 'id', name=op.f('uq_clients_workspace_id_id'))
    )
    op.create_index('ix_clients_created_by', 'clients', ['created_by'], unique=False)
    op.create_index('ix_clients_workspace_id_status', 'clients', ['workspace_id', 'status'], unique=False)
    op.create_table('workspace_invites',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('email_normalized', sa.String(length=320), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='PENDING', nullable=False),
    sa.Column('invited_by', sa.Uuid(), nullable=False),
    sa.Column('accepted_by', sa.Uuid(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'ACCEPTED') = (accepted_at IS NOT NULL AND accepted_by IS NOT NULL)", name=op.f('ck_workspace_invites_accepted_state')),
    sa.CheckConstraint("(status = 'REVOKED') = (revoked_at IS NOT NULL)", name=op.f('ck_workspace_invites_revoked_state')),
    sa.CheckConstraint("role IN ('ADMIN', 'ACCOUNTANT')", name=op.f('ck_workspace_invites_role')),
    sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'REVOKED', 'EXPIRED')", name=op.f('ck_workspace_invites_status')),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_workspace_invites_token_digest')),
    sa.CheckConstraint('(accepted_at IS NULL) = (accepted_by IS NULL)', name=op.f('ck_workspace_invites_accepted_actor')),
    sa.CheckConstraint('email_normalized = lower(btrim(email_normalized)) AND length(email_normalized) > 0', name=op.f('ck_workspace_invites_email_normalized')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_workspace_invites_expiry')),
    sa.ForeignKeyConstraint(['accepted_by'], ['users.id'], name=op.f('fk_workspace_invites_accepted_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], name=op.f('fk_workspace_invites_invited_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_workspace_invites_workspace_id_workspaces'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspace_invites')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_workspace_invites_token_hash'))
    )
    op.create_index('ix_workspace_invites_accepted_by', 'workspace_invites', ['accepted_by'], unique=False)
    op.create_index('ix_workspace_invites_invited_by', 'workspace_invites', ['invited_by'], unique=False)
    op.create_index('uq_workspace_invites_pending_email', 'workspace_invites', ['workspace_id', 'email_normalized'], unique=True, postgresql_where=sa.text("status = 'PENDING'"))
    op.create_table('workspace_memberships',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('ADMIN', 'ACCOUNTANT')", name=op.f('ck_workspace_memberships_role')),
    sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'REMOVED')", name=op.f('ck_workspace_memberships_status')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_workspace_memberships_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_workspace_memberships_workspace_id_workspaces'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspace_memberships')),
    sa.UniqueConstraint('workspace_id', 'id', name=op.f('uq_workspace_memberships_workspace_id_id')),
    sa.UniqueConstraint('workspace_id', 'user_id', name=op.f('uq_workspace_memberships_workspace_id_user_id'))
    )
    op.create_index('ix_workspace_memberships_user_id_status', 'workspace_memberships', ['user_id', 'status'], unique=False)
    op.create_table('client_assignments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('membership_id', sa.Uuid(), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_client_assignments_assigned_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id', 'client_id'], ['clients.workspace_id', 'clients.id'], name=op.f('fk_client_assignments_workspace_id_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id', 'membership_id'], ['workspace_memberships.workspace_id', 'workspace_memberships.id'], name=op.f('fk_client_assignments_workspace_id_membership_id_workspace_memberships'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_client_assignments')),
    sa.UniqueConstraint('workspace_id', 'client_id', 'membership_id', name=op.f('uq_client_assignments_workspace_id_client_id_membership_id'))
    )
    op.create_index('ix_client_assignments_assigned_by', 'client_assignments', ['assigned_by'], unique=False)
    op.create_index('ix_client_assignments_workspace_id_membership_id', 'client_assignments', ['workspace_id', 'membership_id'], unique=False)
    op.create_table('client_gstins',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('gstin', sa.String(length=15), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.CheckConstraint("gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$'", name=op.f('ck_client_gstins_gstin_format')),
    sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name=op.f('ck_client_gstins_status')),
    sa.ForeignKeyConstraint(['workspace_id', 'client_id'], ['clients.workspace_id', 'clients.id'], name=op.f('fk_client_gstins_workspace_id_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_client_gstins')),
    sa.UniqueConstraint('workspace_id', 'client_id', 'id', name=op.f('uq_client_gstins_workspace_id_client_id_id')),
    sa.UniqueConstraint('workspace_id', 'gstin', name=op.f('uq_client_gstins_workspace_id_gstin'))
    )
    op.create_table('reconciliation_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('client_gstin_id', sa.Uuid(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('redis_session_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='DRAFT', nullable=False),
    sa.Column('engine_version', sa.String(length=100), nullable=True),
    sa.Column('input_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('result_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('summary_json', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('session_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('artifacts_available', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("status <> 'COMPLETED' OR (completed_at IS NOT NULL AND result_version > 0)", name=op.f('ck_reconciliation_runs_completed_state')),
    sa.CheckConstraint("status <> 'FAILED' OR failed_at IS NOT NULL", name=op.f('ck_reconciliation_runs_failed_state')),
    sa.CheckConstraint("status IN ('DRAFT', 'READY', 'PROCESSING', 'COMPLETED', 'FAILED')", name=op.f('ck_reconciliation_runs_status')),
    sa.CheckConstraint("summary_json IS NULL OR (jsonb_typeof(summary_json) = 'object' AND octet_length(summary_json::text) <= 65536)", name=op.f('ck_reconciliation_runs_summary_bounded_object')),
    sa.CheckConstraint('input_version >= 1 AND result_version >= 0', name=op.f('ck_reconciliation_runs_versions')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_reconciliation_runs_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id', 'client_id', 'client_gstin_id'], ['client_gstins.workspace_id', 'client_gstins.client_id', 'client_gstins.id'], name=op.f('fk_reconciliation_runs_workspace_id_client_id_client_gstin_id_client_gstins'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id', 'client_id'], ['clients.workspace_id', 'clients.id'], name=op.f('fk_reconciliation_runs_workspace_id_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reconciliation_runs')),
    sa.UniqueConstraint('redis_session_id', name=op.f('uq_reconciliation_runs_redis_session_id')),
    sa.UniqueConstraint('workspace_id', 'client_id', 'id', name=op.f('uq_reconciliation_runs_workspace_id_client_id_id'))
    )
    op.create_index('ix_reconciliation_runs_created_by', 'reconciliation_runs', ['created_by'], unique=False)
    op.create_index('ix_reconciliation_runs_workspace_client_created', 'reconciliation_runs', ['workspace_id', 'client_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_reconciliation_runs_workspace_client_gstin', 'reconciliation_runs', ['workspace_id', 'client_id', 'client_gstin_id'], unique=False)
    op.create_table('audit_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('client_id', sa.Uuid(), nullable=True),
    sa.Column('reconciliation_run_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity_type', sa.String(length=100), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=True),
    sa.Column('event_metadata', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('operation_id', sa.Uuid(), nullable=True),
    sa.CheckConstraint("event_metadata IS NULL OR (jsonb_typeof(event_metadata) = 'object' AND octet_length(event_metadata::text) <= 16384)", name=op.f('ck_audit_events_metadata_bounded_object')),
    sa.CheckConstraint('length(btrim(action)) > 0 AND length(btrim(entity_type)) > 0', name=op.f('ck_audit_events_action_entity_not_blank')),
    sa.CheckConstraint('reconciliation_run_id IS NULL OR client_id IS NOT NULL', name=op.f('ck_audit_events_run_requires_client')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_audit_events_actor_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id', 'client_id', 'reconciliation_run_id'], ['reconciliation_runs.workspace_id', 'reconciliation_runs.client_id', 'reconciliation_runs.id'], name=op.f('fk_audit_events_workspace_id_client_id_reconciliation_run_id_reconciliation_runs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id', 'client_id'], ['clients.workspace_id', 'clients.id'], name=op.f('fk_audit_events_workspace_id_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_audit_events_workspace_id_workspaces'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events')),
    sa.UniqueConstraint('workspace_id', 'operation_id', 'action', name=op.f('uq_audit_events_workspace_id_operation_id_action'))
    )
    op.create_index('ix_audit_events_actor_user_id', 'audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_audit_events_workspace_client_run', 'audit_events', ['workspace_id', 'client_id', 'reconciliation_run_id'], unique=False)
    op.create_index('ix_audit_events_workspace_created', 'audit_events', ['workspace_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_audit_events_workspace_run_created', 'audit_events', ['workspace_id', 'reconciliation_run_id', 'created_at'], unique=False)
    op.execute("""
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = '42501';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Destructive: only for disposable databases or an explicitly reviewed rollback.
    op.drop_index('ix_audit_events_workspace_run_created', table_name='audit_events')
    op.drop_index('ix_audit_events_workspace_created', table_name='audit_events')
    op.drop_index('ix_audit_events_workspace_client_run', table_name='audit_events')
    op.drop_index('ix_audit_events_actor_user_id', table_name='audit_events')
    op.drop_table('audit_events')
    op.execute("DROP FUNCTION reject_audit_mutation()")
    op.drop_index('ix_reconciliation_runs_workspace_client_gstin', table_name='reconciliation_runs')
    op.drop_index('ix_reconciliation_runs_workspace_client_created', table_name='reconciliation_runs')
    op.drop_index('ix_reconciliation_runs_created_by', table_name='reconciliation_runs')
    op.drop_table('reconciliation_runs')
    op.drop_table('client_gstins')
    op.drop_index('ix_client_assignments_workspace_id_membership_id', table_name='client_assignments')
    op.drop_index('ix_client_assignments_assigned_by', table_name='client_assignments')
    op.drop_table('client_assignments')
    op.drop_index('ix_workspace_memberships_user_id_status', table_name='workspace_memberships')
    op.drop_table('workspace_memberships')
    op.drop_index('uq_workspace_invites_pending_email', table_name='workspace_invites', postgresql_where=sa.text("status = 'PENDING'"))
    op.drop_index('ix_workspace_invites_invited_by', table_name='workspace_invites')
    op.drop_index('ix_workspace_invites_accepted_by', table_name='workspace_invites')
    op.drop_table('workspace_invites')
    op.drop_index('ix_clients_workspace_id_status', table_name='clients')
    op.drop_index('ix_clients_created_by', table_name='clients')
    op.drop_table('clients')
    op.drop_index('ix_workspaces_created_by', table_name='workspaces')
    op.drop_table('workspaces')
    op.drop_index('ix_auth_identities_user_id', table_name='auth_identities')
    op.drop_table('auth_identities')
    op.drop_table('users')
